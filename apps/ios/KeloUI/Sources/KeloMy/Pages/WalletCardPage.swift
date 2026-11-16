import SwiftUI
import KeloCore
import KeloShared

/// One card: its nickname (the title) with brand and last 4, its state, whether it is the default card, auto-pay on or off, the
/// payments Kelo made with it, and removing it. The auto-pay limits live on the wallet list, not here.
struct WalletCardPage: View {
    let api: KeloApi
    let card: WalletCard
    let keyId: String?
    let onBack: () -> Void
    let go: (WalletView) -> Void
    let wallet: WalletModel
    let onDeleted: () -> Void
    var cards: [WalletCard] = []
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var renaming = false
    @State private var deleting = false
    @State private var pickingDefault = false   // deleting the default card with two or more others: which one takes over
    @State private var turningOff = false
    @State private var turningOn: WalletCard?
    @State private var keyguardForPaused = false

    var body: some View {
        let status = CardStatus.of(card, keyId: keyId)
        let title = card.label.isEmpty ? L(R.string.wallet_card_masked, card.last4) : card.label
        SubPage(title, onBack: onBack) {
            WalletCardFace(brand: CardBrand.of(card.brand), number: L(R.string.wallet_card_masked, CardMask.last4(card.last4)),
                           holder: card.holder, expiry: Expiry.display(month: card.expMonth, year: card.expYear))
                .padding(.horizontal, 8)
            if let sub = walletCardSubtitle(card) {
                Text(sub).font(KeloFont.body).foregroundStyle(c.muted).frame(maxWidth: .infinity)
            }
            MyCard {
                WalletStatusTags(card: card, keyId: keyId, keyguardOn: app.keyguardOn)
                MutedText(L(R.string.wallet_card_dates, walletWhen(card.createdAt), walletWhen(card.lastUsed)), small: true)
            }
            switch status {
            case .notOnPhone, .unfinished:
                MyCard {
                    ErrorText(L(R.string.wallet_not_here_body), small: true)
                    SecondaryButton(L(R.string.wallet_reenter_title), fill: true) { go(.reenter(card.id, autoPay: false)) }
                }
            case .autoOnly:
                MyCard {
                    MutedText(L(R.string.wallet_auto_only_body), small: true)
                    SecondaryButton(L(R.string.wallet_reenter_title), fill: true) { go(.reenter(card.id, autoPay: false)) }
                }
            case .expired:
                MyCard { ErrorText(L(R.string.wallet_expired_body), small: true) }
            case .usable:
                EmptyView()
            }
            MyCard {
                ToggleRow(L(R.string.wallet_auto_title), isOn: Binding(get: { card.autoPay }, set: { on in
                    if on { turningOn = card } else { turningOff = true }
                }), enabled: !card.expired)
                MutedText(L(card.autoPay ? R.string.wallet_auto_on_body : R.string.wallet_auto_off_body), small: true)
                // an auto-pay card on a server without Keyguard: paused (each payment asks on the phone) until Keyguard is on
                if AutoPayGate.pausedForKeyguard(card, keyguardOn: app.keyguardOn) {
                    Text(L(R.string.wallet_auto_paused_keyguard)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    SecondaryButton(L(R.string.keyguard_turn_on_named), fill: true) { keyguardForPaused = true }
                        .accessibilityIdentifier("kelo.wallet.keyguardOn")
                }
                if !card.autoNow && card.canPay {
                    // why this card asks each time, and the way to change it (this phone opens its own copy; no retyping)
                    Text(walletReasonText(card.reason)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    if !card.autoPay {
                        SecondaryButton(L(R.string.wallet_auto_turn_on_card), fill: true) { turningOn = card }
                            .accessibilityIdentifier("kelo.wallet.autoOn")
                        MutedText(L(R.string.wallet_auto_from_phone), small: true)
                    }
                }
                MutedText(L(R.string.wallet_first_site_rule), small: true)
                MutedText(L(R.string.wallet_auto_tip), small: true)
            }
            MyCard {
                HStack(spacing: 8) {
                    Text(L(R.string.wallet_default_title)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if card.isDefault { WalletTag(text: L(R.string.wallet_tag_default), tone: .accent) }
                }
                MutedText(L(R.string.wallet_default_on), small: true)
                if !card.isDefault {
                    SecondaryButton(L(R.string.wallet_make_default), fill: true) { wallet.patchCard(card.id, ["default": .bool(true)]) }
                    .accessibilityIdentifier("kelo.wallet.makeDefault")
                }
            }
            MenuGroup {
                MenuRow(.edit, L(R.string.wallet_nickname), value: card.label) { renaming = true }
            }
            SectionLabel(L(R.string.wallet_card_history)).padding(.horizontal, -16)
            WalletCardHistory(api: api, cardId: card.id)
            MenuGroup {
                MenuRow(.trash, L(R.string.wallet_delete), danger: true) {
                    if DefaultCardRules.choicesOnDelete(card, cards) != nil { pickingDefault = true } else { deleting = true }
                }
            }
            if !wallet.message.isEmpty { ErrorText(wallet.message, small: true) }
        }
        .alert(L(status == .usable ? R.string.wallet_auto_off_confirm : R.string.wallet_auto_off_confirm_unusable), isPresented: $turningOff) {
            Button(L(R.string.wallet_auto_turn_off), role: status == .usable ? nil : .destructive) {
                wallet.patchCard(card.id, ["auto_pay": .bool(false)])
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .alert(L(R.string.wallet_delete_confirm, card.label.isEmpty ? card.last4 : card.label), isPresented: $deleting) {
            Button(L(R.string.delete), role: .destructive) {
                wallet.deleteCard(card.id, then: onDeleted)
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .modifier(WalletAutoPayFlow(asking: $turningOn, wallet: wallet, go: go))
        .keyguardNeeded($keyguardForPaused) { wallet.reload() }
        .task { await app.refreshKeyguard() }
        .onAppear { wallet.clearMessage() }
        .keloSheet(isPresented: $pickingDefault, detents: [.medium, .large]) {
            WalletNewDefaultSheet(card: card, choices: DefaultCardRules.choicesOnDelete(card, cards) ?? [],
                                  onCancel: { pickingDefault = false }) { next in
                pickingDefault = false
                wallet.deleteCard(card.id, newDefault: next, then: onDeleted)
            }
        }
        .keloSheet(isPresented: $renaming, detents: [.medium, .large]) {
            WalletRenameSheet(current: card.label, onCancel: { renaming = false }) { name in
                renaming = false
                wallet.patchCard(card.id, ["label": .string(name.trimmingCharacters(in: .whitespaces))])
            }
        }
    }
}
