import SwiftUI
import KeloCore

/// The one final order card's body (confirm_purchase), the same in the chat and on the Work page: the site, the items (or the
/// summary), where it goes, what pays, the coupon, and the total, prominent. Every line wraps; nothing is cut at 320 pt.
public struct OrderDetails: View {
    let payment: PaymentInfo
    let card: ApprovalCardInfo?
    @Environment(\.kelo) private var c

    public init(payment: PaymentInfo, card: ApprovalCardInfo?) {
        self.payment = payment; self.card = card
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !payment.site.isEmpty {
                HStack(alignment: .center, spacing: 6) {
                    KeloIconView(.globe, size: 16, tint: c.muted)
                    Text(TokenText.fit(payment.site)).font(KeloFont.small).foregroundStyle(c.muted)
                        .lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                }
                .accessibilityElement(children: .combine)
            }
            if !payment.items.isEmpty {
                part(.package, L(R.string.order_items)) {
                    ForEach(Array(payment.items.enumerated()), id: \.offset) { _, item in itemRow(item) }
                }
            } else if let what = summary {
                Text(what).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
            if let s = payment.shipping {
                part(.mapPin, L(R.string.order_ship_to)) {
                    let head = OrderRules.shipHead(s)
                    if !head.isEmpty {
                        Text(head).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(Array(s.lines.enumerated()), id: \.offset) { _, line in
                        Text(line).font(KeloFont.small).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    }
                    if s.source == OrderShipping.fromSite {
                        MutedText(L(R.string.order_ship_site), small: true)
                    } else if s.source == OrderShipping.once {
                        MutedText(L(R.string.order_ship_once), small: true)
                    }
                }
                .accessibilityElement(children: .combine)
            }
            part(.card, L(R.string.order_pay_with)) {
                if let card {
                    // the owner's nickname for the card, with brand and last 4 under it
                    let b = CardBrand.of(card.card.brand)
                    let name = card.card.label.trimmingCharacters(in: .whitespaces).isEmpty ? (b.displayName ?? b.shortName) : card.card.label
                    Text(name).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                    if let sub = CardMask.subtitle(label: name, brandWord: b.displayName ?? b.shortName, last4: card.card.last4) {
                        Text(sub).font(KeloFont.small).foregroundStyle(c.muted)
                    }
                } else {
                    Text(L(R.string.order_pay_site)).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
            }
            .accessibilityElement(children: .combine)
            if !payment.coupon.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: "tag").font(.system(size: 13)).foregroundStyle(c.muted).accessibilityHidden(true)
                    Text(L(R.string.order_coupon)).font(KeloFont.small).foregroundStyle(c.muted)
                    Text(payment.coupon).font(KeloFont.small.weight(.semibold)).foregroundStyle(c.ink)
                        .fixedSize(horizontal: false, vertical: true).frame(maxWidth: .infinity, alignment: .leading)
                }
                .accessibilityElement(children: .combine)
            }
            Hairline()
            HStack(alignment: .lastTextBaseline, spacing: 12) {
                Text(L(R.string.order_total)).font(KeloFont.body).foregroundStyle(c.muted)
                total.multilineTextAlignment(.trailing).frame(maxWidth: .infinity, alignment: .trailing)
            }
            .accessibilityElement(children: .combine)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 12).fill(c.bubble))
    }

    /// The summary, else what was ordered (nil when neither says anything).
    private var summary: String? {
        let s = payment.summary.trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.isEmpty { return s }
        let w = (payment.orderWhat ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return w.isEmpty ? nil : w
    }

    private var total: some View {
        Text(payment.amountText.isEmpty ? L(R.string.wallet_money, OrderMoney.number(payment.amount), payment.currency) : payment.amountText)
            .font(.system(size: 22, weight: .bold)).foregroundStyle(c.ink)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityIdentifier("kelo.order.total")
    }

    /// One part of the order card: a small icon and title, then its lines (indented under the title).
    private func part<Content: View>(_ icon: KeloIcon, _ title: String, @ViewBuilder _ content: () -> Content) -> some View {
        CardPart(icon, title, content: content)
    }

    /// "블루 머그 / 색상: 네이비" on the left, the price and "× 2" on the right; a long name wraps beside them.
    private func itemRow(_ item: OrderItem) -> some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text(item.name).font(KeloFont.body).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                if !item.options.isEmpty {
                    Text(item.options).font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            VStack(alignment: .trailing, spacing: 1) {
                if !item.priceText.isEmpty {
                    Text(item.priceText).font(KeloFont.body.weight(.semibold)).foregroundStyle(c.ink).multilineTextAlignment(.trailing)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Text(L(R.string.order_quantity, item.quantity)).font(KeloFont.small).foregroundStyle(c.muted)
            }
            .frame(maxWidth: 140, alignment: .trailing)
        }
        .accessibilityElement(children: .combine)
    }
}

/// An amount without trailing zeros ("24000", "24.5") for a total the server sent no text for.
enum OrderMoney {
    static func number(_ v: Double) -> String {
        v == v.rounded() && abs(v) < 1e15 ? String(Int64(v)) : String(v)
    }
}

/// One part of a card: a small icon and title, then its lines (indented under the title).
struct CardPart<Content: View>: View {
    let icon: KeloIcon
    let title: String
    let content: Content
    @Environment(\.kelo) private var c

    init(_ icon: KeloIcon, _ title: String, @ViewBuilder content: () -> Content) { self.icon = icon; self.title = title; self.content = content() }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                KeloIconView(icon, size: 16, tint: c.muted)
                Text(title).font(KeloFont.label).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
            }
            VStack(alignment: .leading, spacing: 2) { content }.padding(.leading, 22)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
