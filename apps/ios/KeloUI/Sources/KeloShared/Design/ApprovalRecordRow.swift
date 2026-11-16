import SwiftUI
import KeloCore

/// One approval in a history: what, where, when, and how it was answered. `padded`: the work page's list draws it in a card
/// (horizontal padding), the permissions page in its own list.
public struct ApprovalRecordRow: View {
    let a: ApprovalRecord
    let when: String
    var padded = false
    @Environment(\.kelo) private var c

    public init(a: ApprovalRecord, when: String, padded: Bool = false) { self.a = a; self.when = when; self.padded = padded }

    /// How an approval was answered, in words.
    public static func decision(_ d: String) -> String {
        switch d {
        case "allowed_once": L(R.string.agent_decision_once)
        case "allowed_always": L(R.string.agent_decision_always)
        case "allowed_by_rule": L(R.string.agent_decision_rule)
        case "denied": L(R.string.agent_decision_denied)
        case "expired": L(R.string.agent_decision_expired)
        case "cancelled": L(R.string.agent_decision_cancelled)
        default: L(R.string.agent_decision_pending)
        }
    }

    public var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(a.label.isEmpty ? a.tool : a.label).font(KeloFont.body).foregroundStyle(c.ink).lineLimit(3)
                let sub = [a.site, a.detail].filter { !$0.isEmpty }.joined(separator: " · ")
                if !sub.isEmpty { Text(sub).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(3) }
                if !when.isEmpty { Text(when).font(KeloFont.label).foregroundStyle(c.muted) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            KeloBadge(Self.decision(a.decision), denied: a.decision == "denied")
        }
        .padding(.horizontal, padded ? 16 : 0).padding(.vertical, padded ? 12 : 10)
    }
}
