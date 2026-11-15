import SwiftUI
import KeloCore
import KeloShared

// Keyguard on the iPhone (Android keyguard/KeyguardUi.kt): the page in 마이 → 보안, the locked banner over the app, the recovery code
// shown once, and the "Auto-pay needs Keyguard" question. The phone's key is KeloCore's `KeyguardKey` (Secure Enclave P-256 + HPKE,
// blob 0x02; the software key on the simulator).

/// This phone's Keyguard key for the signed-in server (nil when signed out or the phone cannot hold one).
@MainActor func keyguardKey(_ app: AppModel) -> KeyguardKey? {
    guard let url = app.session?.serverUrl else { return nil }
    return KeyguardKeys.platform(serverUrl: url)
}

/// What to say when an unlock or enrol did not happen (nil: the owner backed out, nothing to say).
@MainActor func keyguardProblemText(_ p: KeyguardFlow.Problem) -> String? {
    switch p {
    case .cancelled: nil
    case .noLock: L(R.string.keyguard_no_lock_ios)
    case .keyLost: L(R.string.keyguard_key_lost)
    case .notHere: L(R.string.keyguard_locked_other)
    case .server(let e):
        if e.code == KeyguardRules.codeEnvKey { L(R.string.keyguard_env_key) }
        else if let c = e.code, KeyguardRules.lastPhoneCodes.contains(c) { L(R.string.keyguard_last_phone) }
        else if e.status == 403 { L(R.string.keyguard_not_owner) }
        else if e.isNetwork || e.detail.isEmpty { L(R.string.keyguard_failed) }
        else { e.detail }
    }
}

/// Unlocks Kelo with this phone's copy; nil when unlocked, else what to say (`.cancelled`: nothing).
@MainActor func keyguardUnlock(_ app: AppModel) async -> KeyguardFlow.Problem? {
    guard let api = app.api, let key = keyguardKey(app) else { return .keyLost }
    do {
        try await KeyguardFlow.unlock(calls: KeyguardCalls(api: api), key: key, reason: L(R.string.keyguard_prompt_title))
        app.setLocked(false)
        Haptics.success()
        return nil
    } catch let p as KeyguardFlow.Problem {
        return p
    } catch {
        return .server(ApiError(status: 0, detail: ""))
    }
}

/// "Kelo is locked" under the header while the server holds no key (423 answers, /api/agent/status "locked", the keyguard_locked
/// notification): Unlock with this phone's copy, or, on a phone without one, the recovery code in 마이 → 보안 → Keyguard.
public struct LockedBanner: View {
    let onRecover: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var busy = false
    @State private var problem: KeyguardFlow.Problem?

    public init(onRecover: @escaping () -> Void) { self.onRecover = onRecover }

    public var body: some View {
        let other = problem == .notHere
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                KeloIconView(.lock, size: 22, tint: c.accent)
                Text(L(R.string.keyguard_locked_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityAddTraits(.isHeader)
            }
            MutedText(L(other ? R.string.keyguard_locked_other : R.string.keyguard_locked_body), small: true)
            if let p = problem, !other, let text = keyguardProblemText(p) { ErrorText(text, small: true) }
            if !other {
                PrimaryButton(L(busy ? R.string.keyguard_unlocking : R.string.keyguard_unlock), enabled: !busy) { go() }
                    .accessibilityIdentifier("kelo.keyguard.unlock")
            }
            SecondaryButton(L(R.string.keyguard_use_code), fill: true, action: onRecover)
        }
        .padding(16)
        .background(RoundedRectangle(cornerRadius: 14).fill(c.card).shadow(color: .black.opacity(0.12), radius: 8, y: 2))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(c.line, lineWidth: 1))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("kelo.keyguard.locked")
        // opened from the keyguard_locked notification: the check starts by itself, once
        .task(id: app.router.unlockRequested) {
            if app.router.unlockRequested { app.router.unlockRequested = false; go() }
        }
    }

    private func go() {
        guard !busy else { return }
        busy = true
        problem = nil
        Task {
            let p = await keyguardUnlock(app)
            busy = false
            problem = p == .cancelled ? nil : p
        }
    }
}

/// 마이 → 보안 → Keyguard: turn it on, the phones that hold the key, unlock or recover, remove this phone's copy, turn it off.
/// `enableNow`: opened from "Auto-pay needs Keyguard" (the turn-on question at once; `onEnabled` once it is on).
struct KeyguardPage: View {
    var startWithCode = false
    var enableNow = false
    var onEnabled: () -> Void = {}
    var onClose: (() -> Void)? = nil
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var view: KeyguardView?
    @State private var me: Int64?
    @State private var notOwner = false
    @State private var busy = false
    @State private var error = ""
    @State private var note = ""
    @State private var recovery: String?   // shown once, then forgotten
    @State private var wroteDown = false
    @State private var codeOpen = false
    @State private var code = ""
    @State private var confirm: Confirm?
    @State private var started = false

    enum Confirm: Identifiable { case on, remove, off; var id: Self { self } }

    private func back() { if let onClose { onClose() } else { dismiss() } }

    var body: some View {
        Group {
            if let rc = recovery { recoveryView(rc) } else { page }
        }
        .task {
            if !started { started = true; codeOpen = startWithCode; if enableNow { confirm = .on } }
            await load()
        }
        .alert(confirmText, isPresented: Binding(get: { confirm != nil }, set: { if !$0 { confirm = nil } })) {
            switch confirm {
            case .on: Button(L(R.string.keyguard_turn_on)) { confirm = nil; enroll() }
            case .remove: Button(L(R.string.keyguard_remove_this_phone), role: .destructive) { confirm = nil; removeThisPhone() }
            case .off: Button(L(R.string.keyguard_turn_off), role: .destructive) { confirm = nil; turnOff() }
            case nil: EmptyView()
            }
            Button(L(R.string.cancel), role: .cancel) { if confirm == .on && enableNow { back() }; confirm = nil }
        }
    }

    private var confirmText: String {
        switch confirm {
        case .on: L(R.string.keyguard_turn_on_confirm)
        case .remove: L(R.string.keyguard_remove_confirm)
        case .off: L(R.string.keyguard_turn_off_confirm)
        case nil: ""
        }
    }

    private var page: some View {
        SubPage(L(R.string.keyguard_title), onBack: back) {
            MyCard {
                MutedText(L(R.string.keyguard_intro), small: false)
                MutedText(L(R.string.keyguard_restart_note), small: true)
            }
            if notOwner {
                MyCard { MutedText(L(R.string.keyguard_not_owner), small: true) }
            } else if let v = view {
                if !v.on {
                    MyCard {
                        PrimaryButton(L(busy ? R.string.keyguard_turning_on : R.string.keyguard_turn_on), enabled: !busy && keyguardKey(app) != nil) {
                            confirm = .on
                        }
                        .accessibilityIdentifier("kelo.keyguard.turnOn")
                    }
                } else {
                    if v.locked {
                        MyCard {
                            Text(L(R.string.keyguard_locked_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                            if v.holds(me) && keyguardKey(app) != nil {
                                PrimaryButton(L(busy ? R.string.keyguard_unlocking : R.string.keyguard_unlock), enabled: !busy) { unlock() }
                            } else {
                                MutedText(L(R.string.keyguard_locked_other), small: true)
                            }
                        }
                    }
                    MyCard {
                        Text(L(R.string.keyguard_phones_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                        ForEach(v.phones, id: \.deviceId) { p in
                            Text(p.deviceId == me ? L(R.string.keyguard_this_phone, p.name) : p.name).font(KeloFont.body).foregroundStyle(c.ink)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        if !v.locked && !v.holds(me) {
                            SecondaryButton(L(R.string.keyguard_add_this_phone), enabled: !busy && keyguardKey(app) != nil, fill: true) { enroll() }
                        }
                        if v.holds(me) {
                            SecondaryButton(L(R.string.keyguard_remove_this_phone), enabled: !busy, fill: true) { confirm = .remove }
                        }
                        if !v.locked {
                            SecondaryButton(L(R.string.keyguard_turn_off), enabled: !busy, fill: true) { confirm = .off }
                        }
                    }
                }
            }
            if view?.on == true || codeOpen {
                MyCard {
                    if !codeOpen {
                        SecondaryButton(L(R.string.keyguard_use_code), fill: true) { codeOpen = true }
                    } else {
                        Text(L(R.string.keyguard_use_code)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                        // an ASCII keyboard even when the phone types Korean or Japanese; shown in capitals as the code is
                        KeloTextField(L(R.string.keyguard_code_hint), text: $code, keyboard: .ascii, maxLength: 64)
                            .keloCapitals()
                            .onChange(of: code) { _, v in
                                // capitals as the code is written; letters, digits and the separators people type
                                let shown = String(v.uppercased().filter { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == " ") })
                                if shown != v { code = shown }
                            }
                            .accessibilityIdentifier("kelo.keyguard.code")
                        PrimaryButton(L(R.string.keyguard_unlock), enabled: !busy && KeyguardRules.codeReady(code)) { recover() }
                    }
                }
            }
            if !note.isEmpty { MutedText(note, small: true) }
            if !error.isEmpty { ErrorText(error, small: true) }
        }
    }

    /// The recovery code, once: nothing else on the page until "I wrote it down".
    private func recoveryView(_ rc: String) -> some View {
        SubPage(L(R.string.keyguard_recovery_title), onBack: {}) {
            MyCard {
                Text(L(R.string.keyguard_recovery_warning)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                Text(KeyguardRules.codeLines(rc)).font(.system(.title3, design: .monospaced).weight(.bold)).foregroundStyle(c.ink)
                    .lineSpacing(6)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true).padding(.vertical, 8)
                    .accessibilityIdentifier("kelo.keyguard.recoveryCode")
                // the whole row ticks the box, not only the box
                Button { wroteDown.toggle() } label: {
                    HStack(spacing: 10) {
                        Image(systemName: wroteDown ? "checkmark.square.fill" : "square").font(.system(size: 22))
                            .foregroundStyle(wroteDown ? c.accent : c.muted)
                        Text(L(R.string.keyguard_recovery_confirm)).font(KeloFont.body).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 8)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(wroteDown ? .isSelected : [])
                .accessibilityIdentifier("kelo.keyguard.wroteDown")
                PrimaryButton(L(R.string.keyguard_done), enabled: wroteDown) {
                    recovery = nil
                    onEnabled()
                }
            }
        }
        .keloSwipeBackBlocked()   // the code is shown once: the edge swipe must not leave before "I wrote it down"
    }

    // ---- calls ---------------------------------------------------------------------------------------------------------------

    private func load() async {
        guard let api = app.api else { return }
        do {
            let v = try await KeyguardCalls(api: api).view()
            view = v
            app.setLocked(v.locked)
            me = (try? await api.devices())?.current
            _ = await app.refreshKeyguard()
        } catch let e as ApiError {
            if e.status == 403 { notOwner = true } else { error = keyguardProblemText(.server(e)) ?? "" }
        } catch {}
    }

    private func fail(_ p: KeyguardFlow.Problem) {
        if let t = keyguardProblemText(p) { error = t; Haptics.warning() }
    }

    /// Enrols this phone: a key from the server, wrapped here, the blob back; the recovery code once (when this turns it on).
    private func enroll() {
        guard let api = app.api, let key = keyguardKey(app) else { return }
        busy = true; error = ""; note = ""
        Task {
            defer { busy = false }
            do {
                let (v, code) = try await KeyguardFlow.enroll(calls: KeyguardCalls(api: api), key: key)
                view = v
                _ = await app.refreshKeyguard()
                if let code { wroteDown = false; recovery = code } else if v.on { onEnabled() }
                me = (try? await api.devices())?.current ?? me
            } catch let p as KeyguardFlow.Problem { fail(p) } catch {}
        }
    }

    private func unlock() {
        busy = true; error = ""
        Task {
            let p = await keyguardUnlock(app)
            busy = false
            if let p { fail(p) } else { note = L(R.string.keyguard_unlocked); await load() }
        }
    }

    private func recover() {
        guard let api = app.api else { return }
        busy = true; error = ""
        Task {
            defer { busy = false }
            do {
                try await KeyguardCalls(api: api).recover(code: code)
                code = ""; codeOpen = false
                app.setLocked(false)
                note = L(R.string.keyguard_unlocked)
                await load()
            } catch let e as ApiError {
                if e.status == 422 || e.status == 400 { error = L(R.string.keyguard_code_wrong) } else { fail(.server(e)) }
            } catch {}
        }
    }

    private func removeThisPhone() {
        guard let api = app.api, let id = me else { return }
        Task {
            do {
                view = try await KeyguardCalls(api: api).removePhone(deviceId: id)
                try? keyguardKey(app)?.delete()
            } catch let e as ApiError { fail(.server(e)) } catch {}
        }
    }

    private func turnOff() {
        guard let api = app.api else { return }
        Task {
            do {
                view = try await KeyguardCalls(api: api).disable()
                try? keyguardKey(app)?.delete()
                _ = await app.refreshKeyguard()
            } catch let e as ApiError { fail(.server(e)) } catch {}
        }
    }
}

/// "Auto-pay needs Keyguard" (owner's decision): [Turn on Keyguard] opens its enable flow over the page and, once it is on,
/// `onEnabled` goes on with what was asked (turning auto-pay on); [Cancel] leaves everything as it was.
struct KeyguardNeeded: ViewModifier {
    @Binding var isPresented: Bool
    let onEnabled: () -> Void
    @State private var enabling = false

    func body(content: Content) -> some View {
        content
            .alert(L(R.string.wallet_keyguard_needed_title), isPresented: $isPresented) {
                Button(L(R.string.keyguard_turn_on_named)) { isPresented = false; enabling = true }
                Button(L(R.string.cancel), role: .cancel) { isPresented = false }
            } message: {
                Text(L(R.string.wallet_keyguard_needed_body))
            }
            .keloFullScreen(isPresented: $enabling) {
                KeyguardPage(enableNow: true, onEnabled: { enabling = false; onEnabled() }, onClose: { enabling = false })
                    .keloTheme()
            }
    }
}

extension View {
    func keyguardNeeded(_ isPresented: Binding<Bool>, onEnabled: @escaping () -> Void) -> some View {
        modifier(KeyguardNeeded(isPresented: isPresented, onEnabled: onEnabled))
    }
}

private extension View {
    /// The keyboard starts in capitals (iOS).
    @ViewBuilder func keloCapitals() -> some View {
        #if os(iOS)
        textInputAutocapitalization(.characters)
        #else
        self
        #endif
    }
}
