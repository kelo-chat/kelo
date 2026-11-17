import SwiftUI
import KeloCore
import KeloShared

/// 마이 → Forgotten topics: each topic with its date and what was removed, and "Allow again" (after a short confirm).
struct ForgetPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: ForgetModel
    @State private var asking: ForgottenTopic?

    init(api: KeloApi) { _model = State(initialValue: ForgetModel(api: api)) }

    var body: some View {
        SubPage(L(R.string.forget_title), onBack: { dismiss() }) {
            MutedText(L(R.string.forget_intro), small: true)
            if let items = model.items {
                if items.isEmpty {
                    EmptyNote(L(R.string.forget_empty))
                } else {
                    MenuCard {
                        ForEach(Array(items.enumerated()), id: \.element.id) { i, t in
                            if i > 0 { Hairline() }
                            row(t)
                        }
                    }
                }
            } else if model.failed {
                ErrorText(L(R.string.connect_failed), small: true)
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
        .keloConfirm(asking.map { L(R.string.forget_allow_confirm, TokenText.fit($0.label, max: 40)) } ?? "",
                     isPresented: Binding(get: { asking != nil }, set: { if !$0 { asking = nil } }),
                     yes: L(R.string.forget_allow_again), danger: false) {
            if let t = asking { model.allowAgain(t) }
        }
        .toast(model.notice, clear: model.clearNotice)
    }

    private func row(_ t: ForgottenTopic) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(TokenText.fit(t.label, max: 40)).font(KeloFont.bodyLarge.weight(.medium)).foregroundStyle(c.ink).lineLimit(3)
            if t.createdAt > 0 { Text(L(R.string.forget_date, MyFormat.date(t.createdAt, style: .long))).font(KeloFont.small).foregroundStyle(c.muted) }
            Text(ForgetModel.removedLine(t)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            SecondaryButton(L(R.string.forget_allow_again), enabled: !model.busy.contains(t.id), fill: true) { asking = t }
                .padding(.top, 4)
        }
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityIdentifier("kelo.forget.row")
    }
}
