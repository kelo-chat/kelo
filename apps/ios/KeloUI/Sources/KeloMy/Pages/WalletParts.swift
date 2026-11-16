import SwiftUI
import KeloCore
import KeloShared

// Shared pieces of the card pages: money and date words, tags, the wrapping row, the form field and the agree row.

/// "30,000원", "25.00 USD": digits and separators in the app's language.
@MainActor func walletMoney(_ amount: Double, _ currency: String) -> String { keloMoney(amount, currency) }

@MainActor func walletWhen(_ at: Double?) -> String {
    guard let at, at > 0 else { return L(R.string.vault_never) }
    return MyFormat.dayTime(at)
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

/// A payment Kelo made, as its first lines: how it went ahead, where, how much, and what for.
struct PaymentHead: View {
    let p: WalletPayment
    @Environment(\.kelo) private var c

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            WalletTag(text: L(p.auto ? R.string.wallet_kind_auto : R.string.wallet_kind_approved), tone: p.auto ? .accent : .plain)
            Text(p.site.isEmpty ? p.host : p.site).font(KeloFont.bodyLarge.weight(.semibold)).foregroundStyle(c.ink).lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(p.amountText.isEmpty ? walletMoney(p.amount, p.currency) : p.amountText).font(KeloFont.button).foregroundStyle(c.ink)
        }
        if !p.summary.isEmpty { Text(p.summary).font(KeloFont.small).foregroundStyle(c.ink).lineLimit(2) }
    }
}
