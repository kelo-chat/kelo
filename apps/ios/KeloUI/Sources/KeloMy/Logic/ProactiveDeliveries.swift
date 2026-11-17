import Foundation
import KeloCore

/// One item Kelo is keeping for a later message (GET /api/proactive/held). `why`: slots | over_cap | waiting_for_reply.
/// Android my/ProactiveDeliveries.kt HeldItem.
public struct HeldItem: Decodable, Equatable, Sendable {
    public var key = "", source = "", title = "", message = "", why = ""
    public var urgency: String?
    public var heldAt = 0.0, expiresAt = 0.0

    public init(key: String = "", source: String = "", title: String = "", urgency: String? = nil, message: String = "", why: String = "",
                heldAt: Double = 0, expiresAt: Double = 0) {
        self.key = key; self.source = source; self.title = title; self.urgency = urgency; self.message = message; self.why = why
        self.heldAt = heldAt; self.expiresAt = expiresAt
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        key = j.str("key"); source = j.str("source"); title = j.str("title"); urgency = j.optStr("urgency"); message = j.str("message")
        why = j.str("why"); heldAt = j.double("held_at"); expiresAt = j.double("expires_at")
    }
}

/// GET /api/proactive/held: the items; an entry that cannot be read is left out.
public struct HeldList: Decodable, Equatable, Sendable {
    public var items: [HeldItem] = []
    public init(items: [HeldItem] = []) { self.items = items }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = j.list("items")
    }
}

/// The delivery count and the held items, apart from the UI (Android my/ProactiveDeliveries.kt DeliveryRules).
public enum DeliveryRules {
    public static let min = 0, max = 10

    public enum Why: Equatable, Sendable { case slots, overCap, waitingForReply, other }

    /// The server's reason code as a case; a code this app does not know (or none) is `.other`.
    public static func why(_ code: String) -> Why {
        switch code.trimmingCharacters(in: .whitespaces) {
        case "slots": .slots
        case "over_cap": .overCap
        case "waiting_for_reply": .waitingForReply
        default: .other
        }
    }

    /// The choice kept inside 0...10.
    public static func clamp(_ n: Int) -> Int { Swift.min(Swift.max(n, min), max) }

    /// Whether the server has the delivery count at all (an older one sends no such field: the rows are hidden).
    public static func hasCount(_ p: ProactiveSettings) -> Bool { p.dailyDeliveries != nil }

    /// Whether the server counts held items (and so has the list).
    public static func hasHeld(_ p: ProactiveSettings) -> Bool { p.held != nil }

    /// The line over a held item: its title, else the first line of its message.
    public static func heading(_ item: HeldItem) -> String {
        let title = item.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !title.isEmpty { return title }
        return item.message.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "\n", omittingEmptySubsequences: true).first.map(String.init) ?? ""
    }

    /// The message with blank lines collapsed; empty when it only repeats the heading.
    public static func preview(_ item: HeldItem) -> String {
        let text = item.message.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: "\n", omittingEmptySubsequences: true)
            .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }.joined(separator: "\n")
        return text == heading(item) ? "" : text
    }

    /// Whether a deadline falls on the same local day as `now` (then it is shown as a time only).
    public static func sameDay(_ seconds: Double, now: Double, zone: TimeZone = .current) -> Bool {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        return cal.isDate(Date(timeIntervalSince1970: seconds), inSameDayAs: Date(timeIntervalSince1970: now))
    }
}
