import Foundation
import KeloCore

/// "8시간 뒤" / "3분 전" / "곧" / "방금": a time distance in the app's words (the work page's and the approval cards').
@MainActor public enum RelWords {
    public static func of(_ seconds: Double, now: Double = Date().timeIntervalSince1970) -> String {
        let r = RelTime.of(seconds, now: now)
        let n = Int(r.amount)
        switch r.unit {
        case .now: return L(r.future ? R.string.agent_rel_soon : R.string.agent_rel_now)
        case .minutes: return P(r.future ? R.plurals.agent_rel_minutes_future : R.plurals.agent_rel_minutes_past, n, n)
        case .hours: return P(r.future ? R.plurals.agent_rel_hours_future : R.plurals.agent_rel_hours_past, n, n)
        case .days: return P(r.future ? R.plurals.agent_rel_days_future : R.plurals.agent_rel_days_past, n, n)
        }
    }
}
