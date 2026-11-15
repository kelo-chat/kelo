import Foundation

/// What the user allowed for a running task as a whole (GET /api/task-permissions items; Android agent/AgentModels.kt
/// TaskPermission): on `site`, the step `action`, up to `maxCount` times, `used` so far, until the task ends.
public struct TaskPermission: Equatable, Sendable, Identifiable {
    public var id: String
    public var site: String
    public var kind: String
    public var action: String
    public var maxCount: Int
    public var used: Int
    public var taskId: Int64?
    public var expiresAt: Double

    public init(id: String, site: String = "", kind: String = "", action: String = "", maxCount: Int = 0, used: Int = 0,
                taskId: Int64? = nil, expiresAt: Double = 0) {
        self.id = id; self.site = site; self.kind = kind; self.action = action; self.maxCount = maxCount; self.used = used
        self.taskId = taskId; self.expiresAt = expiresAt
    }

    public static func parse(_ e: JSON) -> TaskPermission? {
        guard e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let id = o.str("id")
        guard !id.isEmpty else { return nil }
        return TaskPermission(id: id, site: o.str("site"), kind: o.str("kind"), action: o.str("action"), maxCount: o.int("max_count"),
                              used: o.int("used"), taskId: o.optInt64("task_id"), expiresAt: o.double("expires_at"))
    }

    /// {"items": [...]} or a bare list.
    public static func parseList(_ e: JSON) -> [TaskPermission] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap(parse)
    }
}

public extension KeloApi {
    /// GET /api/task-permissions; an older server without it (404) has none.
    func taskPermissions() async throws -> [TaskPermission] {
        do { return TaskPermission.parseList(try await request("GET", "/api/task-permissions")) }
        catch let e as ApiError where e.status == 404 { return [] }
    }

    /// DELETE /api/task-permissions/{id}: the task's next such step asks again.
    func revokeTaskPermission(_ id: String) async throws { _ = try await request("DELETE", "/api/task-permissions/\(KeloApi.segment(id))") }

    /// GET /api/approvals/pending with its "resolved" (hand-overs that ended on the server).
    func pendingApprovalsWithResolved() async throws -> ([PendingApproval], [String: String]) {
        let (p, r, _) = try await pendingApprovalsFull()
        return (p, r)
    }

    /// The same, plus the location cards waiting (items of type location_request: not approvals; listed so a reloaded chat shows a
    /// waiting one again).
    func pendingApprovalsFull() async throws -> ([PendingApproval], [String: String], [TurnEvent.LocationRequest]) {
        let j = try await request("GET", "/api/approvals/pending")
        let locations: [TurnEvent.LocationRequest] = (j["items"]?.arrayValue ?? []).compactMap {
            if case .locationRequest(let l) = TurnEvent.parse($0) { return l }
            return nil
        }
        return (PendingApproval.parseList(j), PendingApproval.parseResolved(j), locations)
    }
}
