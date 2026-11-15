import SwiftUI
import CryptoKit
import KeloCore
import KeloShared

// The card pages behind the lock (Android wallet/WalletScreens.kt, CardForms.kt, WalletPage.kt WalletOpen): the list, one card,
// adding a card in steps, re-entering a card, the auto-pay limits and the payment history.

/// Everything behind the lock. The data lives only as long as the pages are open; the page that was open comes back after a lock.
struct WalletOpenView: View {
    let onBack: () -> Void
    let touch: () -> Void
    @Environment(AppModel.self) private var app
    @State private var info: WalletInfo?
    @State private var notices: [WalletPayment] = []
    @State private var failed = ""
    @State private var view: WalletView = WalletPlaces.currentView()

    var body: some View {
        Group {
            if let v = info {
                page(v)
            } else {
                SubPage(L(R.string.wallet_title), onBack: onBack) {
                    if !failed.isEmpty { ErrorText(failed, small: true) } else { SkeletonRows(rows: 3) }
                }
            }
        }
        .simultaneousGesture(TapGesture().onEnded { touch(); WalletPlaces.touch() })
        .task { await loadAll() }
    }

    private func go(_ v: WalletView) { view = v; WalletPlaces.go(v); touch() }
    private func back() { if let b = view.back { go(b) } else { onBack() } }

    @ViewBuilder private func page(_ v: WalletInfo) -> some View {
        let keyId = WalletKey.keyId
        switch view {
        case .list:
            WalletListPage(info: v, keyId: keyId, notices: notices, onBack: onBack, go: go, onChanged: { Task { await load() } })
        case .add:
            WalletAddPage(onBack: { go(.list) }, onSaved: { Task { await load() }; go(.list) })
        case .card(let id):
            if let card = v.cards.first(where: { $0.id == id }) {
                WalletCardPage(card: card, keyId: keyId, onBack: { go(.list) }, go: go,
                               onChanged: { Task { await load() } }, onDeleted: { Task { await load() }; go(.list) }, cards: v.cards)
            } else {
                WalletListPage(info: v, keyId: keyId, notices: notices, onBack: onBack, go: go, onChanged: { Task { await load() } })
            }
        case .reenter(let id, let autoPay):
            if let card = v.cards.first(where: { $0.id == id }) {
                WalletReenterPage(card: card, autoPay: autoPay, onBack: { go(.card(id)) },
                                  onSaved: { Task { await load() }; go(.card(id)) })
            } else {
                WalletListPage(info: v, keyId: keyId, notices: notices, onBack: onBack, go: go, onChanged: { Task { await load() } })
            }
        case .limits:
            WalletLimitsPage(paused: v.paused, onBack: back, onPaused: { Task { await load() } })
        case .payments:
            WalletPaymentsPage(onBack: back)
        case .address(let id):
            if let id, let profile = v.shipping.first(where: { $0.id == id }) {
                ShippingEditPage(profile: profile, others: v.shipping.count - 1, onBack: { go(.list) },
                                 onSaved: { Task { await load() }; go(.list) })
                    .id("address:" + id)
            } else if id == nil {
                ShippingEditPage(profile: nil, others: v.shipping.count, onBack: { go(.list) },
                                 onSaved: { Task { await load() }; go(.list) })
                    .id("address:new")
            } else {
                WalletListPage(info: v, keyId: keyId, notices: notices, onBack: onBack, go: go, onChanged: { Task { await load() } })
            }
        }
    }

    private func load() async {
        do {
            let api = try app.requireApi()
            var w = try await api.wallet()
            // the shipping addresses from their own list (GET /api/wallet carries them too, but not every server does)
            if let list = try? await api.shippingProfiles() { w.shipping = list }
            info = w
            failed = ""
        } catch { failed = app.message(for: error) }
    }

    private func loadAll() async {
        await load()
        // automatic payments the owner has not looked at: shown at the top of the list once, then marked as seen
        if let n = try? await app.requireApi().walletNotifications() {
            notices = n.items.filter { !$0.seen }
            if n.unseen > 0, let top = n.items.map(\.id).max() {
                _ = try? await app.requireApi().request("POST", "/api/wallet/notifications/seen", body: ["up_to": .number(Double(top))])
            }
        }
    }
}

// ---- small shared parts ----------------------------------------------------------------------------------------------------------

/// "30,000원", "25.00 USD": digits and separators in the app's language.
@MainActor func walletMoney(_ amount: Double, _ currency: String) -> String { keloMoney(amount, currency) }

@MainActor func walletWhen(_ at: Double?) -> String {
    guard let at, at > 0 else { return L(R.string.vault_never) }
    return MyFormat.dayTime(at)
}

/// Brand names are product names, the same in every language.
enum WalletBrand {
    static func short(_ b: CardBrand) -> String {
        switch b {
        case .visa: "VISA"
        case .mastercard: "MC"
        case .amex: "AMEX"
        case .jcb: "JCB"
        case .discover: "DISC"
        case .unionpay: "UP"
        case .diners: "DC"
        case .other: "•••"
        }
    }

    static func name(_ b: CardBrand) -> String? {
        switch b {
        case .visa: "Visa"
        case .mastercard: "Mastercard"
        case .amex: "American Express"
        case .jcb: "JCB"
        case .discover: "Discover"
        case .unionpay: "UnionPay"
        case .diners: "Diners Club"
        case .other: nil
        }
    }
}

private func faceGradient(_ brand: CardBrand) -> LinearGradient {
    let (fg, bg) = CardFaceRules.palette(brand)
    return LinearGradient(colors: [Color(hex: fg & 0xFFFFFF), Color(hex: bg & 0xFFFFFF)], startPoint: .topLeading, endPoint: .bottomTrailing)
}

/// A card's small face: Kelo's own colour pair per brand (no logos) and the last four.
struct WalletCardThumb: View {
    let brand: CardBrand
    let last4: String
    var dimmed = false
    var body: some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(faceGradient(brand))
            .frame(width: 52, height: 34)
            .overlay(alignment: .topLeading) {
                Text(WalletBrand.short(brand)).font(.system(size: 7, weight: .bold)).foregroundStyle(.white).padding(4)
            }
            .overlay(alignment: .bottomTrailing) {
                Text(CardFaceRules.lastFour(last4)).font(.system(size: 8, weight: .semibold, design: .monospaced)).foregroundStyle(.white).padding(4)
            }
            .opacity(dimmed ? 0.5 : 1)
            .accessibilityHidden(true)
    }
}

/// A card's face at full width: the brand, the (masked) number, the holder and the expiry.
struct WalletCardFace: View {
    let brand: CardBrand
    let number: String
    let holder: String
    let expiry: String
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(WalletBrand.name(brand) ?? WalletBrand.short(brand)).font(.system(.subheadline, weight: .bold)).foregroundStyle(.white)
                Spacer()
                KeloIconView(.card, size: 20, tint: .white.opacity(0.85))
            }
            Spacer(minLength: 12)
            Text(number).font(.system(.title3, design: .monospaced).weight(.semibold)).foregroundStyle(.white)
                .lineLimit(1).minimumScaleFactor(0.5)
            Spacer(minLength: 8)
            HStack(alignment: .bottom) {
                Text(holder.uppercased()).font(.system(.caption, weight: .medium)).foregroundStyle(.white.opacity(0.9)).lineLimit(1)
                Spacer(minLength: 8)
                Text(expiry).font(.system(.caption, design: .monospaced)).foregroundStyle(.white.opacity(0.9))
            }
        }
        .padding(18)
        .frame(maxWidth: 360)
        .aspectRatio(1.586, contentMode: .fit)
        .background(RoundedRectangle(cornerRadius: 16).fill(faceGradient(brand)))
        .frame(maxWidth: .infinity)
        .accessibilityElement(children: .combine)
    }
}

/// A rounded tag ("자동결제", "만료됨").
struct WalletTag: View {
    enum Tone { case plain, accent, danger }
    let text: String
    var tone: Tone = .plain
    @Environment(\.kelo) private var c
    var body: some View {
        let (bg, fg): (Color, Color) = switch tone {
        case .plain: (c.bubble, c.muted)
        case .accent: (c.mine, c.ink)
        case .danger: (c.dangerSoft, c.dangerInk)
        }
        Text(text).font(KeloFont.label).foregroundStyle(fg).lineLimit(1)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(Capsule().fill(bg))
    }
}

/// The line under a card's nickname: brand and last 4 ("Visa ••4242"); nil when the nickname already is exactly that.
func walletCardSubtitle(_ card: WalletCard) -> String? {
    let b = CardBrand.of(card.brand)
    return CardMask.subtitle(label: card.label, brandWord: WalletBrand.name(b) ?? WalletBrand.short(b), last4: card.last4)
}

/// Whether the card pays on its own, or why it asks each time, in one line (nil for a card that cannot pay at all).
@MainActor func walletAutoLine(_ card: WalletCard, keyguardOn: Bool? = nil) -> String? {
    // paused until Keyguard is on (auto-pay works only with Keyguard on)
    if AutoPayGate.pausedForKeyguard(card, keyguardOn: keyguardOn) { return L(R.string.wallet_auto_paused_keyguard) }
    guard card.canPay else { return nil }
    return card.autoNow ? L(R.string.wallet_row_pays_auto) : walletReasonText(card.reason)
}

/// Why a card asks each time, in the app's words (the server's ask_reason).
@MainActor func walletReasonText(_ reason: String) -> String {
    switch reason {
    case WalletCard.reasonNoServerCopy: L(R.string.wallet_reason_no_server_copy)
    case WalletCard.reasonPaused: L(R.string.wallet_reason_paused)
    case WalletCard.reasonVaultLocked: L(R.string.wallet_reason_vault_locked)
    default: L(R.string.wallet_reason_not_auto)
    }
}

/// The card's tags: auto-pay, and what it can do on this phone.
struct WalletStatusTags: View {
    let card: WalletCard
    let keyId: String?
    var keyguardOn: Bool? = nil
    var body: some View {
        WalletWrap {
            if card.isDefault { WalletTag(text: L(R.string.wallet_tag_default), tone: .accent) }
            // paused until Keyguard is on: "Paused", not "Asks each time"
            if AutoPayGate.pausedForKeyguard(card, keyguardOn: keyguardOn) { WalletTag(text: L(R.string.wallet_auto_paused_value), tone: .danger) }
            // "Auto-pay" only while the card really pays on its own; a card that can pay but does not says it asks each time
            if card.autoNow { WalletTag(text: L(R.string.wallet_tag_auto), tone: .accent) }
            else if card.canPay { WalletTag(text: L(R.string.wallet_tag_asks)) }
            switch CardStatus.of(card, keyId: keyId) {
            case .expired: WalletTag(text: L(R.string.wallet_tag_expired), tone: .danger)
            case .notOnPhone: WalletTag(text: L(R.string.wallet_tag_not_here), tone: .danger)
            case .unfinished: WalletTag(text: L(R.string.wallet_tag_unfinished), tone: .danger)
            case .autoOnly: WalletTag(text: L(R.string.wallet_tag_auto_only))
            case .usable: WalletTag(text: L(R.string.wallet_tag_ready))
            }
        }
    }
}

/// Lays its children out in rows, wrapping to the next row when the width runs out (tags, chips).
struct WalletWrap: Layout {
    var spacing: CGFloat = 6
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxW = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0, widest: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(ProposedViewSize(width: maxW, height: nil))
            if x > 0 && x + s.width > maxW { y += rowH + spacing; x = 0; rowH = 0 }
            x += s.width + spacing
            rowH = max(rowH, s.height)
            widest = max(widest, x - spacing)
        }
        return CGSize(width: min(widest, maxW), height: y + rowH)
    }
    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowH: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
            if x > bounds.minX && x + s.width > bounds.maxX { y += rowH + spacing; x = bounds.minX; rowH = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: min(s.width, bounds.width), height: s.height))
            x += s.width + spacing
            rowH = max(rowH, s.height)
        }
    }
}

/// A labelled input of the card forms; `error` shows under it in red.
struct WalletField: View {
    let label: String
    let placeholder: String
    @Binding var text: String
    var secure = false
    var keyboard: KeloKeyboard = .text
    var maxLength: Int = 80
    var error: String?
    var trailing: String?
    @Environment(\.kelo) private var c
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(label).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 6)
                if let trailing { Text(trailing).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1) }
            }
            KeloTextField(placeholder, text: $text, secure: secure, keyboard: keyboard, maxLength: maxLength)
            if let error { ErrorText(error, small: true) }
        }
    }
}

/// "확인했어요" with a check box; the whole row toggles it.
struct WalletAgreeRow: View {
    @Binding var agreed: Bool
    @Environment(\.kelo) private var c
    var body: some View {
        Button { agreed.toggle(); Haptics.tick() } label: {
            HStack(spacing: 10) {
                Image(systemName: agreed ? "checkmark.square.fill" : "square").font(.title3).foregroundStyle(agreed ? c.accent : c.muted)
                Text(L(R.string.wallet_notice_agree)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.vertical, 4).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(agreed ? [.isSelected, .isButton] : .isButton)
    }
}

// ---- the list --------------------------------------------------------------------------------------------------------------------

/// New auto-payments, the cards as small faces, and the way to the limits and the history; how cards are kept safe is behind (i).
struct WalletListPage: View {
    let info: WalletInfo
    let keyId: String?
    let notices: [WalletPayment]
    let onBack: () -> Void
    let go: (WalletView) -> Void
    var onChanged: () -> Void = {}
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
                ScheduleGrantRows(info.scheduleGrants, showSchedule: true) { g in
                    Task {
                        do { try await app.requireApi().revokeScheduleGrant(g); Haptics.success(); onChanged() }
                        catch { Haptics.warning() }
                    }
                }
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
        .modifier(WalletAutoPayFlow(asking: $turnOnFor, go: go, onChanged: onChanged))
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

// ---- one card --------------------------------------------------------------------------------------------------------------------

/// One card: its nickname (the title) with brand and last 4, its state, whether it is the default card, auto-pay on or off, the
/// payments Kelo made with it, and removing it. The auto-pay limits live on the wallet list, not here.
struct WalletCardPage: View {
    let card: WalletCard
    let keyId: String?
    let onBack: () -> Void
    let go: (WalletView) -> Void
    let onChanged: () -> Void
    let onDeleted: () -> Void
    var cards: [WalletCard] = []
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var msg = ""
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
                    SecondaryButton(L(R.string.wallet_make_default), fill: true) {
                        act { _ = try await app.requireApi().patch("/api/wallet/cards/\(KeloApi.segment(card.id))", body: ["default": .bool(true)], as: WalletCard.self) }
                    }
                    .accessibilityIdentifier("kelo.wallet.makeDefault")
                }
            }
            MenuGroup {
                MenuRow(.edit, L(R.string.wallet_nickname), value: card.label) { renaming = true }
            }
            SectionLabel(L(R.string.wallet_card_history)).padding(.horizontal, -16)
            WalletCardHistory(cardId: card.id)
            MenuGroup {
                MenuRow(.trash, L(R.string.wallet_delete), danger: true) {
                    if DefaultCardRules.choicesOnDelete(card, cards) != nil { pickingDefault = true } else { deleting = true }
                }
            }
            if !msg.isEmpty { ErrorText(msg, small: true) }
        }
        .alert(L(status == .usable ? R.string.wallet_auto_off_confirm : R.string.wallet_auto_off_confirm_unusable), isPresented: $turningOff) {
            Button(L(R.string.wallet_auto_turn_off), role: status == .usable ? nil : .destructive) {
                act { _ = try await app.requireApi().patch("/api/wallet/cards/\(KeloApi.segment(card.id))", body: ["auto_pay": .bool(false)], as: WalletCard.self) }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .alert(L(R.string.wallet_delete_confirm, card.label.isEmpty ? card.last4 : card.label), isPresented: $deleting) {
            Button(L(R.string.delete), role: .destructive) {
                act(then: onDeleted) { _ = try await app.requireApi().request("DELETE", "/api/wallet/cards/\(KeloApi.segment(card.id))") }
            }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .modifier(WalletAutoPayFlow(asking: $turningOn, go: go, onChanged: onChanged))
        .keyguardNeeded($keyguardForPaused) { onChanged() }
        .task { await app.refreshKeyguard() }
        .keloSheet(isPresented: $pickingDefault, detents: [.medium, .large]) {
            WalletNewDefaultSheet(card: card, choices: DefaultCardRules.choicesOnDelete(card, cards) ?? [],
                                  onCancel: { pickingDefault = false }) { next in
                pickingDefault = false
                act(then: onDeleted) {
                    _ = try await app.requireApi().request("DELETE", "/api/wallet/cards/\(KeloApi.segment(card.id))?new_default=\(KeloApi.segment(next))")
                }
            }
        }
        .keloSheet(isPresented: $renaming, detents: [.medium, .large]) {
            WalletRenameSheet(current: card.label, onCancel: { renaming = false }) { name in
                renaming = false
                act { _ = try await app.requireApi().patch("/api/wallet/cards/\(KeloApi.segment(card.id))",
                                                          body: ["label": .string(name.trimmingCharacters(in: .whitespaces))], as: WalletCard.self) }
            }
        }
    }

    private func act(then: @escaping () -> Void = {}, _ block: @escaping () async throws -> Void) {
        msg = ""
        Task {
            do {
                try await block()
                Haptics.success()
                onChanged()
                then()
            } catch {
                msg = app.message(for: error)
                Haptics.warning()
            }
        }
    }
}

/// Before auto-pay goes on: what it means, ticked, then 켜기 (this phone then opens its own copy of the card).
struct WalletAutoPaySheet: View {
    let onCancel: () -> Void
    let onConfirm: () -> Void
    @Environment(\.kelo) private var c
    @State private var agreed = false
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(R.string.wallet_auto_confirm_title), onClose: onCancel)
                Text(L(R.string.wallet_auto_notice)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                MutedText(L(R.string.wallet_auto_from_phone), small: true)
                WalletAgreeRow(agreed: $agreed)
                PrimaryButton(L(R.string.wallet_auto_confirm_yes), enabled: agreed, action: onConfirm)
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
    }
}

/// Turning auto-pay on for `asking` (Android WalletScreens.rememberAutoPayOn): the notice (ticked), then this phone's own end-to-end
/// copy opened with Face ID / Touch ID / the passcode and sent once to the auto-pay endpoint (never typed, shown or logged). A card
/// this phone holds no copy of (added on another device) says so, with typing the number as the last resort.
struct WalletAutoPayFlow: ViewModifier {
    @Binding var asking: WalletCard?
    let go: (WalletView) -> Void
    let onChanged: () -> Void
    @Environment(AppModel.self) private var app
    @State private var notHere: WalletCard?
    @State private var failed = false
    /// The card that passed the Keyguard gate: its notice sheet shows.
    @State private var cleared: WalletCard?
    /// Auto-pay needs Keyguard (`AutoPayGate`): the dialog, and the card it goes on with once Keyguard is on.
    @State private var needKeyguard = false
    @State private var waiting: WalletCard?

    /// Auto-pay needs Keyguard: with it off (or not known) the dialog comes first, and turning Keyguard on goes on from there.
    private func gate() {
        guard let card = asking else { return }
        asking = nil
        Task { @MainActor in
            if AutoPayGate.mayTurnOn(await app.refreshKeyguard()) { cleared = card } else { waiting = card; needKeyguard = true }
        }
    }

    func body(content: Content) -> some View {
        content
            .onChange(of: asking?.id) { _, id in if id != nil { gate() } }
            .keyguardNeeded($needKeyguard) {
                if let card = waiting { waiting = nil; cleared = card }
                onChanged()
            }
            .keloSheet(isPresented: Binding(get: { cleared != nil }, set: { if !$0 { cleared = nil } }), detents: [.medium, .large]) {
                WalletAutoPaySheet(onCancel: { cleared = nil }, onConfirm: {
                    guard let card = cleared else { return }
                    cleared = nil
                    Task { @MainActor in
                        try? await Task.sleep(nanoseconds: 450_000_000)   // the sheet goes first, so the system check is not behind it
                        if let problem = await WalletAutoPay.turnOn(app: app, card: card) {
                            switch problem {
                            case .notOnPhone: notHere = card
                            case .cancelled: break
                            case .keyguardRequired: waiting = card; needKeyguard = true   // the server says so: the same dialog
                            default: failed = true; Haptics.warning()
                            }
                        } else {
                            Haptics.success()
                            onChanged()
                        }
                    }
                })
            }
            .alert(L(R.string.wallet_auto_not_here_title), isPresented: Binding(get: { notHere != nil }, set: { if !$0 { notHere = nil } })) {
                Button(L(R.string.wallet_auto_enter_number)) {
                    if let card = notHere { notHere = nil; go(.reenter(card.id, autoPay: true)) }
                }
                Button(L(R.string.cancel), role: .cancel) { notHere = nil }
            } message: {
                Text(L(R.string.wallet_auto_not_here_body))
            }
            .alert(L(R.string.wallet_auto_on_failed), isPresented: $failed) {
                Button(L(R.string.ok), role: .cancel) {}
            }
    }
}

/// Deleting the default card with two or more others left: which card Kelo pays with from now on, then delete.
struct WalletNewDefaultSheet: View {
    let card: WalletCard
    let choices: [WalletCard]
    let onCancel: () -> Void
    let onDelete: (String) -> Void
    @Environment(\.kelo) private var c
    @State private var picked: String?
    var body: some View {
        let chosen = picked ?? DefaultCardRules.suggested(choices)?.id
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.wallet_delete_default_title), onClose: onCancel)
                Text(L(R.string.wallet_delete_default_body, card.label)).font(KeloFont.body).foregroundStyle(c.ink)
                    .fixedSize(horizontal: false, vertical: true)
                VStack(spacing: 0) {
                    ForEach(choices, id: \.id) { other in
                        RadioRow(other.label, detail: walletCardSubtitle(other), selected: chosen == other.id) { picked = other.id }
                        if other.id != choices.last?.id { Hairline() }
                    }
                }
                PrimaryButton(L(R.string.delete), enabled: chosen != nil) { if let chosen { onDelete(chosen) } }
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
    }
}

/// The payments Kelo made with one card, newest first, older ones a page at a time; each opens its chat or task when known.
struct WalletCardHistory: View {
    let cardId: String
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var items: [WalletPayment]?
    @State private var next: Int64?
    @State private var loading = false
    @State private var msg = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let list = items {
                if list.isEmpty { EmptyNote(L(R.string.wallet_card_history_empty)).padding(.bottom, 16) }
                ForEach(list, id: \.id) { p in
                    row(p)
                    Hairline()
                }
                if next != nil && !list.isEmpty {
                    SecondaryButton(L(R.string.wallet_card_history_more), enabled: !loading, fill: true) { Task { await load(before: next) } }
                        .padding(.top, 10)
                        .accessibilityIdentifier("kelo.wallet.historyMore")
                }
            } else {
                SkeletonRows(rows: 2)
            }
            if !msg.isEmpty { ErrorText(msg, small: true) }
        }
        .task(id: cardId) { await load(before: nil) }
    }

    private func row(_ p: WalletPayment) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                WalletTag(text: L(p.auto ? R.string.wallet_kind_auto : R.string.wallet_kind_approved), tone: p.auto ? .accent : .plain)
                Text(p.site.isEmpty ? p.host : p.site).font(KeloFont.bodyLarge.weight(.semibold)).foregroundStyle(c.ink).lineLimit(2)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Text(p.amountText.isEmpty ? walletMoney(p.amount, p.currency) : p.amountText).font(KeloFont.button).foregroundStyle(c.ink)
            }
            if !p.summary.isEmpty { Text(p.summary).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2) }
            MutedText(walletWhen(p.at), small: true)
            switch CardHistoryRules.link(p) {
            case .chat: LinkButton(L(R.string.wallet_open_chat)) { app.router.open(.chat) }
            case .task(let id): LinkButton(L(R.string.wallet_open_task)) { app.router.open(.work(.task(id))) }
            case .none: EmptyView()
            }
        }
        .padding(.vertical, 8)
    }

    private func load(before: Int64?) async {
        loading = true
        defer { loading = false }
        do {
            var path = "/api/wallet/cards/\(KeloApi.segment(cardId))/payments?limit=20"
            if let before { path += "&before_id=\(before)" }
            let page = try await app.requireApi().get(path, as: WalletPayments.self)
            items = CardHistoryRules.merge(before == nil ? [] : (items ?? []), page)
            next = page.nextBeforeId
            msg = ""
        } catch {
            msg = app.message(for: error)
            if items == nil { items = [] }
        }
    }
}

/// A new name for a card; never one that holds card digits.
struct WalletRenameSheet: View {
    let current: String
    let onCancel: () -> Void
    let onSave: (String) -> Void
    @Environment(\.kelo) private var c
    @State private var name = ""
    var body: some View {
        let digits = WalletFormRules.nameHasDigits(name)
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                SheetHeader(L(R.string.wallet_nickname), onClose: onCancel)
                KeloTextField(L(R.string.wallet_rename_hint), text: $name, maxLength: 60) { if !digits { onSave(name) } }
                MutedText(L(R.string.wallet_rename_note), small: true)
                if digits { ErrorText(L(R.string.wallet_rename_no_digits), small: true) }
                PrimaryButton(L(R.string.save), enabled: !digits) { onSave(name) }
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .onAppear { name = current }
    }
}

// ---- limits ----------------------------------------------------------------------------------------------------------------------

/// 자동결제 한도: the global pause, and per currency the limit for one payment and for a month with what was paid automatically this
/// month. Each can go back to the default; another currency can be added.
struct WalletLimitsPage: View {
    let paused: Bool
    let onBack: () -> Void
    let onPaused: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var data: WalletLimits?
    @State private var isPaused: Bool?
    @State private var added: Set<String> = []
    @State private var editing: String?
    @State private var adding = false
    @State private var msg = ""

    var body: some View {
        let homes = LimitRules.homeCurrencies(app: Strings.shared.locale, system: Locale.current)
        SubPage(L(R.string.wallet_limits_title), onBack: onBack) {
            MyCard {
                ToggleRow(L(R.string.wallet_pause_title), isOn: Binding(get: { isPaused ?? paused }, set: { setPaused($0) }))
                MutedText(L((isPaused ?? paused) ? R.string.wallet_pause_on_body : R.string.wallet_pause_off_body), small: true)
            }
            MutedText(L(R.string.wallet_limits_help), small: true)
            if let d = data {
                if !d.month.isEmpty { MutedText(L(R.string.wallet_limits_month, d.month, d.timezone), small: true) }
                let shown = LimitRules.visible(d.limits, homes: homes, added: added.union(editing.map { [$0] } ?? []))
                ForEach(shown, id: \.currency) { row in
                    WalletLimitCard(row: row, editing: editing == row.currency,
                                    onEdit: { editing = editing == row.currency ? nil : row.currency },
                                    onSave: { save(row.currency, $0, $1) },
                                    onReset: { reset(row.currency) })
                }
                // a currency being added that the server does not know yet
                if let cur = editing, !d.limits.contains(where: { $0.currency == cur }) {
                    WalletLimitCard(row: WalletLimit(currency: cur), editing: true, onEdit: { editing = nil },
                                    onSave: { save(cur, $0, $1) }, onReset: { editing = nil })
                }
                if !msg.isEmpty { ErrorText(msg, small: true) }
                MenuGroup { MenuRow(.plus, L(R.string.wallet_limits_add)) { adding = true } }
                    .keloSheet(isPresented: $adding, detents: [.medium, .large]) {
                        WalletCurrencySheet(options: LimitRules.addable(d.limits, shown: shown), onCancel: { adding = false }) { cur in
                            adding = false
                            added.insert(cur)
                            editing = cur
                        }
                    }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await load() }
    }

    private func load() async {
        do { data = try await app.requireApi().walletLimits() } catch {
            msg = app.message(for: error)
            if data == nil { data = try? KeloApi.decode(WalletLimits.self, ["limits": .array([])]) }
        }
    }

    private func setPaused(_ on: Bool) {
        msg = ""
        Task {
            do { isPaused = try await app.requireApi().setWalletPaused(on).paused; Haptics.success(); onPaused() }
            catch { msg = app.message(for: error); Haptics.warning() }
        }
    }

    private func save(_ currency: String, _ per: Double, _ mon: Double) {
        msg = ""
        Task {
            do {
                _ = try await app.requireApi().put("/api/wallet/limits/\(KeloApi.segment(currency))",
                                                   body: ["per_payment": .number(per), "monthly": .number(mon)], as: WalletLimit.self)
                editing = nil; added.insert(currency); Haptics.success()
                await load()
            } catch { msg = app.message(for: error); Haptics.warning() }
        }
    }

    private func reset(_ currency: String) {
        msg = ""
        Task {
            do {
                _ = try await app.requireApi().request("DELETE", "/api/wallet/limits/\(KeloApi.segment(currency))")
                editing = nil; added.remove(currency); Haptics.success()
                await load()
            } catch { msg = app.message(for: error) }
        }
    }
}

struct WalletLimitCard: View {
    let row: WalletLimit
    let editing: Bool
    let onEdit: () -> Void
    let onSave: (Double, Double) -> Void
    let onReset: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        MyCard(action: editing ? nil : onEdit) {
            HStack(spacing: 8) {
                Text(row.currency).font(KeloFont.titleSmall).foregroundStyle(c.ink).frame(maxWidth: .infinity, alignment: .leading)
                WalletTag(text: L(row.added ? R.string.wallet_limit_added : (row.custom ? R.string.wallet_limit_custom : R.string.wallet_limit_default)),
                          tone: row.custom || row.added ? .accent : .plain)
            }
            if row.monthly <= 0 && row.perPayment <= 0 && (row.custom || row.added) {
                MutedText(L(R.string.wallet_limit_never), small: true)
            } else {
                Text(L(R.string.wallet_limit_line, walletMoney(row.perPayment, row.currency), walletMoney(row.monthly, row.currency)))
                    .font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                KeloBar(LimitRules.used(row.spentThisMonth, row.monthly), hot: row.spentThisMonth >= row.monthly && row.monthly > 0)
                MutedText(L(R.string.wallet_limit_spent, walletMoney(row.spentThisMonth, row.currency)), small: true)
            }
            if editing { WalletLimitEditor(row: row, onSave: onSave, onReset: onReset, onCancel: onEdit) }
        }
    }
}

struct WalletLimitEditor: View {
    let row: WalletLimit
    let onSave: (Double, Double) -> Void
    let onReset: () -> Void
    let onCancel: () -> Void
    @State private var per = ""
    @State private var mon = ""

    var body: some View {
        let perV = Money.parse(per, row.currency)
        let monV = Money.parse(mon, row.currency)
        let check = LimitRules.check(perV, monV)
        VStack(alignment: .leading, spacing: 10) {
            WalletField(label: L(R.string.wallet_limit_per, row.currency), placeholder: "", text: $per, keyboard: .number, maxLength: 16,
                        error: !per.isEmpty && perV == nil ? L(R.string.wallet_limit_bad_number) : nil)
            WalletField(label: L(R.string.wallet_limit_month, row.currency), placeholder: "", text: $mon, keyboard: .number, maxLength: 16,
                        error: !mon.isEmpty && monV == nil ? L(R.string.wallet_limit_bad_number)
                            : (check == .perAboveMonthly ? L(R.string.wallet_limit_per_above) : nil))
            MutedText(L(R.string.wallet_limit_zero_note), small: true)
            if let dp = row.defaultPerPayment {
                MutedText(L(R.string.wallet_limit_default_line, walletMoney(dp, row.currency), walletMoney(row.defaultMonthly ?? 0, row.currency)), small: true)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) { buttons(perV, monV, check) }
                VStack(spacing: 8) { buttons(perV, monV, check) }
            }
            if row.custom {
                LinkButton(L(row.added ? R.string.wallet_limit_remove : R.string.wallet_limit_reset), danger: row.added, action: onReset)
            }
        }
        .onAppear {
            let known = row.custom || row.defaultPerPayment != nil
            per = known ? Money.plain(row.perPayment, row.currency) : ""
            mon = known ? Money.plain(row.monthly, row.currency) : ""
        }
    }

    @ViewBuilder private func buttons(_ perV: Double?, _ monV: Double?, _ check: LimitCheck) -> some View {
        SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
        PrimaryButton(L(R.string.save), enabled: check == .ok) { if let perV, let monV { onSave(perV, monV) } }
    }
}

/// Another currency for the limits: a 3-letter code typed or one of the known ones.
struct WalletCurrencySheet: View {
    let options: [String]
    let onCancel: () -> Void
    let onPick: (String) -> Void
    @State private var code = ""
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                SheetHeader(L(R.string.wallet_limits_add), onClose: onCancel)
                KeloTextField(L(R.string.wallet_currency_hint), text: $code, keyboard: .ascii, maxLength: 3)
                WalletWrap {
                    ForEach(Array(options.prefix(18)), id: \.self) { o in ChoiceChip(o, selected: o == code) { code = o } }
                }
                PrimaryButton(L(R.string.wallet_currency_add), enabled: Money.validCode(code)) { onPick(code) }
                SecondaryButton(L(R.string.cancel), fill: true, action: onCancel)
            }
            .padding(.horizontal, 20).padding(.bottom, 24)
        }
        .onChange(of: code) { _, v in
            let f = String(v.uppercased().filter { $0 >= "A" && $0 <= "Z" }.prefix(3))
            if f != v { code = f }
        }
    }
}

// ---- payments --------------------------------------------------------------------------------------------------------------------

/// 결제 내역: every payment that went ahead, newest first, automatic or allowed on the phone.
struct WalletPaymentsPage: View {
    let onBack: () -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var items: [WalletPayment]?
    @State private var msg = ""

    var body: some View {
        SubPage(L(R.string.wallet_history_title), onBack: onBack) {
            if let list = items {
                if !msg.isEmpty { ErrorText(msg, small: true) }
                if list.isEmpty { EmptyNote(L(R.string.wallet_history_empty)) }
                ForEach(list, id: \.id) { p in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            WalletTag(text: L(p.auto ? R.string.wallet_kind_auto : R.string.wallet_kind_approved), tone: p.auto ? .accent : .plain)
                            Text(p.site.isEmpty ? p.host : p.site).font(KeloFont.bodyLarge.weight(.semibold)).foregroundStyle(c.ink).lineLimit(2)
                                .frame(maxWidth: .infinity, alignment: .leading)
                            Text(p.amountText.isEmpty ? walletMoney(p.amount, p.currency) : p.amountText).font(KeloFont.button).foregroundStyle(c.ink)
                        }
                        if !p.summary.isEmpty { Text(p.summary).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2) }
                        MutedText(p.cardLabel.isEmpty ? walletWhen(p.at) : L(R.string.wallet_history_meta, p.cardLabel, walletWhen(p.at)), small: true)
                    }
                    .padding(.vertical, 8)
                    Hairline()
                }
            } else {
                SkeletonRows(rows: 4)
            }
        }
        .task {
            do { items = try await app.requireApi().get("/api/wallet/payments?limit=100", as: WalletPayments.self).items }
            catch { msg = app.message(for: error); items = [] }
        }
    }
}

// ---- adding a card ---------------------------------------------------------------------------------------------------------------

/// Why a card was not saved.
@MainActor private func saveWord(_ e: Error) -> String {
    switch e as? WalletKey.Failure {
    case .noLock: L(R.string.my_ios_wallet_save_no_lock)
    case .cancelled: L(R.string.wallet_save_cancelled)
    default: L(R.string.wallet_save_key_lost)
    }
}

/// The phone's key after the owner's check, for sealing a card (Android wallet_auth_save_*).
@MainActor private func sealKey() async throws -> SymmetricKey {
    try await WalletKey.key(reason: L(R.string.wallet_auth_save_subtitle))
}

@MainActor private func numberError(_ number: String, mustEnd: String?) -> String? {
    switch WalletFormRules.numberProblem(number, mustEnd: mustEnd) {
    case .invalid: L(R.string.wallet_number_invalid)
    case .otherCard(let end): L(R.string.wallet_number_other_card, end)
    case nil: nil
    }
}

/// 카드 등록, in steps over a live card face: 1 the number (its brand shows as it is typed; [카드 스캔] fills it and the expiry from the
/// camera), 2 expiry and CVC, 3 the nickname, the holder's name and postal code (both optional), then the notice to tick and Save. The
/// number and CVC are kept only in memory (`WalletPlaces` holds them through a lock for up to ten minutes), sealed with this iPhone's
/// key after Face ID, and cleared once sent.
struct WalletAddPage: View {
    let onBack: () -> Void
    let onSaved: () -> Void
    @Environment(AppModel.self) private var app
    @State private var draft = WalletPlaces.addDraft() ?? AddCardDraft()
    @State private var busy = false

    var body: some View {
        let prev = AddCardSteps.prev(draft.step)
        SubPage(L(R.string.wallet_add_title), onBack: { if let prev, !busy { draft.step = prev } else if !busy { app.secretCardWait.stop(); onBack() } }) {
            AddCardForm(draft: $draft, busy: $busy, inline: false) { added in
                WalletPlaces.clear()
                app.secretCardKept()   // the chat puts "(saved to Keychain: …)" in place of the number, if it came from there
                // a card entry the agent asked for waits for this card: it is answered with it (nickname and last four digits only)
                guard let api = app.api else { onSaved(); return }
                let back = await app.secretCardWait.cardAdded(SecretCardWait.card(from: added), api: api)
                onSaved()
                if let back {   // back where the entry was (the chat), the wallet left at its list
                    app.router.paths[.my] = NavigationPath()
                    app.router.tab = back
                }
            }
        }
        .keloSensitive()
        .onChange(of: draft) { _, d in WalletPlaces.saveAdd(d) }
        .onAppear {
            // a card number from a message the chat held back (SecretGuard): already filled in, taken out of the hand-off at once
            if let v = app.secretHandoff.peek() {
                app.secretHandoff.clear()
                if draft.number.isEmpty {
                    let d = CardNumber.digits(v)
                    draft.number = String(d.prefix(CardNumber.maxLength(CardNumber.brand(d))))
                }
            }
        }
    }
}

/// The card form inside a card fill-in card (the chat's, the Keychain's waiting list): its own draft, memory only, gone with the card.
struct InlineCardForm: View {
    let onSaved: (JSON) -> Void
    @State private var draft = AddCardDraft()
    @State private var busy = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            AddCardForm(draft: $draft, busy: $busy, inline: true) { added in onSaved(added) }
        }
        .keloSensitive()
        .onDisappear { draft = AddCardDraft() }
    }
}

/// The card form itself (Android wallet/CardForms.kt AddCardForm), used by 카드 등록 and, `inline`, by a card fill-in card.
/// `onAdded`: the server's answer for the saved card ({id, label, brand, last4, ...}).
struct AddCardForm: View {
    @Binding var draft: AddCardDraft
    @Binding var busy: Bool
    let inline: Bool
    let onAdded: (JSON) async -> Void
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var problem = ""

    var body: some View {
        let now = YearMonth.now()
        let brand = CardNumber.brand(draft.number)
        let prev = AddCardSteps.prev(draft.step)
        let (at, of) = AddCardSteps.position(draft.step)
        let canLeave = AddCardSteps.canLeave(draft.step, number: draft.number, expiry: draft.expiry, cvc: draft.cvc, agreed: draft.agreed, now: now)
        WalletCardFace(brand: brand, number: CardFaceRules.preview(draft.number), holder: draft.holder,
                       expiry: draft.expiry.isEmpty ? L(R.string.wallet_field_expiry_hint) : Expiry.format(draft.expiry))
            .padding(.horizontal, inline ? 32 : 8)
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(L(stepTitle(draft.step))).font(KeloFont.titleMedium).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityAddTraits(.isHeader)
            Text(L(R.string.wallet_step_of, at, of)).font(KeloFont.label).foregroundStyle(c.muted)
        }
        ProgressView(value: Double(at), total: Double(of)).tint(c.accent)
        switch draft.step {
        case .number:
            WalletField(label: L(R.string.wallet_field_number), placeholder: L(R.string.wallet_field_number_hint), text: numberBinding,
                        keyboard: .number, maxLength: 23, error: numberError(draft.number, mustEnd: nil), trailing: WalletBrand.name(brand))
            CardScanButton { scan in
                draft.number = String(CardNumber.digits(scan.number).prefix(CardNumber.maxLength(CardNumber.brand(scan.number))))
                if let e = scan.expiry { draft.expiry = Expiry.clean(e) }
            }
        case .dates:
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .top, spacing: 10) { dateFields(brand, now: now) }
                VStack(alignment: .leading, spacing: 10) { dateFields(brand, now: now) }
            }
        case .owner:
            // the owner's name for the card: its title in the wallet, the approval cards and what Kelo calls it
            WalletField(label: L(R.string.wallet_nickname_field), placeholder: L(R.string.wallet_rename_hint), text: field(\.nickname, max: 60),
                        maxLength: 60, error: WalletFormRules.nameHasDigits(draft.nickname) ? L(R.string.wallet_rename_no_digits) : nil)
                .accessibilityIdentifier("kelo.wallet.nickname")
            WalletField(label: L(R.string.wallet_field_holder), placeholder: L(R.string.wallet_field_holder_hint), text: field(\.holder, max: 80))
            WalletField(label: L(R.string.wallet_field_postal), placeholder: L(R.string.wallet_field_postal_hint), text: field(\.postal, max: 16),
                        keyboard: .ascii, maxLength: 16)
        case .confirm:
            Text(L(R.string.wallet_card_notice)).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            WalletAgreeRow(agreed: Binding(get: { draft.agreed }, set: { draft.agreed = $0 }))
        }
        if !problem.isEmpty { ErrorText(problem, small: true) }
        if draft.step == .confirm {
            PrimaryButton(busy ? L(R.string.wallet_saving) : L(R.string.wallet_save), enabled: canLeave && !busy, busy: busy) { save() }
        } else {
            PrimaryButton(L(R.string.wallet_step_next), enabled: canLeave) { draft.step = AddCardSteps.next(draft.step) }
                .accessibilityIdentifier("kelo.wallet.next")
        }
        if inline, let prev { LinkButton(L(R.string.back)) { if !busy { draft.step = prev } } }
    }

    @ViewBuilder private func dateFields(_ brand: CardBrand, now: YearMonth) -> some View {
        WalletField(label: L(R.string.wallet_field_expiry), placeholder: L(R.string.wallet_field_expiry_hint), text: expiryBinding,
                    keyboard: .number, maxLength: 5, error: expiryError(now: now))
        WalletField(label: L(R.string.wallet_field_cvc), placeholder: L(brand == .amex ? R.string.wallet_field_cvc_hint4 : R.string.wallet_field_cvc_hint3),
                    text: Binding(get: { draft.cvc }, set: { draft.cvc = Cvc.clean($0, brand: brand) }), secure: true, keyboard: .number, maxLength: 4)
    }

    private func stepTitle(_ s: AddStep) -> StringKey {
        switch s {
        case .number: R.string.wallet_step_number
        case .dates: R.string.wallet_step_dates
        case .owner: R.string.wallet_step_owner
        case .confirm: R.string.wallet_step_confirm
        }
    }

    private func expiryError(now: YearMonth) -> String? {
        switch Expiry.check(draft.expiry, now: now) {
        case .badMonth: L(R.string.wallet_expiry_bad_month)
        case .expired: L(R.string.wallet_expiry_expired)
        case .tooFar: L(R.string.wallet_expiry_too_far)
        default: nil
        }
    }

    /// The field shows the digits grouped ("4242 4242 …"); the draft keeps only the digits.
    private var numberBinding: Binding<String> {
        Binding(get: { CardNumber.group(draft.number) }, set: { v in
            let d = CardNumber.digits(v)
            draft.number = String(d.prefix(CardNumber.maxLength(CardNumber.brand(d))))
        })
    }

    private var expiryBinding: Binding<String> {
        Binding(get: { Expiry.format(draft.expiry) }, set: { draft.expiry = Expiry.clean($0) })
    }

    private func field(_ k: WritableKeyPath<AddCardDraft, String>, max: Int) -> Binding<String> {
        Binding(get: { draft[keyPath: k] }, set: { draft[keyPath: k] = String($0.prefix(max)) })
    }

    private func save() {
        let now = YearMonth.now()
        if let blocking = AddCardSteps.firstBlocking(number: draft.number, expiry: draft.expiry, cvc: draft.cvc, now: now) {
            draft.step = blocking; return
        }
        guard case .ok(let month, let year) = Expiry.check(draft.expiry, now: now),
              let secret = CardSecret.of(number: CardNumber.digits(draft.number), cvc: draft.cvc) else { return }
        busy = true; problem = ""
        let d = draft
        Task {
            defer { busy = false; secret.wipe() }
            let key: SymmetricKey
            do { key = try await sealKey() } catch { problem = saveWord(error); return }
            guard let keyId = WalletKey.keyId, let e2e = try? CardBlob.seal(secret, key: key, keyId: keyId) else {
                problem = L(R.string.wallet_save_key_lost); return
            }
            // the phone's copy always goes; auto-pay is turned on later from the card's page (Android NewCard autoPay = false)
            let fields: [String: JSON] = [
                "exp_month": .number(Double(month)), "exp_year": .number(Double(year)),
                "holder": .string(d.holder.trimmingCharacters(in: .whitespaces)), "postal_code": .string(d.postal.trimmingCharacters(in: .whitespaces)),
                "auto_pay": .bool(false), "brand": .string(CardNumber.brand(d.number).rawValue), "last4": .string(secret.last4),
                "e2e": ["blob": .string(e2e.blob), "alg": .string(e2e.alg), "key_id": .string(e2e.keyId)],
            ].merging(d.nickname.trimmingCharacters(in: .whitespaces).isEmpty ? [:] :
                        ["label": .string(d.nickname.trimmingCharacters(in: .whitespaces))]) { a, _ in a }
            do {
                let api = try app.requireApi()
                let added = try await api.request("POST", "/api/wallet/cards", body: .object(fields))
                draft = AddCardDraft()
                Haptics.success()
                await onAdded(added)
            } catch let e as ApiError {
                problem = e.isNetwork ? L(R.string.wallet_save_server) : L(R.string.wallet_save_failed, e.detail)
                Haptics.warning()
            } catch {
                problem = L(R.string.wallet_save_server)
            }
        }
    }
}

/// The number and CVC of a saved card typed again: to make a new phone copy (a card this iPhone cannot open), or to turn auto-pay
/// on (the server keeps its own sealed copy then). The number must be the same card (same last 4 digits).
struct WalletReenterPage: View {
    let card: WalletCard
    let autoPay: Bool
    let onBack: () -> Void
    let onSaved: () -> Void
    @Environment(AppModel.self) private var app
    @State private var draft: ReenterDraft
    @State private var busy = false
    @State private var problem = ""
    @State private var needKeyguard = false

    init(card: WalletCard, autoPay: Bool, onBack: @escaping () -> Void, onSaved: @escaping () -> Void) {
        self.card = card; self.autoPay = autoPay; self.onBack = onBack; self.onSaved = onSaved
        _draft = State(initialValue: MainActor.assumeIsolated { WalletPlaces.reenterDraft(card.id, autoPay: autoPay) } ?? ReenterDraft(cardId: card.id, autoPay: autoPay))
    }

    var body: some View {
        let brand = draft.number.isEmpty ? CardBrand.of(card.brand) : CardNumber.brand(draft.number)
        let ready = WalletFormRules.reenterReady(number: draft.number, cvc: draft.cvc, card: card)
        let name = card.label.isEmpty ? L(R.string.wallet_card_masked, card.last4) : card.label
        SubPage(L(autoPay ? R.string.wallet_reenter_auto_title : R.string.wallet_reenter_title), onBack: { if !busy { onBack() } }) {
            MutedText(L(autoPay ? R.string.wallet_reenter_auto_body : R.string.wallet_reenter_body, name), small: true)
            MyCard {
                WalletField(label: L(R.string.wallet_field_number), placeholder: L(R.string.wallet_field_number_ending, card.last4),
                            text: Binding(get: { CardNumber.group(draft.number) }, set: { v in
                                let d = CardNumber.digits(v)
                                draft.number = String(d.prefix(CardNumber.maxLength(CardNumber.brand(d))))
                            }), keyboard: .number, maxLength: 23, error: numberError(draft.number, mustEnd: card.last4))
                CardScanButton { scan in
                    draft.number = String(CardNumber.digits(scan.number).prefix(CardNumber.maxLength(CardNumber.brand(scan.number))))
                }
                WalletField(label: L(R.string.wallet_field_cvc), placeholder: L(brand == .amex ? R.string.wallet_field_cvc_hint4 : R.string.wallet_field_cvc_hint3),
                            text: Binding(get: { draft.cvc }, set: { draft.cvc = Cvc.clean($0, brand: brand) }), secure: true, keyboard: .number, maxLength: 4)
            }
            if !problem.isEmpty { ErrorText(problem, small: true) }
            PrimaryButton(busy ? L(R.string.wallet_saving) : L(autoPay ? R.string.wallet_auto_turn_on : R.string.wallet_save),
                          enabled: ready && !busy, busy: busy) { save() }
        }
        .keloSensitive()
        .onChange(of: draft) { _, d in WalletPlaces.saveReenter(d) }
        .keyguardNeeded($needKeyguard) { save() }
    }

    private func save() {
        guard let secret = CardSecret.of(number: CardNumber.digits(draft.number), cvc: draft.cvc) else { return }
        busy = true; problem = ""
        Task {
            defer { busy = false; secret.wipe() }
            let key: SymmetricKey
            do { key = try await sealKey() } catch { problem = saveWord(error); return }
            guard let keyId = WalletKey.keyId, let e2e = try? CardBlob.seal(secret, key: key, keyId: keyId) else {
                problem = L(R.string.wallet_save_key_lost); return
            }
            var fields: [String: JSON] = ["e2e": ["blob": .string(e2e.blob), "alg": .string(e2e.alg), "key_id": .string(e2e.keyId)]]
            if autoPay { fields["auto_pay"] = .bool(true) }
            let path = "/api/wallet/cards/\(KeloApi.segment(card.id))"
            do {
                let api = try app.requireApi()
                if autoPay {
                    // the number and CVC go too, for the server's own sealed copy (as bytes, wiped after)
                    var body = SecretJson.body(.object(fields), secret)
                    defer { body.resetBytes(in: 0..<body.count) }
                    _ = try await api.requestBytes("PATCH", path, body: body)
                } else {
                    _ = try await api.request("PATCH", path, body: .object(fields))
                }
                draft = ReenterDraft(cardId: card.id, autoPay: autoPay)
                WalletPlaces.saveReenter(draft)
                Haptics.success()
                onSaved()
            } catch let e as ApiError {
                if e.code == AutoPayGate.codeKeyguardRequired { needKeyguard = true; return }   // the dialog, then again
                problem = e.isNetwork ? L(R.string.wallet_save_server) : e.detail
                Haptics.warning()
            } catch {
                problem = L(R.string.wallet_save_server)
            }
        }
    }
}
