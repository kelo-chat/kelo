import SwiftUI
import KeloCore
import KeloShared

/// What Keyguard needs from the signed-in app: the API and server address now, and the shared "locked" / "Keyguard is on" state.
@MainActor protocol KeyguardHost: AnyObject {
    var api: KeloApi? { get }
    var session: Session? { get }
    func setLocked(_ on: Bool)
    @discardableResult func refreshKeyguard() async -> Bool?
}

extension AppModel: KeyguardHost {}

/// Keyguard on this phone (Android keyguard/KeyguardUi.kt): turn it on, the phones that hold the key, unlock with this phone's copy
/// or the recovery code, remove this phone's copy, turn it off. The locked banner uses the same model for its unlock.
@MainActor @Observable final class KeyguardModel {
    private(set) var view: KeyguardView?
    private(set) var me: Int64?
    private(set) var notOwner = false
    private(set) var busy = false
    private(set) var error = ""
    private(set) var note = ""
    /// The recovery code, shown once, then forgotten.
    private(set) var recovery: String?
    /// Why the last unlock did not happen (the banner's line); nil when it did, or the owner backed out.
    private(set) var problem: KeyguardFlow.Problem?
    /// Goes up when Keyguard is on after an enrol that showed no code (the page that asked can go on).
    private(set) var enabledTick = 0
    @ObservationIgnored private unowned let host: any KeyguardHost
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(host: any KeyguardHost) { self.host = host }

    /// This phone's Keyguard key for the signed-in server (nil when signed out or the phone cannot hold one).
    var key: KeyguardKey? {
        guard let url = host.session?.serverUrl else { return nil }
        return KeyguardKeys.platform(serverUrl: url)
    }

    // ---- unlock ------------------------------------------------------------------------------------------------------------------------

    /// Unlocks Kelo with this phone's copy; nil when unlocked, else what to say (`.cancelled`: nothing).
    private func unlockNow() async -> KeyguardFlow.Problem? {
        guard let api = host.api, let key else { return .keyLost }
        do {
            try await KeyguardFlow.unlock(calls: KeyguardCalls(api: api), key: key, reason: L(R.string.keyguard_prompt_title))
            host.setLocked(false)
            Haptics.success()
            return nil
        } catch let p as KeyguardFlow.Problem {
            return p
        } catch {
            return .server(ApiError(status: 0, detail: ""))
        }
    }

    /// The banner's button, and opening from the keyguard_locked notification.
    func unlockFromBanner() {
        guard !busy else { return }
        busy = true
        problem = nil
        jobs.run { [self] in
            let p = await unlockNow()
            busy = false
            problem = p == .cancelled ? nil : p
        }
    }

    func unlock() {
        busy = true; error = ""
        jobs.run { [self] in
            let p = await unlockNow()
            busy = false
            if let p { fail(p) } else { note = L(R.string.keyguard_unlocked); await load() }
        }
    }

    // ---- the page ------------------------------------------------------------------------------------------------------------------------

    /// A newer read cancels the older one, so an old answer never covers a change just made.
    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let api = host.api else { return }
            do {
                let v = try await KeyguardCalls(api: api).view()
                guard !Task.isCancelled else { return }
                view = v
                host.setLocked(v.locked)
                me = (try? await api.devices())?.current
                _ = await host.refreshKeyguard()
            } catch let e as ApiError {
                if e.status == 403 { notOwner = true } else { error = keyguardProblemText(.server(e)) ?? "" }
            } catch {}
        }
        loading = task
        await task.value
    }

    private func fail(_ p: KeyguardFlow.Problem) {
        if let t = keyguardProblemText(p) { error = t; Haptics.warning() }
    }

    /// Enrols this phone: a key from the server, wrapped here, the blob back; the recovery code once (when this turns it on).
    func enroll() {
        guard let api = host.api, let key else { return }
        busy = true; error = ""; note = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                let (v, code) = try await KeyguardFlow.enroll(calls: KeyguardCalls(api: api), key: key)
                view = v
                _ = await host.refreshKeyguard()
                if let code { recovery = code } else if v.on { enabledTick += 1 }
                me = (try? await api.devices())?.current ?? me
            } catch let p as KeyguardFlow.Problem { fail(p) } catch {}
        }
    }

    /// The code written down: the page that asked goes on.
    func recoveryWritten() { recovery = nil; enabledTick += 1 }

    func recover(code: String, done: @escaping () -> Void) {
        guard let api = host.api else { return }
        busy = true; error = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                try await KeyguardCalls(api: api).recover(code: code)
                done()
                host.setLocked(false)
                note = L(R.string.keyguard_unlocked)
                await load()
            } catch let e as ApiError {
                if e.status == 422 || e.status == 400 { error = L(R.string.keyguard_code_wrong) } else { fail(.server(e)) }
            } catch {}
        }
    }

    func removeThisPhone() {
        guard let api = host.api, let id = me else { return }
        jobs.run { [self] in
            do {
                view = try await KeyguardCalls(api: api).removePhone(deviceId: id)
                try? key?.delete()
            } catch let e as ApiError { fail(.server(e)) } catch {}
        }
    }

    func turnOff() {
        guard let api = host.api else { return }
        jobs.run { [self] in
            do {
                view = try await KeyguardCalls(api: api).disable()
                try? key?.delete()
                _ = await host.refreshKeyguard()
            } catch let e as ApiError { fail(.server(e)) } catch {}
        }
    }
}
