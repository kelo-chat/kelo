import Foundation
import Observation
import KeloCore
import KeloShared

/// GET/POST /api/goals, PATCH/DELETE /api/goals/{id}, and the suggestions (Android goals/GoalsViewModel.kt). The goals kept from
/// last time show at once and are refreshed in place; a goal swiped away is hidden and deleted only when its undo offer is gone.
@MainActor @Observable
final class GoalsModel {
    var state = GoalsState()
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private var keptLoaded = false
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    /// `api`: where the signed-in API comes from, read when a call is made.
    init(api: @escaping () -> KeloApi?, cache: ScreenCache) { self.api = api; self.cache = cache }

    private func requireApi() throws -> KeloApi {
        guard let a = api() else { throw ApiError(status: 401, detail: "signed out") }
        return a
    }

    /// The goals kept from last time (once).
    func warm() {
        guard !keptLoaded else { return }
        keptLoaded = true
        if let kept: [GoalItem] = cache.get("goals") { state.goals = kept; state.loaded = true }
    }

    func load() { jobs.run { [self] in await read() } }

    /// A newer read cancels the older one, so an old list never covers what a change just made.
    func read() async {
        warm()
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                let api = try requireApi()
                let goals: [GoalItem] = try await api.get("/api/goals?include=work")
                if Task.isCancelled { return }
                cache.put("goals", goals)
                state.goals = goals; state.loaded = true; state.failed = false; state.offline = false
            } catch let e as ApiError {
                if Task.isCancelled { return }
                // with goals on screen a failed refresh is only a hint, not an error
                if state.loaded && e.isNetwork { state.offline = true } else { state.failed = true }
            } catch { if !Task.isCancelled { state.failed = true } }
            // an older server has no suggestions: nothing shows
            if !Task.isCancelled, let s: GoalSuggestions = try? await requireApi().get("/api/goals/suggestions") { state.suggestions = s.items }
            // an older server has no one list: the work section shows its own work items instead
            if !Task.isCancelled, let c: CarriedList = try? await requireApi().get("/api/carried") {
                state.carried = c.items; state.carriedKnown = true
            }
        }
        loading = task
        await task.value
    }

    func acceptSuggestion(_ s: GoalSuggestion, onAdded: @escaping () -> Void) {
        state.settling.insert(s.id)
        jobs.run { [self] in
            do {
                try await requireApi().request("POST", "/api/goals/suggestions/\(s.id)/accept")
                onAdded()
            } catch { state.failed = true }
            state.settling.remove(s.id)
            load()
        }
    }

    func dismissSuggestion(_ s: GoalSuggestion) {
        state.settling.insert(s.id)
        state.suggestions.removeAll { $0.id == s.id }
        jobs.run { [self] in
            _ = try? await requireApi().request("POST", "/api/goals/suggestions/\(s.id)/dismiss")
            state.settling.remove(s.id)
        }
    }

    func add(title: String, note: String, category: String, onAdded: @escaping () -> Void) {
        guard !title.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        call {
            try await $0.request("POST", "/api/goals", body: ["title": .string(title), "note": .string(note), "category": .string(category)])
            onAdded()
        }
    }

    func update(_ goal: GoalItem, title: String, note: String, category: String, onSaved: @escaping () -> Void) {
        guard !title.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        call {
            try await $0.request("PATCH", "/api/goals/\(goal.id)", body: ["title": .string(title), "note": .string(note), "category": .string(category)])
            onSaved()
        }
    }

    func toggle(_ goal: GoalItem) {
        let next = goal.isDone ? GoalItem.statusTracking : GoalItem.statusDone
        call { try await $0.request("PATCH", "/api/goals/\(goal.id)", body: ["status": .string(next)]) }
    }

    /// `pauseSchedules`: the user chose to turn the goal's linked schedules off with it (asked only when it has some on).
    func startRemove(_ goal: GoalItem, pauseSchedules: Bool = false) {
        state.removing.insert(goal.id)
        if pauseSchedules { state.pausing.insert(goal.id) } else { state.pausing.remove(goal.id) }
    }
    func undoRemove(_ goal: GoalItem) { state.removing.remove(goal.id); state.pausing.remove(goal.id) }

    func commitRemove(_ goal: GoalItem) {
        guard state.removing.contains(goal.id) else { return }   // undone meanwhile
        jobs.run { [self] in
            do {
                try await requireApi().request("DELETE", GoalsRules.deletePath(goal.id, deleteSchedules: state.pausing.contains(goal.id)))
                state.goals.removeAll { $0.id == goal.id }
            } catch { state.failed = true }
            state.removing.remove(goal.id)   // a failed delete brings the goal back
            state.pausing.remove(goal.id)
            load()
        }
    }

    /// Leaving the tab with an undo still on offer: delete what was swiped away.
    func commitAll() {
        for id in state.removing { if let g = state.goals.first(where: { $0.id == id }) { commitRemove(g) } }
    }

    private func call(_ block: @escaping (KeloApi) async throws -> Void) {
        jobs.run { [self] in
            do { try await block(requireApi()); load() } catch { state.failed = true }
        }
    }
}

/// One goal's page state (Android GoalDetailViewModel).
@MainActor @Observable
final class GoalDetailModel {
    let goalId: Int64
    var workspace: GoalWorkspace?
    /// The follow items of this work item, from the one list (GET /api/carried); empty for a goal
    var linkedFollow: [FollowItem] = []
    var failed = false
    /// Steps being ticked right now (shown ticked at once).
    var ticking: [Int64: String] = [:]
    /// The line shown after "Kelo에게 이어서 맡기기".
    var resumed: String?
    var resuming = false
    /// A page of older entries is on its way.
    var loadingLog = false
    @ObservationIgnored private let api: () -> KeloApi?
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(goalId: Int64, api: @escaping () -> KeloApi?) { self.goalId = goalId; self.api = api }

    private func requireApi() throws -> KeloApi {
        guard let a = api() else { throw ApiError(status: 401, detail: "signed out") }
        return a
    }

    func load() { jobs.run { [self] in await read() } }

    /// A newer read cancels the older one, so an old page never covers a tick just made.
    func read() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                let w: GoalWorkspace = try await requireApi().get("/api/goals/\(goalId)")
                if Task.isCancelled { return }
                workspace = GoalDetailRules.refreshed(prev: workspace, fresh: w)
                failed = false; ticking = [:]
                if let c: CarriedList = try? await requireApi().get("/api/carried") { linkedFollow = CarriedRules.followOf(c.items, goalId: goalId) }
            } catch { if !Task.isCancelled { failed = true } }
        }
        loading = task
        await task.value
    }

    /// The next page of older entries (GET /api/goals/{id}/log), added to the page once; no call while one runs.
    func loadMoreLog() {
        guard GoalDetailRules.canLoadMore(workspace: workspace, loading: loadingLog), let offset = workspace?.logNext else { return }
        loadingLog = true
        jobs.run { [self] in
            do {
                let page: GoalLogPage = try await requireApi().get("/api/goals/\(goalId)/log?offset=\(offset)")
                if let w = workspace { workspace = GoalDetailRules.withMoreLog(w, page) }
            } catch { failed = true }
            loadingLog = false
        }
    }

    func tick(_ step: GoalStep) {
        guard GoalDetailRules.canTick(step) else { return }
        let next = GoalDetailRules.toggled(step)
        ticking[step.id] = next
        jobs.run { [self] in
            do { try await requireApi().request("PATCH", "/api/goals/\(goalId)/steps/\(step.id)", body: ["status": .string(next)]) } catch { failed = true }
            load()
        }
    }

    func resume(onStarted: @escaping () -> Void) {
        guard !resuming else { return }
        resuming = true
        jobs.run { [self] in
            do {
                try await requireApi().request("POST", "/api/goals/\(goalId)/work")
                resumed = L(R.string.goal_resume_started)
                onStarted()
            } catch let e as ApiError {
                resumed = L(e.status == 409 ? R.string.goal_resume_no_model : R.string.connect_failed)
            } catch { resumed = L(R.string.connect_failed) }
            resuming = false
        }
    }
}
