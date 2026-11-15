import SwiftUI
import KeloCore
import KeloShared

/// The payments Kelo made with one card, newest first, older ones a page at a time; each opens its chat or task when known.
struct WalletCardHistory: View {
    @Environment(AppModel.self) private var app
    @State private var model: WalletCardHistoryModel
    private let cardId: String

    init(api: KeloApi, cardId: String) {
        self.cardId = cardId
        _model = State(initialValue: WalletCardHistoryModel(api: api, cardId: cardId))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let list = model.items {
                if list.isEmpty { EmptyNote(L(R.string.wallet_card_history_empty)).padding(.bottom, 16) }
                ForEach(list, id: \.id) { p in
                    row(p)
                    Hairline()
                }
                if model.next != nil && !list.isEmpty {
                    SecondaryButton(L(R.string.wallet_card_history_more), enabled: !model.loading, fill: true) { model.loadMore() }
                        .padding(.top, 10)
                        .accessibilityIdentifier("kelo.wallet.historyMore")
                }
            } else {
                SkeletonRows(rows: 2)
            }
            if !model.message.isEmpty { ErrorText(model.message, small: true) }
        }
        .task(id: cardId) { await model.load() }
    }

    private func row(_ p: WalletPayment) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            PaymentHead(p: p)
            MutedText(walletWhen(p.at), small: true)
            switch CardHistoryRules.link(p) {
            case .chat: LinkButton(L(R.string.wallet_open_chat)) { app.router.open(.chat) }
            case .task(let id): LinkButton(L(R.string.wallet_open_task)) { app.router.open(.work(.task(id))) }
            case .none: EmptyView()
            }
        }
        .padding(.vertical, 8)
    }
}
