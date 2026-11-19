import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 알림 → the items Kelo keeps for a later message (read-only): title, message preview, why it is held, and until when
/// (Android my/ProactiveDeliveries.kt ProactiveHeldPage).
struct ProactiveHeldPage: View {
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var model: ProactiveHeldModel

    init(api: KeloApi) { _model = State(initialValue: ProactiveHeldModel(api: api)) }

    var body: some View {
        SubPage(L(R.string.deliveries_held_title), onBack: { dismiss() }) {
            if !model.error.isEmpty {
                ErrorText(model.error, small: true)
            } else if model.missing {
                MutedText(L(R.string.notify_page_unavailable), small: true)
            } else if let items = model.items {
                if items.isEmpty {
                    EmptyNote(L(R.string.deliveries_held_empty))
                } else {
                    MutedText(L(R.string.deliveries_held_about), small: true)
                    VStack(spacing: 0) {
                        ForEach(Array(items.enumerated()), id: \.offset) { i, item in
                            if i > 0 { Hairline() }
                            HeldRow(item: item)
                        }
                    }
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
                }
            } else {
                SkeletonRows(rows: 3)
            }
        }
        .task { await model.load() }
    }
}

/// One held item: its title (else the first line of the message), the message, why it waits, and the deadline.
struct HeldRow: View {
    let item: HeldItem
    @Environment(\.kelo) private var c

    var body: some View {
        let preview = DeliveryRules.preview(item)
        VStack(alignment: .leading, spacing: 4) {
            Text(DeliveryRules.heading(item)).font(KeloFont.bodyLarge).foregroundStyle(c.ink).lineLimit(2)
            if !preview.isEmpty { Text(preview).font(KeloFont.body).foregroundStyle(c.muted).lineLimit(3) }
            Text(HeldText.why(DeliveryRules.why(item.why))).font(KeloFont.small).foregroundStyle(c.muted)
            if item.expiresAt > 0 { Text(HeldText.until(item.expiresAt)).font(KeloFont.small).foregroundStyle(c.muted) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14).padding(.vertical, 12)
    }
}

/// The words of the held items.
@MainActor enum HeldText {
    static func why(_ w: DeliveryRules.Why) -> String {
        switch w {
        case .overCap: L(R.string.deliveries_why_cap)
        case .waitingForReply: L(R.string.deliveries_why_reply)
        case .other: L(R.string.deliveries_why_other)
        }
    }

    /// "오후 6:00까지 보관" today, "11월 8일 오전 9:00까지 보관" another day.
    static func until(_ expiresAt: Double, now: Double = Date().timeIntervalSince1970) -> String {
        L(R.string.deliveries_held_until, DeliveryRules.sameDay(expiresAt, now: now) ? MyFormat.clock(expiresAt) : MyFormat.monthDayTime(expiresAt))
    }
}
