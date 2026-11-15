import SwiftUI
import KeloCore
import KeloShared

/// 결제 내역: every payment that went ahead, newest first, automatic or allowed on the phone.
struct WalletPaymentsPage: View {
    let onBack: () -> Void
    @State private var model: WalletPaymentsModel

    init(api: KeloApi, onBack: @escaping () -> Void) {
        self.onBack = onBack
        _model = State(initialValue: WalletPaymentsModel(api: api))
    }

    var body: some View {
        SubPage(L(R.string.wallet_history_title), onBack: onBack) {
            if let list = model.items {
                if !model.message.isEmpty { ErrorText(model.message, small: true) }
                if list.isEmpty { EmptyNote(L(R.string.wallet_history_empty)) }
                ForEach(list, id: \.id) { p in
                    VStack(alignment: .leading, spacing: 4) {
                        PaymentHead(p: p)
                        MutedText(p.cardLabel.isEmpty ? walletWhen(p.at) : L(R.string.wallet_history_meta, p.cardLabel, walletWhen(p.at)), small: true)
                    }
                    .padding(.vertical, 8)
                    Hairline()
                }
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task { await model.load() }
    }
}
