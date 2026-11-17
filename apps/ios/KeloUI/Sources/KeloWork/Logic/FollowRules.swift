import Foundation
import KeloCore
import KeloShared

/// "Kelo가 챙기는 일" (Android follow/FollowRules.kt): the one line of what Kelo does for an item, and the words for its states.
enum FollowRules {
    static func isDayLead(_ offset: String) -> Bool { offset == "-1d" || offset == "-24h" }

    /// The hours of a "-3h" / "+3h" offset (3 when it cannot be read).
    static func leadHours(_ offset: String) -> Int {
        Int(offset.trimmingCharacters(in: CharacterSet(charactersIn: "+-")).trimmingCharacters(in: CharacterSet(charactersIn: "hH"))) ?? 3
    }

    /// One lead offset in words: "출발 하루 전 알림", "하루 전 알림" or "3시간 전 알림"; nil for an offset that is not read.
    @MainActor static func leadText(_ offset: String, kind: String) -> String? {
        if isDayLead(offset) {
            return L(["flight", "trip"].contains(kind) ? R.string.follow_summary_lead_flight : R.string.follow_summary_lead_general)
        }
        if offset.lowercased().hasSuffix("h") { return L(R.string.follow_summary_lead_hours, leadHours(offset)) }
        return nil
    }

    static func preps(_ item: FollowItem) -> [FollowPrep] { item.prep.isEmpty ? (item.plan?.prep ?? []) : item.prep }

    /// "출발 하루 전 알림 · 체크인 열리면 준비": lead notice, then the preparation, else what is watched.
    @MainActor static func planSummary(_ item: FollowItem) -> String {
        var parts: [String] = []
        let tell = item.plan?.tell
        let lead = tell?.lead ?? []
        if lead.contains(where: isDayLead) {
            parts.append(leadText("-1d", kind: item.kind) ?? "")
        } else if let h = lead.first(where: { $0.lowercased().hasSuffix("h") && $0 != "-24h" }) {
            parts.append(L(R.string.follow_summary_lead_hours, leadHours(h)))
        }
        let all = preps(item)
        if all.contains(where: { $0.kind == "check_in" }) {
            parts.append(L(R.string.follow_summary_prep_checkin))
        } else if let first = all.first?.what, !first.isEmpty {
            parts.append(L(R.string.follow_summary_prep_general, first))
        }
        if parts.isEmpty {
            parts.append(!item.checks.isEmpty || tell?.onChange == true ? L(R.string.follow_summary_watch_changes) : L(R.string.follow_summary_default))
        }
        return parts.joined(separator: " · ")
    }

    @MainActor static func prepStateText(_ state: String) -> String {
        switch state {
        case "running": L(R.string.follow_prep_running)
        case "done": L(R.string.follow_prep_done)
        case "failed": L(R.string.follow_prep_failed)
        default: L(R.string.follow_prep_later)
        }
    }

    @MainActor static func itemStateText(_ state: String) -> String {
        switch state {
        case "stopped": L(R.string.follow_state_stopped)
        case "done": L(R.string.follow_state_done)
        default: L(R.string.follow_state_watching)
        }
    }

    /// The earliest next time among the active checks.
    static func nextCheck(_ item: FollowItem) -> Double? {
        item.checks.filter { $0.state == "active" }.compactMap(\.nextAt).min()
    }
}
