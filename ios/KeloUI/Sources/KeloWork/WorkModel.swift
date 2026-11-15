import Foundation
import Observation
import KeloCore
import KeloShared

/// One list's loading state (Android Loadable).
struct Loadable<T> {
    var data: T?
    var loading = false
    var failed = false
}

struct TaskList: Sendable {
    var items: [AgentTask] = []
    var nextBefore: Int64?
    var loadingMore = false
}

/// The schedules with their first / last days (starts_on / ends_on, read beside KeloCore's Schedule).
struct ScheduleBook: Sendable {
    var items: [Schedule] = []
    var spans: [Int64: ScheduleSpan] = [:]
    var timezone = ""
}

/// The agent's work for the work page (Android agent/AgentViewModel.kt, the parts the work page shows: tasks, background jobs,
/// watchers, approvals waiting, follow-ups on hold, schedules and their archive, browser tabs). Each part refreshes on its own; a
/// failed refresh keeps what is shown. Short results go to `toast`.
@MainActor @Observable
final class WorkModel {
    static let page = 50

    var tasks = Loadable<TaskList>()
    var workers = Loadable<WorkerList>()
    var watchers = Loadable<WatcherList>()
    var pending = Loadable<[PendingApproval]>()
    /// What the user allowed for running tasks as a whole (GET /api/task-permissions).
    var grants = Loadable<[TaskPermission]>()
    /// Approvals being answered right now.
    var answering: Set<String> = []
    var followups = Loadable<FollowupList>()
    var followupBusy: Set<String> = []
    /// Secure entries waiting for a value (GET /api/secret-requests): rows in 답이 필요해요; an older server counts as none.
    var entries = Loadable<[SecretRequest]>()
    var schedules = Loadable<ScheduleBook>()
    var archive = Loadable<ScheduleArchive>()
    /// Deletes in 보관한 예약 waiting for the undo message, by schedule id (see `deleteArchived`)
    @ObservationIgnored var pendingErase: [Int64: Task<Void, Never>] = [:]
    var browser = Loadable<BrowserTabs>()
    /// The newest short message (snackbar).
    var toast: Toast?
    /// The id of the "deleted" message whose 실행 취소 covers the waiting archive deletes (`commitArchivedErases` takes it away).
    @ObservationIgnored private var eraseToast: UUID?

    @ObservationIgnored private var markedUpTo: Int64 = 0
    /// Where the signed-in API comes from (set by the screen from the app model).
    @ObservationIgnored var api: () -> KeloApi? = { nil }

    init(api: @escaping () -> KeloApi? = { nil }) { self.api = api }

    /// A short message; with `action` (실행 취소) it has a button: `onAction` runs when it is pressed, `onEnd` when the message goes
    /// without it (timed out, tapped away or replaced by the next one).
    struct Toast: Equatable, Identifiable {
        let id = UUID()
        let text: String
        var action: String? = nil
        var onAction: (() -> Void)? = nil
        var onEnd: (() -> Void)? = nil

        static func == (a: Toast, b: Toast) -> Bool { a.id == b.id }
    }

    private func requireApi() throws -> KeloApi {
        guard let a = api() else { throw ApiError(status: 401, detail: "signed out") }
        return a
    }

    func say(_ text: String) { show(Toast(text: text)) }

    /// A message with 실행 취소: `undo` when it is pressed, else `otherwise` once the message goes.
    func sayUndo(_ text: String, undo: @escaping () -> Void, otherwise: @escaping () -> Void = {}) {
        show(Toast(text: text, action: L(R.string.undo), onAction: undo, onEnd: otherwise))
    }

    /// The next message replaces the shown one, which ends without its action.
    private func show(_ t: Toast) {
        let old = toast
        toast = t
        old?.onEnd?()
    }

    func sayError(_ e: Error) {
        if let a = e as? ApiError, !a.isNetwork, !a.detail.isEmpty { say(L(R.string.agent_error_detail, a.detail)) } else { say(L(R.string.connect_failed)) }
    }

    /// Runs `block` into `path` unless that part is already loading; a failure keeps the data and marks it failed.
    private func refresh<T>(_ path: ReferenceWritableKeyPath<WorkModel, Loadable<T>>, _ block: @escaping () async throws -> T) {
        if self[keyPath: path].loading { return }
        self[keyPath: path].loading = true
        Task {
            do {
                let v = try await block()
                self[keyPath: path] = Loadable(data: v)
            } catch {
                self[keyPath: path].loading = false
                self[keyPath: path].failed = true
            }
        }
    }

    /// Everything the timeline shows (also what changes while the agent works).
    func loadWork() {
        loadTasks(); loadWorkers(); loadWatchers(); loadPending(); loadGrants(); loadSchedules(); loadBrowser(); loadFollowups()
        loadEntries()
    }

    // ---- secure entries waiting for a value (404: none)
    func loadEntries() {
        refresh(\.entries) { [self] in
            do { return try await SecretCalls(api: requireApi()).list() } catch let e as ApiError where e.status == 404 { return [] }
        }
    }

    /// 그만두기 on an entry's row: the agent is told and does not ask again; the row and the badge go at once (`onClosed`).
    func cancelEntry(_ r: SecretRequest, onClosed: (String) -> Void = { _ in }) {
        entries.data = entries.data?.filter { $0.id != r.id }
        onClosed(r.kind)
        Task {
            do { try await SecretCalls(api: requireApi()).cancel(id: r.id) } catch let e as ApiError { if e.status != 404 { sayError(e) } } catch {}
            loadEntries()
        }
    }

    // ---- tasks
    func loadTasks() {
        refresh(\.tasks) { [self] in
            let p = try await requireApi().tasks(limit: WorkModel.page)
            return TaskList(items: p.items, nextBefore: p.nextBefore)
        }
    }

    func loadMoreTasks() {
        guard var cur = tasks.data, let before = cur.nextBefore, !cur.loadingMore else { return }
        cur.loadingMore = true
        tasks.data = cur
        Task {
            do {
                let p = try await requireApi().tasks(limit: WorkModel.page, before: before)
                guard var d = tasks.data else { return }
                let known = Set(d.items.map(\.id))
                d.items += p.items.filter { !known.contains($0.id) }
                d.nextBefore = p.nextBefore
                d.loadingMore = false
                tasks.data = d
            } catch {
                tasks.data?.loadingMore = false
            }
        }
    }

    /// Everything finished up to `upTo` counts as seen on the server (the dots stay for this visit).
    func markSeen(_ upTo: Int64) {
        guard upTo > markedUpTo else { return }
        markedUpTo = upTo
        Task { _ = try? await requireApi().tasksSeen(upTo: upTo) }
    }

    func task(_ id: Int64) async throws -> TaskDetail { try await requireApi().task(id) }

    func stopTask(_ id: Int64, then: @escaping () -> Void = {}) {
        Task {
            do {
                try await requireApi().stopTask(id)
                say(L(R.string.agent_task_stopped))
            } catch let e as ApiError {
                if e.status == 404 { say(L(R.string.agent_task_not_running)) } else { sayError(e) }
            } catch { sayError(error) }
            then()
            loadTasks()
        }
    }

    // ---- background jobs (an older server without them answers 404: none)
    func loadWorkers() {
        refresh(\.workers) { [self] in
            do { return try await requireApi().workers(limit: WorkModel.page) } catch let e as ApiError where e.status == 404 { return WorkerList() }
        }
    }

    func worker(_ id: String) async throws -> WorkerDetail { try await requireApi().worker(id) }

    func stopWorker(_ id: String, then: @escaping () -> Void = {}) {
        workerCall(id, done: L(R.string.work_job_stopped), then: then) { try await $0.stopWorker(id) }
    }

    func resumeWorker(_ id: String, note: String = "", then: @escaping () -> Void = {}) {
        let n = note.trimmingCharacters(in: .whitespacesAndNewlines)
        workerCall(id, done: L(R.string.work_job_resumed), then: then) { try await $0.resumeWorker(id, note: n.isEmpty ? nil : n) }
    }

    func messageWorker(_ id: String, text: String, then: @escaping () -> Void = {}) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        workerCall(id, done: L(R.string.work_job_message_sent), then: then) { try await $0.messageWorker(id, text: t) }
    }

    private func workerCall(_ id: String, done: String, then: @escaping () -> Void, _ call: @escaping (KeloApi) async throws -> WorkerAnswer) {
        Task {
            do {
                _ = try await call(requireApi())
                say(done)
                then()
            } catch let e as ApiError {
                switch e.status {
                case 404: say(L(R.string.work_job_gone))
                case 409: say(L(R.string.work_job_busy))
                case 429: say(L(R.string.work_job_too_many))
                default: sayError(e)
                }
            } catch { sayError(error) }
            loadWorkers()
            loadTasks()
        }
    }

    // ---- watchers (404: none)
    func loadWatchers() {
        refresh(\.watchers) { [self] in
            do { return try await requireApi().watchers() } catch let e as ApiError where e.status == 404 { return WatcherList() }
        }
    }

    func watcherEvents(_ id: Int64) async throws -> [WatcherEvent] { try await requireApi().watcherEvents(id).items }

    private func replaceWatcher(_ w: Watcher) {
        guard var d = watchers.data else { return }
        d.items = d.items.map { $0.id == w.id ? w : $0 }
        watchers.data = d
    }

    /// On or off; turning on needs a fresh successful check first (409).
    func setWatcherEnabled(_ w: Watcher, _ on: Bool) {
        var shown = w
        shown.enabled = on
        replaceWatcher(shown)
        Task {
            do { replaceWatcher(try await requireApi().enableWatcher(w.id, on)) } catch let e as ApiError {
                replaceWatcher(w)
                if e.status == 409 { say(L(R.string.work_watch_needs_check)) } else { sayError(e) }
            } catch { replaceWatcher(w); sayError(error) }
        }
    }

    func dryRunWatcher(_ w: Watcher, then: @escaping (DryRun?) -> Void = { _ in }) {
        Task {
            do {
                let a = try await requireApi().dryRunWatcher(w.id)
                if let nw = a.watcher { replaceWatcher(nw) }
                then(a.result)
            } catch { sayError(error); then(nil) }
        }
    }

    func deleteWatcher(_ w: Watcher, then: @escaping () -> Void) {
        Task {
            do {
                try await requireApi().deleteWatcher(w.id)
                say(L(R.string.work_watch_deleted))
                then(); loadWatchers()
            } catch let e as ApiError where e.status == 404 { then(); loadWatchers() } catch { sayError(error) }
        }
    }

    // ---- schedules
    func loadSchedules() {
        refresh(\.schedules) { [self] in
            let raw = try await requireApi().request("GET", "/api/schedules")
            return WorkModel.book(raw)
        }
    }

    /// GET /api/schedules read twice: KeloCore's Schedule, and the first / last day of each.
    nonisolated static func book(_ raw: JSON) -> ScheduleBook {
        let list = (try? KeloApi.decode(ScheduleList.self, raw)) ?? ScheduleList()
        var spans: [Int64: ScheduleSpan] = [:]
        for item in raw.objectValue?["items"]?.arrayValue ?? [] {
            let o = Lenient(item)
            guard let id = o.optInt64("id") else { continue }
            spans[id] = ScheduleSpan(startsOn: o.optStr("starts_on"), endsOn: o.optStr("ends_on"))
        }
        return ScheduleBook(items: list.items, spans: spans, timezone: list.timezone)
    }

    private func replaceSchedule(_ s: Schedule) {
        guard var d = schedules.data else { return }
        d.items = d.items.map { $0.id == s.id ? s : $0 }
        schedules.data = d
    }

    func setEnabled(_ s: Schedule, _ on: Bool) {
        var shown = s
        shown.enabled = on
        replaceSchedule(shown)
        Task {
            do { replaceSchedule(try await requireApi().updateSchedule(s.id, ["enabled": .bool(on)])) } catch {
                replaceSchedule(s); sayError(error)
            }
        }
    }

    func saveSchedule(id: Int64?, body: JSON, onFailed: @escaping () -> Void, onSaved: @escaping (Schedule) -> Void) {
        Task {
            do {
                let api = try requireApi()
                let s = id == nil ? try await api.createSchedule(body) : try await api.updateSchedule(id!, body)
                say(L(R.string.agent_schedule_saved))
                onSaved(s)
                loadSchedules()
            } catch { onFailed(); sayError(error) }
        }
    }

    func resumeSchedule(_ id: Int64) {
        Task {
            do {
                replaceSchedule(try await requireApi().resumeSchedule(id))
                say(L(R.string.work_schedule_resumed))
            } catch { sayError(error) }
            loadSchedules()
        }
    }

    func scheduleRuns(_ id: Int64) async throws -> [ScheduleRun] { try await requireApi().scheduleRuns(id).items }
    /// What a schedule may do without asking (allowed once at setup), and taking one back.
    func scheduleGrants(_ id: Int64) async throws -> [ScheduleGrant] { try await requireApi().scheduleGrants(id).items }
    func revokeScheduleGrant(_ g: ScheduleGrant) async throws { try await requireApi().revokeScheduleGrant(g) }

    func loadArchive() {
        refresh(\.archive) { [self] in
            var a: ScheduleArchive
            do { a = try await requireApi().scheduleArchive() } catch let e as ApiError where e.status == 404 { a = ScheduleArchive() }
            // one swiped away whose delete still waits for the undo message stays hidden
            let waiting = Set(pendingErase.keys)
            if !waiting.isEmpty { a.items.removeAll { waiting.contains($0.id) } }
            return a
        }
    }

    func restoreSchedule(_ id: Int64) {
        Task {
            do {
                _ = try await requireApi().restoreSchedule(id)
                say(L(R.string.work_schedule_restored))
            } catch { sayError(error) }
            loadArchive(); loadSchedules()
        }
    }

    func runNow(_ id: Int64) {
        Task {
            do {
                _ = try await requireApi().runSchedule(id)
                say(L(R.string.agent_schedule_started))
                loadSchedules()
            } catch let e as ApiError where e.status == 409 { say(L(R.string.agent_schedule_already_running)) } catch { sayError(error) }
        }
    }

    /// The detail page's 삭제 dialog: into the archive (restorable for a while), or `forever`: gone at once.
    func deleteSchedule(_ id: Int64, forever: Bool = false, then: @escaping () -> Void) {
        Task {
            do {
                if forever { try await requireApi().deleteScheduleForever(id) } else { try await requireApi().archiveSchedule(id) }
                say(L(forever ? R.string.schedule_deleted_forever : R.string.agent_schedule_deleted))
                then(); loadSchedules(); loadArchive()
            } catch { sayError(error) }
        }
    }

    /// A swipe on the schedules list: into the archive at once, with 실행 취소 (which puts it back: `restoreSchedule`).
    func archiveSchedule(_ id: Int64) {
        if var book = schedules.data {
            book.items.removeAll { $0.id == id }
            schedules.data = book
        }
        sayUndo(L(R.string.agent_schedule_deleted), undo: { [weak self] in self?.restoreSchedule(id) })
        Task {
            do { try await requireApi().archiveSchedule(id) } catch { sayError(error) }
            loadSchedules(); loadArchive()
        }
    }

    /// A swipe in 보관한 예약: hidden at once; deleted for good (`eraseArchived`) once the message goes without 실행 취소.
    /// The delete waits here in the model (`pendingErase`), not in the message: closing the page sends it at once
    /// (`commitArchivedErases`), and one nobody decided still goes out after `eraseGrace`, so leaving never loses it.
    func deleteArchived(_ id: Int64) {
        if var a = archive.data {
            a.items.removeAll { $0.id == id }
            archive.data = a
        }
        pendingErase[id]?.cancel()
        pendingErase[id] = Task { [weak self] in
            try? await Task.sleep(nanoseconds: WorkModel.eraseGrace)
            if Task.isCancelled { return }
            self?.eraseArchived(id)
        }
        sayUndo(L(R.string.schedule_deleted_forever), undo: { [weak self] in self?.undoEraseArchived(id) },
                otherwise: { [weak self] in self?.eraseArchived(id) })
        eraseToast = toast?.id
    }

    /// 실행 취소: the delete is dropped and the schedule shows again.
    func undoEraseArchived(_ id: Int64) {
        guard let t = pendingErase.removeValue(forKey: id) else { return }
        t.cancel()
        loadArchive()
    }

    /// The archive page is closing: every waiting delete is sent now.
    func commitArchivedErases() {
        if pendingErase.isEmpty { return }
        for id in Array(pendingErase.keys) { eraseArchived(id) }
        // its 실행 취소 has nothing left to undo. Told by the message's id, not its words: the language can change in between
        // (and the tests switch the shared strings while they run in parallel).
        if let id = eraseToast, toast?.id == id { toast = nil }
        eraseToast = nil
    }

    func eraseArchived(_ id: Int64) {
        pendingErase.removeValue(forKey: id)?.cancel()
        Task {
            do { try await requireApi().eraseArchivedSchedule(id) } catch let e as ApiError where e.status == 404 {} catch { sayError(error) }
            loadArchive()
        }
    }

    /// How long a swiped-away archived schedule waits for 실행 취소 when no message decides (a little over the message's time)
    static let eraseGrace: UInt64 = 12_000_000_000

    // ---- approvals waiting
    func loadPending() {
        refresh(\.pending) { [self] in try await requireApi().pendingApprovals() }
    }

    /// Answers a waiting approval (`remember`: allow this kind of step on this site from now on; `rememberSchedule`: in that job).
    func answer(_ a: PendingApproval, approve: Bool, remember: Bool = false, rememberSchedule: Bool = false) {
        guard !answering.contains(a.id) else { return }
        answering.insert(a.id)
        let mode: String? = approve && remember && a.canRemember ? "site"
            : (approve && rememberSchedule && a.canRememberSchedule ? "schedule" : nil)
        Task {
            do {
                _ = try await requireApi().answerApproval(a.id, approve: approve, remember: mode)
                if approve { ApprovalHooks.afterAllow() }
                if !approve && a.isHumanCheck { say(L(R.string.handover_skipped, a.site.isEmpty ? L(R.string.handover_this_site) : a.site)) }
                else if !approve { say(L(R.string.agent_approval_denied)) } else if mode == "site" { say(L(R.string.agent_approval_always)) }
                else if mode == "schedule" { say(L(R.string.approval_kept_for_schedule)) } else { say(L(R.string.agent_approval_allowed)) }
            } catch let e as ApiError where e.status == 404 { say(L(R.string.agent_approval_gone)) } catch { sayError(error) }
            answering.remove(a.id)
            pending.loading = false
            loadPending()
        }
    }

    // ---- task permissions ("이 작업에서 허용한 일")
    func loadGrants() {
        refresh(\.grants) { [self] in try await requireApi().taskPermissions() }
    }

    /// Stops a task's permission at once: its next such step asks again.
    func revokeGrant(_ g: TaskPermission) {
        grants.data?.removeAll { $0.id == g.id }
        Task {
            do {
                try await requireApi().revokeTaskPermission(g.id)
                say(L(R.string.work_grant_revoked))
            } catch let e as ApiError where e.status == 404 {
            } catch {
                sayError(error); grants.loading = false; loadGrants()
            }
        }
    }

    // ---- follow-ups (404: none)
    func loadFollowups() {
        refresh(\.followups) { [self] in try await FollowupCalls(api: requireApi()).list() }
    }

    func runFollowup(_ f: Followup, _ step: FollowupStep) {
        if followupBusy.contains(f.id) { return }
        if case .openLogin = step { return }
        followupBusy.insert(f.id)
        Task {
            do {
                let (result, detail) = try await FollowupCalls(api: requireApi()).run(step)
                switch result {
                case .done:
                    switch step {
                    case .drop: say(L(R.string.followup_done_dropped))
                    case .snooze: say(L(R.string.followup_done_snoozed))
                    case .answer(_, let ok): say(L(ok ? R.string.followup_done_approved : R.string.followup_done_denied))
                    default: say(L(R.string.followup_done_resumed))
                    }
                case .gone: break
                case .failed: say(detail.isEmpty ? L(R.string.connect_failed) : L(R.string.agent_error_detail, detail))
                }
            } catch { sayError(error) }
            followupBusy.remove(f.id)
            loadFollowups()
            switch step {
            case .resumeWorker, .retry: loadWorkers(); loadPending()
            case .answer: loadPending()
            default: break
            }
        }
    }

    // ---- browser tabs
    func loadBrowser() {
        refresh(\.browser) { [self] in try await requireApi().browserTabs(thumbnails: true) }
    }

    func screenshot(_ tabId: String) async throws -> Data {
        try await requireApi().download("/api/browser/sessions/\(KeloApi.segment(tabId))/screenshot")
    }

    func closeTab(_ id: String, then: @escaping () -> Void) {
        Task {
            do {
                try await requireApi().closeBrowserTab(id)
                say(L(R.string.agent_tab_closed))
                then(); loadBrowser()
            } catch let e as ApiError {
                switch e.status {
                case 409: say(L(R.string.agent_tab_busy))
                case 404: then(); loadBrowser()
                default: sayError(e)
                }
            } catch { sayError(error) }
        }
    }

    func startBrowserJob(prompt: String, url: String, onStarted: @escaping () -> Void) {
        let u = url.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            do {
                _ = try await requireApi().startBrowserJob(prompt: prompt.trimmingCharacters(in: .whitespacesAndNewlines), url: u.isEmpty ? nil : u)
                say(L(R.string.agent_browser_started))
                onStarted()
                try? await Task.sleep(nanoseconds: 800_000_000)
                loadBrowser(); loadTasks()
            } catch let e as ApiError where e.status == 404 { say(L(R.string.agent_browser_none)) } catch { sayError(error) }
        }
    }
}
