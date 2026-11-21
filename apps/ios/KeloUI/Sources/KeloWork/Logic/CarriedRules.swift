import Foundation
import KeloCore

/// One row of the one list of what Kelo carries (GET /api/carried; Android goals/CarriedModels.kt): a work item with the follow items
/// of the same thing under it (`follow`), or a follow item on its own (`key` "follow:<key>", `follow` holds just that item).
struct CarriedItem: Decodable, Equatable, Identifiable, Sendable {
    /// "work:<id>" or "follow:<key>"
    var key: String
    var title: String
    /// open | done | planned | watching | stopped (a follow item's own state)
    var state: String
    var startsAt: Double?
    var allDay: Bool
    /// The work item (a work row), else nil
    var work: GoalItem?
    var follow: [FollowItem]

    var id: String { key }
    var isWork: Bool { key.hasPrefix("work:") }

    init(key: String, title: String, state: String, startsAt: Double?, allDay: Bool, work: GoalItem?, follow: [FollowItem]) {
        self.key = key; self.title = title; self.state = state; self.startsAt = startsAt; self.allDay = allDay; self.work = work; self.follow = follow
    }

    private enum CodingKeys: String, CodingKey { case key, title, state, startsAt = "starts_at", allDay = "all_day", work, follow }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decodeIfPresent(String.self, forKey: .key) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        state = try c.decodeIfPresent(String.self, forKey: .state) ?? "open"
        startsAt = try c.decodeIfPresent(Double.self, forKey: .startsAt)
        allDay = try c.decodeIfPresent(Bool.self, forKey: .allDay) ?? false
        work = try c.decodeIfPresent(GoalItem.self, forKey: .work)
        follow = try c.decodeIfPresent([FollowItem].self, forKey: .follow) ?? []
    }
}

/// GET /api/carried: `{"items": [CarriedItem]}`.
struct CarriedList: Decodable, Equatable, Sendable {
    var items: [CarriedItem] = []

    init(items: [CarriedItem] = []) { self.items = items }

    private enum CodingKeys: String, CodingKey { case items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        items = try c.decodeIfPresent([CarriedItem].self, forKey: .items) ?? []
    }
}

/// The one list's rules, apart from the UI.
enum CarriedRules {
    /// A row still to do: not done and not stopped.
    static func isOpen(_ i: CarriedItem) -> Bool { i.state != "done" && i.state != "stopped" }

    /// The open rows, the nearest date first and the undated ones after them; the done and stopped rows last.
    static func ordered(_ items: [CarriedItem]) -> [CarriedItem] {
        let live = items.filter(isOpen)
        let dated = live.filter { $0.startsAt != nil }.sorted { ($0.startsAt ?? 0) < ($1.startsAt ?? 0) }
        let undated = live.filter { $0.startsAt == nil }
        return dated + undated + items.filter { !isOpen($0) }
    }

    /// A work row's follow items; a follow row's own item.
    static func linked(_ i: CarriedItem) -> [FollowItem] { i.isWork ? i.follow : Array(i.follow.prefix(1)) }

    /// The follow items of the work item `goalId`, from the one list.
    static func followOf(_ items: [CarriedItem], goalId: Int64) -> [FollowItem] {
        items.first { $0.key == "work:\(goalId)" }?.follow ?? []
    }
}
