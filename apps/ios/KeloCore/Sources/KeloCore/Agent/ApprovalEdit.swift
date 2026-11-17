import Foundation

// The outgoing message of an approval card the owner may fix before allowing it (Android ApprovalEdit.kt; server approval_edit.py).
// Only a tool that SENDS a message (a mail's body, a text) carries it; a payment never does. The server checks the same limits again.

/// The card's "editable" object: `field` is the argument the server replaces, `text` the message as it stands, `max` the service's own
/// limit in characters (a text message) or nil.
public struct EditableMessage: Equatable, Sendable {
    public var field: String
    public var text: String
    public var max: Int?

    public init(field: String, text: String, max: Int? = nil) {
        self.field = field; self.text = text; self.max = max
    }

    /// nil when the object is missing or has no field or text (an older server sends none).
    public static func parse(_ j: JSON?) -> EditableMessage? {
        guard let o = j?.objectValue,
              let field = o["field"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !field.isEmpty,
              let text = o["text"]?.stringValue else { return nil }
        return EditableMessage(field: field, text: text, max: o["max"]?.intValue)
    }
}

/// What is wrong with an edited message, as the sheet says it.
public enum EditProblem: Equatable, Sendable {
    case empty, tooLong
}

public enum ApprovalEditRules {
    /// The text as the server counts it (its characters are code points, the Swift unicode scalars).
    static func length(_ s: String) -> Int { s.unicodeScalars.count }

    static func trimmed(_ s: String) -> String { s.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// Nothing to save: blank, or over the service's limit.
    public static func problem(_ text: String, max: Int?) -> EditProblem? {
        if trimmed(text).isEmpty { return .empty }
        if let max, length(trimmed(text)) > max { return .tooLong }
        return nil
    }

    /// "n / max" for the counter, only when the service has a limit.
    public static func counter(_ text: String, max: Int?) -> (count: Int, max: Int)? {
        max.map { (length(trimmed(text)), $0) }
    }

    /// What the card shows as the message: the owner's saved text, else the server's.
    public static func shown(_ e: EditableMessage, draft: String?) -> String { draft ?? e.text }

    /// The lines without the one that is the message itself (the card shows the message in its own block).
    public static func withoutMessage(_ lines: [ApprovalLine], _ e: EditableMessage) -> [ApprovalLine] {
        lines.filter { trimmed($0.value) != trimmed(e.text) }
    }

    /// The text to send as the edit, or nil when nothing changed (a saved text equal to the original is no edit).
    public static func toSend(_ e: EditableMessage?, draft: String?) -> String? {
        guard let e, let draft, trimmed(draft) != trimmed(e.text) else { return nil }
        return trimmed(draft)
    }
}

/// The texts the owner fixed in approval cards, by approval id, until the answer goes out. The card saves the sheet here; every place
/// that answers the approval reads it with `edited` and clears it.
@MainActor public enum ApprovalDrafts {
    private static var drafts: [String: String] = [:]

    public static func get(_ id: String) -> String? { drafts[id] }
    public static func set(_ id: String, _ text: String) { drafts[id] = text }
    public static func clear(_ id: String) { drafts.removeValue(forKey: id) }

    /// The `edited` object of an Allow for this card: {field: the saved text}, or nil when there is nothing to send.
    public static func edited(_ id: String, _ e: EditableMessage?) -> [String: JSON]? {
        guard let e, let text = ApprovalEditRules.toSend(e, draft: drafts[id]) else { return nil }
        return [e.field: .string(text)]
    }
}
