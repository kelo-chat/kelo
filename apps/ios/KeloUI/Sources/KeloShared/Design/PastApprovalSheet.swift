import SwiftUI
import KeloCore

/// One past approval (Android PastApprovalSheet): the question, how it was answered, where, when, who answered, and whether the owner
/// fixed the message first.
public struct PastApprovalSheet: View {
    let a: ApprovalRecord
    let onClose: () -> Void
    @Environment(\.kelo) private var c

    public init(a: ApprovalRecord, onClose: @escaping () -> Void) { self.a = a; self.onClose = onClose }

    /// Who answered, when a person did (a rule's allow or a run-out has none).
    static func answeredBy(_ via: String) -> String? {
        switch via {
        case "app": L(R.string.approval_via_app)
        case "notification": L(R.string.approval_via_notification)
        case "messenger": L(R.string.approval_via_messenger)
        default: nil
        }
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                Text(a.label.isEmpty ? a.tool : a.label).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                KeloBadge(ApprovalRecordRow.decision(a.decision), denied: a.decision == "denied")
            }
            if !a.detail.isEmpty { Text(a.detail).font(KeloFont.body).foregroundStyle(c.ink) }
            let where_ = a.place.isEmpty ? a.site : a.place
            if !where_.isEmpty { pastLine(L(R.string.approval_history_where), where_) }
            if a.askedAt > 0 { pastLine(L(R.string.approval_history_when), MyFormat.dayTime(a.askedAt)) }
            if let who = Self.answeredBy(a.via) { pastLine(L(R.string.approval_history_answered), who) }
            if a.edited { Text(L(R.string.approval_edited_note)).font(KeloFont.small).foregroundStyle(c.muted) }
            LinkButton(L(R.string.close), action: onClose)
        }
        .padding(20)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func pastLine(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label).font(KeloFont.small).foregroundStyle(c.muted).frame(minWidth: 64, alignment: .leading)
            Text(value).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
