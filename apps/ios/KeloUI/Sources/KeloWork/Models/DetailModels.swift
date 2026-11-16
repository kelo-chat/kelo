import SwiftUI
import Observation
import KeloCore
import KeloShared

/// A task in full (the page's `.task(id: tick)` runs `follow()`: it reads the task and, while it runs, again every 3 seconds; it ends
/// with the page). A read that comes back after the page asked again is dropped.
@MainActor @Observable
final class TaskDetailModel {
    private(set) var detail: TaskDetail?
    private(set) var failed = false
    @ObservationIgnored private let work: WorkModel
    @ObservationIgnored private let id: Int64
    @ObservationIgnored private var seq = Latest()

    init(work: WorkModel, id: Int64) { self.work = work; self.id = id }

    func follow() async {
        while !Task.isCancelled {
            let n = seq.begin()
            do {
                let d = try await work.task(id)
                if seq.accept(n), !Task.isCancelled { detail = d; failed = false }
            } catch {
                if Task.isCancelled { return }
                if seq.accept(n) { failed = true }
            }
            if detail?.running != true { break }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
    }
}

/// A background job in full: read again every 3 seconds while it runs.
@MainActor @Observable
final class WorkerDetailModel {
    private(set) var detail: WorkerDetail?
    private(set) var failed = false
    @ObservationIgnored private let work: WorkModel
    @ObservationIgnored private let id: String
    @ObservationIgnored private var seq = Latest()

    init(work: WorkModel, id: String) { self.work = work; self.id = id }

    func follow() async {
        while !Task.isCancelled {
            let n = seq.begin()
            do {
                let d = try await work.worker(id)
                if seq.accept(n), !Task.isCancelled { detail = d; failed = false }
            } catch {
                if Task.isCancelled { return }
                if seq.accept(n) { failed = true }
            }
            if detail?.status != Worker.statusRunning { break }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
    }
}

/// One schedule's recent runs and what its runs may do without asking (each grant can be taken back).
@MainActor @Observable
final class ScheduleRunsModel {
    private(set) var runs: [ScheduleRun]?
    private(set) var grants: [ScheduleGrant] = []
    @ObservationIgnored private let work: WorkModel
    @ObservationIgnored private let id: Int64
    @ObservationIgnored let jobs = Jobs()

    init(work: WorkModel, id: Int64) { self.work = work; self.id = id }

    func load() async {
        let r = (try? await work.scheduleRuns(id)) ?? []
        // loaded here: a view with nothing to show yet never appears, so its own .task would never run
        let g = (try? await work.scheduleGrants(id)) ?? []
        if Task.isCancelled { return }
        runs = r; grants = g
    }

    func revoke(_ g: ScheduleGrant) {
        jobs.run { [self] in
            do { try await work.revokeScheduleGrant(g); grants.removeAll { $0.id == g.id }; Haptics.success() }
            catch { Haptics.warning() }
        }
    }
}

/// The newest picture kept of a tab (when there is no live view).
@MainActor @Observable
final class ScreenshotModel {
    private(set) var image: PlatformImage?
    private(set) var problem: String?
    private(set) var loading = true
    @ObservationIgnored private let work: WorkModel
    @ObservationIgnored private let id: String

    init(work: WorkModel, id: String) { self.work = work; self.id = id }

    func load() async {
        loading = image == nil
        do {
            let data = try await work.screenshot(id)
            if Task.isCancelled { return }
            image = PlatformImage(data: data)
            problem = nil
        } catch let e as ApiError {
            problem = e.status == 404 ? L(R.string.agent_tab_no_picture) : L(R.string.connect_failed)
        } catch { problem = L(R.string.connect_failed) }
        loading = false
    }
}

/// A watcher's recent checks (read again when the watcher's own state moved: its dry run or last check).
@MainActor @Observable
final class WatcherEventsModel {
    private(set) var events: [WatcherEvent]?
    @ObservationIgnored private let work: WorkModel
    @ObservationIgnored private let id: Int64

    init(work: WorkModel, id: Int64) { self.work = work; self.id = id }

    func load() async {
        let got = (try? await work.watcherEvents(id)) ?? []
        if !Task.isCancelled { events = got }
    }
}

/// The browser tab a live view opens on: the one asked for, or "owner" = the tab this phone's chat replies work in (this phone's own
/// device id, found with GET /api/devices; "owner" itself when signed in with the access key).
@MainActor @Observable
final class LiveOverlayModel {
    private(set) var resolved: String?
    @ObservationIgnored private let api: () -> KeloApi?

    init(api: @escaping () -> KeloApi?) { self.api = api }

    func resolve(_ tabId: String) async {
        if tabId != "owner" { resolved = tabId; return }
        let own = await Self.ownTabId(api())
        if !Task.isCancelled { resolved = own }
    }

    static func ownTabId(_ api: KeloApi?) async -> String {
        guard let api, let list = try? await api.devices(), let current = list.current else { return "owner" }
        return String(current)
    }
}
