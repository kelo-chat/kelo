import Foundation
import Observation
import KeloCore
import KeloShared

/// GET/POST /api/goals, PATCH/DELETE /api/goals/{id}, and the suggestions (Android goals/GoalsViewModel.kt). The goals kept from
/// last time show at once and are refreshed in place; a goal swiped away is hidden and deleted only when its undo offer is gone.
@MainActor @Observable
final class GoalsModel {
    var state = GoalsState()
    @ObservationIgnored var app: AppModel?
    @ObservationIgnored private var keptLoaded = false

    private func requireApi() throws -> KeloApi {
        guard let a = app?.api else { throw ApiError(status: 401, detail: "signed out") }
        return a
    }

    /// The goals kept from last time (once).
    func warm() {
        guard !keptLoaded, let app else { return }
        keptLoaded = true
        if let kept: [GoalItem] = app.cache.get("goals") { state.goals = kept; state.loaded = true }
    }

    func load() {
        warm()
        Task {
            do {
                let api = try requireApi()
                let goals: [GoalItem] = try await api.get("/api/goals")
                app?.cache.put("goals", goals)
                state.goals = goals; state.loaded = true; state.failed = false; state.offline = false
            } catch let e as ApiError {
                // with goals on screen a failed refresh is only a hint, not an error
                if state.loaded && e.isNetwork { state.offline = true } else { state.failed = true }
            } catch { state.failed = true }
            // an older server has no suggestions: nothing shows
            if let s: GoalSuggestions = try? await requireApi().get("/api/goals/suggestions") { state.suggestions = s.items }
        }
    }

    func acceptSuggestion(_ s: GoalSuggestion, onAdded: @escaping () -> Void) {
        state.settling.insert(s.id)
        Task {
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
        Task {
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
        Task {
            do {
                try await requireApi().request("DELETE", GoalsRules.deletePath(goal.id, pauseSchedules: state.pausing.contains(goal.id)))
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
        Task {
            do { try await block(requireApi()); load() } catch { state.failed = true }
        }
    }
}

/// One goal's page state (Android GoalDetailViewModel).
@MainActor @Observable
final class GoalDetailModel {
    let goalId: Int64
    var workspace: GoalWorkspace?
    var failed = false
    /// Steps being ticked right now (shown ticked at once).
    var ticking: [Int64: String] = [:]
    /// The line shown after "Kelo에게 이어서 맡기기".
    var resumed: String?
    var resuming = false
    @ObservationIgnored var app: AppModel?

    init(goalId: Int64) { self.goalId = goalId }

    private func requireApi() throws -> KeloApi {
        guard let a = app?.api else { throw ApiError(status: 401, detail: "signed out") }
        return a
    }

    func load() {
        Task {
            do {
                workspace = try await requireApi().get("/api/goals/\(goalId)")
                failed = false; ticking = [:]
            } catch { failed = true }
        }
    }

    func tick(_ step: GoalStep) {
        guard GoalDetailRules.canTick(step) else { return }
        let next = GoalDetailRules.toggled(step)
        ticking[step.id] = next
        Task {
            do { try await requireApi().request("PATCH", "/api/goals/\(goalId)/steps/\(step.id)", body: ["status": .string(next)]) } catch { failed = true }
            load()
        }
    }

    func resume(onStarted: @escaping () -> Void) {
        guard !resuming else { return }
        resuming = true
        Task {
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
