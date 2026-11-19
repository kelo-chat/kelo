import Foundation

/// The chat's call cards (owner 2026-11-20): the messages of one in-app call (stored with via=call and the call's id, server calls.py)
/// show as ONE row, `ChatItem.call`, that opens to the call's lines. The messages themselves are unchanged (Kelo's memory of the call
/// too); only what the list draws is folded. An ordinary message or another call's message between two lines ends the card (the rest
/// of the call makes a second card); rows that are not messages (tool chips, approval cards) stay in the list after the card.
public enum CallCards {
    public static func fold(_ items: [ChatItem]) -> [ChatItem] {
        guard items.contains(where: { $0.asMsg?.callId != nil }) else { return items }
        var out: [ChatItem] = []
        out.reserveCapacity(items.count)
        var openAt = -1
        var openCall: String?
        for row in items {
            guard let m = row.asMsg else { out.append(row); continue }
            guard let call = m.callId else { openCall = nil; out.append(row); continue }
            if call == openCall, case .call(var card) = out[openAt] {
                card.lines.append(m)
                out[openAt] = .call(card)
            } else {
                openCall = call
                openAt = out.count
                out.append(.call(ChatItem.CallCard(key: "call:\(call):\(m.key)", callId: call, lines: [m])))
            }
        }
        return out
    }

    /// The rows a reply event added to the list (`after` minus `before`) that are messages get the call of the turn (`callId`).
    public static func tagNew(before: [ChatItem], after: [ChatItem], callId: String) -> [ChatItem] {
        let old = Set(before.map(\.key))
        return after.map { r in
            guard var m = r.asMsg, m.callId == nil, !old.contains(m.key) else { return r }
            m.callId = callId
            m.fromCall = true
            return .msg(m)
        }
    }

    /// The card in `rows` that holds message `id`, or nil.
    public static func cardWith(_ rows: [ChatItem], id: Int64) -> ChatItem.CallCard? {
        for r in rows {
            if let c = r.asCall, c.lines.contains(where: { $0.id == id }) { return c }
        }
        return nil
    }

    /// The call's length for its title in whole minutes: from its first line to its last, rounded, at least 1.
    public static func minutes(_ card: ChatItem.CallCard) -> Int {
        guard let first = card.lines.first?.at, let last = card.lines.last?.at else { return 1 }
        if first <= 0 || last <= first { return 1 }
        return max(1, Int((last - first + 30_000) / 60_000))
    }

    /// One line under the title: the owner's first words, else Kelo's; spaces and line breaks folded to one space.
    public static func preview(_ card: ChatItem.CallCard) -> String {
        let line = card.lines.first(where: { $0.mine && !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
            ?? card.lines.first(where: { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty })
        guard let text = line?.text else { return "" }
        return text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
    }
}
