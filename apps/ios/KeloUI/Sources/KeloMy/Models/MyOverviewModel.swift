import SwiftUI
import KeloCore
import KeloShared

/// The My home page: the numbers kept from last time show at once; each part replaces its old value when it arrives. Also what the
/// home page does to the whole account: clear the chat, sign out.
@MainActor @Observable final class MyOverviewModel {
    private(set) var o = MyOverview()
    private(set) var offline = false
    /// GET /api/android: the device Kelo drives. nil = no row (not set up, an older server, or the read failed); never kept for next time.
    private(set) var android: AndroidDeviceInfo?
    private(set) var notice: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private let onSignedOut: () -> Void
    @ObservationIgnored private let onHistoryCleared: () -> Void
    @ObservationIgnored private let removeLimit: UInt64
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    /// `onSignedOut`: the local sign-out, once this phone is off the server's list. `onHistoryCleared`: the screens drop what they hold.
    /// `removeLimit`: the longest the sign-out waits for the server (nanoseconds).
    init(api: KeloApi, cache: ScreenCache, onSignedOut: @escaping () -> Void, onHistoryCleared: @escaping () -> Void,
         removeLimit: UInt64 = 5_000_000_000) {
        self.api = api; self.cache = cache; self.onSignedOut = onSignedOut; self.onHistoryCleared = onHistoryCleared
        self.removeLimit = removeLimit
    }

    func clearNotice() { notice = nil }

    /// Reads every part again; a newer read cancels the older one, so an old answer never covers a newer one.
    func reload() { jobs.run { [self] in await load() } }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in await refresh() }
        loading = task
        await task.value
    }

    private func refresh() async {
        if let kept: MyOverview = cache.get("my") { o = kept }
        let api = api
        let usage = Task { try await api.get("/api/usage", as: MyUsage.self) }
        let devices = Task { try await api.devices() }
        let vault = Task { try await api.vault() }
        let uploads = Task { try await api.request("GET", "/api/uploads") }
        let resources = Task { try await api.resources() }
        let about = Task { try await api.about() }
        let walletN = Task { try await api.get("/api/wallet/notifications?limit=1", as: WalletNotifications.self) }
        let wallet = Task { try await api.get("/api/wallet/cards", as: WalletInfo.self) }
        let op = Task { try await api.get("/api/operator/status", as: OperatorStatus.self) }
        let serverUpdate = Task { await ServerUpdateRules.load(api) }
        let android = Task { try await api.get("/api/android", as: AndroidDeviceInfo.self) }
        var failures: [Error?] = []
        func step<T>(_ t: Task<T, Error>, _ apply: (T) -> Void) async {
            let r = await t.result
            if Task.isCancelled { return }   // a newer read is under way
            switch r {
            case .success(let v): apply(v); failures.append(nil)
            case .failure(let e): failures.append(e)
            }
        }
        await step(usage) { o.usage = $0 }
        await step(devices) { o.devices = $0.devices.count }
        await step(vault) { o.creds = $0.items; o.locked = $0.locked }
        await step(uploads) { o.documents = $0.arrayValue?.count }
        await step(resources) {
            o.sizes = $0.profile
            o.storage = StorageRules.key(StorageRules.state($0.alert))
            o.storagePercent = $0.alert.percent
        }
        await step(about) { o.version = $0.version }
        await step(walletN) { o.walletUnseen = $0.unseen }
        await step(wallet) { o.cards = $0.cards.count }
        // the Operator section only for the server that runs the shared services (Android MyScreen: /api/operator/status)
        let opResult = await op.result
        if Task.isCancelled { return }
        switch opResult {
        case .success(let st): o.operator = OperatorRules.operatorFrom(nil, st.isOperator)
        case .failure(let e):
            if let a = e as? ApiError, a.status == 403 || a.status == 404 {
                var older: Int? = -1
                if a.status == 404 {   // an older server: the owner's own token can read the operator settings
                    do { _ = try await api.request("GET", "/api/operator/firebase"); older = nil }
                    catch let f as ApiError where f.status == 403 { older = 403 } catch {}
                }
                if let shown = OperatorRules.operatorFrom(a.status, nil, fallback: { OperatorRules.ownerFrom(older) }) { o.operator = shown }
            }
        }
        // the server update: only the owner's device, only a server that has it (403 / 404 / unavailable: nothing)
        let update = await serverUpdate.value
        if Task.isCancelled { return }
        switch update {
        case .shown(let st): o.serverUpdate = st
        case .hidden: o.serverUpdate = nil
        case .failed: break   // not reached: keep what was shown
        }
        // the device's row: shown only when the server has a device set up; a failed read hides it (and is not "offline")
        let androidResult = await android.result
        if Task.isCancelled { return }
        self.android = try? androidResult.get()
        offline = MyOverview.offline(failures)
        if failures.contains(where: { $0 == nil }) { cache.put("my", o) }
    }

    /// While a server update runs, its card's progress moves: asked every few seconds while 마이 shows (the page's `.task`, so it
    /// ends with the page; failures pass: the server restarts on the way).
    func followServerUpdate() async {
        while o.serverUpdate?.isRunning == true, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled else { return }
            switch await ServerUpdateRules.load(api) {
            case .shown(let st): o.serverUpdate = st
            case .hidden: o.serverUpdate = nil
            case .failed: break
            }
        }
    }

    /// 대화 기록 삭제: the chat and what came of it (the server stops running work first).
    func clearChat() {
        jobs.run { [self] in
            do {
                try await api.clearMessages()
                onHistoryCleared()   // the kept chat is emptied and the screens drop what they hold
                notice = L(R.string.my_clear_chat_done)
            } catch {
                notice = ApiMessage.text(error)
            }
        }
    }

    /// Sign out: this phone first leaves the server's device list (best effort, at most `removeLimit`), then the local sign-out.
    func signOut() {
        jobs.run { [self] in
            await removeThisPhone()
            onSignedOut()
        }
    }

    /// GET /api/devices → `current`, DELETE /api/devices/{current}. A failure or a slow server never keeps the person signed in.
    private func removeThisPhone() async {
        let api = api, limit = removeLimit
        // a race, not a task group: a group would wait for the slow call even after it was cancelled
        let (done, finish) = AsyncStream<Void>.makeStream()
        let removal = Task {
            if let list = try? await api.devices(), let me = list.current { try? await api.removeDevice(me) }
            finish.yield()
        }
        let timer = Task { try? await Task.sleep(nanoseconds: limit); finish.yield() }
        for await _ in done { break }
        removal.cancel(); timer.cancel()
    }
}
