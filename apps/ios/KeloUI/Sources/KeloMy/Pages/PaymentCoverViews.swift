import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 권한 → "Payments approved in advance" (Android agent/PaymentCoverParts.kt): the switch (off: every payment asks), each
/// order approved in advance (what, the all-in cap, until when, for which task or schedule) and each schedule's payment allow
/// (schedule, shop, payment method), every one removable. Rows stay listed while the switch is off (they are just not used).
struct PaymentCoverSection: View {
    let cover: PaymentCover
    let onSwitch: (Bool) -> Void
    let onRemoveOrder: (Preapproval) -> Void
    let onRemoveAllow: (PayAllow) -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        MenuCard {
            ToggleRow(L(R.string.paycover_switch), detail: L(R.string.paycover_switch_note),
                      isOn: Binding(get: { cover.enabled }, set: { v in Haptics.tick(); onSwitch(v) }))
        }
        if cover.isEmpty {
            MutedText(L(R.string.paycover_empty), small: true)
        } else {
            if !cover.enabled { MutedText(L(R.string.paycover_off_note), small: true) }
            if !cover.orders.isEmpty {
                MutedText(L(R.string.paycover_orders), small: true).padding(.top, 8)
                MenuCard {
                    ForEach(Array(cover.orders.enumerated()), id: \.element.id) { i, p in
                        if i > 0 { Hairline() }
                        orderRow(p)
                    }
                }
            }
            if !cover.allows.isEmpty {
                MutedText(L(R.string.paycover_allows), small: true).padding(.top, 8)
                MenuCard {
                    ForEach(Array(cover.allows.enumerated()), id: \.element.id) { i, a in
                        if i > 0 { Hairline() }
                        allowRow(a)
                    }
                }
            }
        }
    }

    private func orderRow(_ p: Preapproval) -> some View {
        let what = TokenText.fit(PaymentCoverRules.what(intent: p.intent, items: p.items), max: 40)
        return HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(what).font(KeloFont.bodyLarge.weight(.medium)).foregroundStyle(c.ink).lineLimit(3)
                Text(L(R.string.paycover_cap, MyFormat.money(p.maxTotal, currency: p.currency))).font(KeloFont.small).foregroundStyle(c.ink)
                if p.expiresAt > 0 { Text(L(R.string.paycover_until, MyFormat.dayTime(Double(p.expiresAt)))).font(KeloFont.small).foregroundStyle(c.muted) }
                Text("\(preapprovalFor(schedule: p.forSchedule, name: p.ownerName)) · \(L(R.string.paycover_one_order))")
                    .font(KeloFont.label).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trash(L(R.string.paycover_remove, what)) { onRemoveOrder(p) }
        }
        .padding(.vertical, 10)
        .accessibilityIdentifier("kelo.paycover.order")
    }

    private func allowRow(_ a: PayAllow) -> some View {
        let name = a.scheduleName.isEmpty ? L(R.string.paycover_for_a_schedule) : a.scheduleName
        return HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(TokenText.fit(name, max: 40)).font(KeloFont.bodyLarge.weight(.medium)).foregroundStyle(c.ink).lineLimit(2)
                Text([a.shop, a.paymentLabel].filter { !$0.isEmpty }.map { TokenText.fit($0, max: 30) }.joined(separator: " · "))
                    .font(KeloFont.small).foregroundStyle(c.ink).lineLimit(3)
                Text(L(R.string.paycover_allow_scope)).font(KeloFont.label).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            trash(L(R.string.paycover_remove, name)) { onRemoveAllow(a) }
        }
        .padding(.vertical, 10)
        .accessibilityIdentifier("kelo.paycover.allow")
    }

    private func trash(_ label: String, _ action: @escaping () -> Void) -> some View {
        Button(action: action) { KeloIconView(.trash, size: 20, tint: c.muted).frame(width: 40, height: 40) }
            .buttonStyle(.plain).accessibilityLabel(label)
    }
}
