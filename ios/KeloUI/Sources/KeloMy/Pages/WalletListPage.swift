import SwiftUI
import KeloCore
import KeloShared

/// New auto-payments, the cards as small faces, and the way to the limits and the history; how cards are kept safe is behind (i).
struct WalletListPage: View {
    let info: WalletInfo
    let keyId: String?
    let notices: [WalletPayment]
    let onBack: () -> Void
    let go: (WalletView) -> Void
    let wallet: WalletModel
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var explain = false
    @State private var turnOnFor: WalletCard?   // a card whose row's "turn on auto-pay" was tapped

    var body: some View {
        SubPage(L(R.string.wallet_title), onBack: onBack, actions: {
            Button { explain = true } label: { KeloIconView(.info, size: 22, tint: c.ink).frame(width: 44, height: 44) }
                .buttonStyle(.plain)
                .accessibilityLabel(L(R.string.wallet_safe_title))
        }) {
            if !notices.isEmpty {
                MyCard {
                    HStack(spacing: 8) {
                        KeloIconView(.card, size: 20, tint: c.success)
                        Text(P(R.plurals.wallet_notices_title, notices.count, notices.count)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(notices.prefix(5), id: \.id) { p in
                        Text(L(R.string.wallet_notice_line, p.site.isEmpty ? p.host : p.site, p.amountText.isEmpty ? walletMoney(p.amount, p.currency) : p.amountText,
                               p.cardLabel, walletWhen(p.at)))
                            .font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            if info.paused {
                MyCard(action: { go(.limits) }) {
                    Text(L(R.string.wallet_paused_banner)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
            }
            if info.cards.isEmpty { MutedText(L(R.string.wallet_no_cards), small: true) }
            VStack(spacing: 0) {
                ForEach(info.cards, id: \.id) { card in row(card) }
            }
            SectionLabel(L(R.string.wallet_auto_section)).padding(.horizontal, -16)
            MenuGroup {
                // how many cards really pay on their own now: never just "on" while every card asks
                MenuRow(.shield, L(R.string.wallet_limits_title), value: autoValue) { go(.limits) }
                MenuRow(.receipt, L(R.string.wallet_history_title)) { go(.payments) }
            }
            if WalletAutoSummary.showsNoneNote(info) { MutedText(L(R.string.wallet_auto_none_note), small: true) }
            if WalletAutoSummary.showsFirstSiteRule(info) && !keyguardPause { MutedText(L(R.string.wallet_first_site_rule), small: true) }
            if !info.scheduleGrants.isEmpty {
                SectionLabel(L(R.string.wallet_schedule_section)).padding(.horizontal, -16)
                ScheduleGrantRows(info.scheduleGrants, showSchedule: true) { g in wallet.revoke(g) }
                MutedText(L(R.string.grant_section_note), small: true)
            }
            PrimaryButton(L(R.string.wallet_add_title)) { go(.add) }
                .accessibilityIdentifier("kelo.wallet.add")
            // the addresses orders go to, next to the cards that pay for them
            ShippingSection(profiles: info.shipping, go: go)
        }
        .alert(L(R.string.wallet_safe_title), isPresented: $explain) {
            Button(L(R.string.ok), role: .cancel) {}
        } message: {
            Text(L(R.string.wallet_safe_body) + "\n\n" + L(R.string.wallet_relock_note))
        }
        // the notice, then this phone's own copy opened with Face ID / the passcode (no retyping)
        .modifier(WalletAutoPayFlow(asking: $turnOnFor, wallet: wallet, go: go))
        .task { await app.refreshKeyguard() }
    }

    /// Auto-pay works only with Keyguard on: with it off, the auto-pay cards show as paused (owner's decision).
    private var keyguardPause: Bool {
        app.keyguardOn == false && info.cards.contains { AutoPayGate.pausedForKeyguard($0, keyguardOn: app.keyguardOn) }
    }

    private var autoValue: String {
        if keyguardPause { return L(R.string.wallet_auto_paused_value) }
        switch WalletAutoSummary.of(info) {
        case .paused: return L(R.string.wallet_auto_paused_value)
        case .none: return L(R.string.wallet_auto_none_value)
        case .cards(let n): return P(R.plurals.wallet_auto_cards_value, n, n)
        }
    }

    /// The card's row, and under it, for a card saved to ask each time, the way to turn auto-pay on (this phone's own copy).
    private func row(_ card: WalletCard) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            rowButton(card)
            if card.offersTurnOn {
                LinkButton(L(R.string.wallet_auto_turn_on_card)) { turnOnFor = card }
                    .padding(.leading, 4 + 52 + 14).padding(.top, -6).padding(.bottom, 6)
                    .accessibilityIdentifier("kelo.wallet.turn-on.\(card.id)")
            }
        }
    }

    private func rowButton(_ card: WalletCard) -> some View {
        Button { go(.card(card.id)) } label: {
            HStack(spacing: 14) {
                WalletCardThumb(brand: CardBrand.of(card.brand), last4: card.last4, dimmed: card.expired)
                VStack(alignment: .leading, spacing: 3) {
                    Text(card.label.isEmpty ? L(R.string.wallet_card_masked, card.last4) : card.label).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                        .lineLimit(2)
                    if let sub = walletCardSubtitle(card) {
                        Text(sub).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    }
                    Text(L(R.string.wallet_card_meta, Expiry.display(month: card.expMonth, year: card.expYear), walletWhen(card.lastUsed)))
                        .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    if let line = walletAutoLine(card, keyguardOn: app.keyguardOn) {
                        Text(line).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    WalletStatusTags(card: card, keyId: keyId, keyguardOn: app.keyguardOn)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                KeloIconView(.chevronRight, size: 16, tint: c.muted)
            }
            .padding(.horizontal, 4).padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("kelo.wallet.card.\(card.id)")
    }
}
