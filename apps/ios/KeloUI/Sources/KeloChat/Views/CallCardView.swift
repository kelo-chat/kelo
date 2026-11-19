import SwiftUI
import KeloShared

/// A call in the chat: the handset, "Call · 3 min · 2:10 PM", the owner's first words; a tap opens the call's lines (owner 2026-11-20).
struct CallCardView: View {
    let card: ChatItem.CallCard
    let open: Bool
    let agentName: String
    let onToggle: () -> Void
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onToggle) {
                HStack(spacing: 10) {
                    KeloIconView(.handset, size: 18, tint: c.accent)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(KeloFont.titleSmall).foregroundStyle(c.ink).lineLimit(1)
                        let preview = CallCards.preview(card)
                        if !preview.isEmpty { Text(preview).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1) }
                    }
                    Spacer(minLength: 0)
                    KeloIconView(.chevronDown, size: 18, tint: c.muted).rotationEffect(.degrees(open ? 180 : 0))
                }
                .padding(.horizontal, 14).padding(.vertical, 12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(L(open ? R.string.call_chat_hide : R.string.call_chat_show))
            .accessibilityIdentifier("chat.call.head")
            if open {
                VStack(spacing: 8) {
                    ForEach(Array(card.lines.enumerated()), id: \.offset) { i, m in
                        if !m.text.isEmpty { CallLineView(line: CallLine(id: i, mine: m.mine, text: m.text), agentName: agentName) }
                    }
                }
                .padding(.horizontal, 12).padding(.bottom, 12)
                .accessibilityIdentifier("chat.call.lines")
            }
        }
        .background(RoundedRectangle(cornerRadius: 16).fill(c.card))
        .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.line, lineWidth: 1))
        .accessibilityIdentifier("chat.call.card")
    }

    private var title: String {
        let n = CallCards.minutes(card)
        let length = P(R.plurals.call_chat_length, n, n)
        guard let first = card.lines.first, first.at > 0 else { return length }
        let time = Date(timeIntervalSince1970: Double(first.at) / 1000).formatted(Date.FormatStyle(date: .omitted, time: .shortened).locale(appLocale()))
        return "\(length) · \(time)"
    }
}
