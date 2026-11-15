import SwiftUI
import KeloCore
import KeloShared

// The view behind the wallet lock (Android wallet/WalletPage.kt WalletOpen): loads the wallet and shows the page that is current.
// The pages themselves live in their own Wallet*Page.swift files.

/// Everything behind the lock. The data lives only as long as the pages are open; the page that was open comes back after a lock.
struct WalletOpenView: View {
    let onBack: () -> Void
    let touch: () -> Void
    let api: KeloApi
    @State private var wallet: WalletModel
    @State private var view: WalletView = WalletPlaces.currentView()

    init(api: KeloApi, keyguard: @escaping @MainActor () async -> Bool?, onBack: @escaping () -> Void, touch: @escaping () -> Void) {
        self.api = api; self.onBack = onBack; self.touch = touch
        _wallet = State(initialValue: WalletModel(api: api, keyguard: keyguard))
    }

    var body: some View {
        Group {
            if let v = wallet.info {
                page(v)
            } else {
                SubPage(L(R.string.wallet_title), onBack: onBack) {
                    if !wallet.failed.isEmpty { ErrorText(wallet.failed, small: true) } else { SkeletonRows(rows: 3) }
                }
            }
        }
        .simultaneousGesture(TapGesture().onEnded { touch(); WalletPlaces.touch() })
        .task { await wallet.loadAll() }
    }

    private func go(_ v: WalletView) { view = v; WalletPlaces.go(v); touch() }
    private func back() { if let b = view.back { go(b) } else { onBack() } }

    @ViewBuilder private func page(_ v: WalletInfo) -> some View {
        let keyId = WalletKey.keyId()
        switch view {
        case .list:
            WalletListPage(info: v, keyId: keyId, notices: wallet.notices, onBack: onBack, go: go, wallet: wallet)
        case .add:
            WalletAddPage(api: api, onBack: { go(.list) }, onSaved: { wallet.reload(); go(.list) })
        case .card(let id):
            if let card = v.cards.first(where: { $0.id == id }) {
                WalletCardPage(api: api, card: card, keyId: keyId, onBack: { go(.list) }, go: go,
                               wallet: wallet, onDeleted: { go(.list) }, cards: v.cards)
            } else {
                WalletListPage(info: v, keyId: keyId, notices: wallet.notices, onBack: onBack, go: go, wallet: wallet)
            }
        case .reenter(let id, let autoPay):
            if let card = v.cards.first(where: { $0.id == id }) {
                WalletReenterPage(api: api, card: card, autoPay: autoPay, onBack: { go(.card(id)) },
                                  onSaved: { wallet.reload(); go(.card(id)) })
            } else {
                WalletListPage(info: v, keyId: keyId, notices: wallet.notices, onBack: onBack, go: go, wallet: wallet)
            }
        case .limits:
            WalletLimitsPage(api: api, wallet: wallet, paused: v.paused, onBack: back)
        case .payments:
            WalletPaymentsPage(api: api, onBack: back)
        case .address(let id):
            if let id, let profile = v.shipping.first(where: { $0.id == id }) {
                ShippingEditPage(api: api, profile: profile, others: v.shipping.count - 1, onBack: { go(.list) },
                                 onSaved: { wallet.reload(); go(.list) })
                    .id("address:" + id)
            } else if id == nil {
                ShippingEditPage(api: api, profile: nil, others: v.shipping.count, onBack: { go(.list) },
                                 onSaved: { wallet.reload(); go(.list) })
                    .id("address:new")
            } else {
                WalletListPage(info: v, keyId: keyId, notices: wallet.notices, onBack: onBack, go: go, wallet: wallet)
            }
        }
    }
}
