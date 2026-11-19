import Foundation
import Observation
import KeloCore
import KeloShared

/// "Kelo가 챙기는 일" on the work page (Android follow/FollowViewModel.kt): GET /api/follow, stop and replan. An older server (404)
/// has none: the section stays hidden.
@MainActor @Observable
final class FollowModel {
    private(set) var items: [FollowItem] = []
    private(set) var loading = false
    private(set) var failed = false
    private(set) var stopping: Set<String> = []
    private(set) var replanning: Set<String> = []
    private(set) var removing: Set<String> = []
    @ObservationIgnored var api: () -> KeloApi?

    init(api: @escaping () -> KeloApi?) { self.api = api }

    func load() async {
        guard let api = api() else { return }
        loading = true
        defer { loading = false }
        do {
            items = try await api.get("/api/follow", as: FollowItemList.self).items
            failed = false
        } catch is CancellationError {
            return
        } catch let e as ApiError where e.status == 404 {
            items = []
            failed = false
        } catch {
            failed = true
        }
    }

    /// POST /api/follow/{key}/stop; then the list is read again.
    func stop(_ key: String) async {
        guard !stopping.contains(key), let api = api() else { return }
        stopping.insert(key)
        defer { stopping.remove(key) }
        if let r = try? await api.post("/api/follow/\(KeloApi.segment(key))/stop", as: FollowActionResponse.self) {
            set(key, state: r.state.isEmpty ? "stopped" : r.state)
            await load()
        }
    }

    /// POST /api/follow/{key}/replan; then the list is read again.
    func replan(_ key: String) async {
        guard !replanning.contains(key), let api = api() else { return }
        replanning.insert(key)
        defer { replanning.remove(key) }
        if let r = try? await api.post("/api/follow/\(KeloApi.segment(key))/replan", as: FollowActionResponse.self) {
            set(key, state: r.state.isEmpty ? "watching" : r.state)
            await load()
        }
    }

    /// DELETE /api/follow/{key} ("목록에서 지우기"): the row leaves the list (the next load reads the server's list again).
    func remove(_ key: String) async {
        guard !removing.contains(key), let api = api() else { return }
        removing.insert(key)
        defer { removing.remove(key) }
        if (try? await api.delete("/api/follow/\(KeloApi.segment(key))", as: FollowActionResponse.self)) != nil {
            items = items.filter { $0.key != key }
        }
    }

    private func set(_ key: String, state: String) {
        items = items.map { var x = $0; if x.key == key { x.state = state }; return x }
    }
}
