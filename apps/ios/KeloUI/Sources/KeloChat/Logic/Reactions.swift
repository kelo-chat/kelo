import Foundation

/// The emoji on the user's own messages (Android chat/Reactions.kt), set by the server (reactions.py): `read` (👀) once Kelo starts
/// on a message, then one emoji that fits what was done or talked about when the work for it is over (✅ when the model chose none,
/// ⚠️ when the reply failed or was stopped). Work handed to a background job, or waiting for the user's answer, keeps 👀 until it ends.
/// It comes with the stored messages (GET /api/messages `reaction`) and live in a reply's events (`.reaction`).
public enum Reactions {
    public static let read = "\u{1F440}"   // 👀

    /// The row the reaction for message `messageId` goes on: the user's message with that id, else (sent from this phone, no id on
    /// screen yet) the newest of the user's messages without an id. nil when there is none.
    public static func target(_ items: [ChatItem], messageId: Int64) -> Int? {
        if let i = items.lastIndex(where: { if case .msg(let m) = $0 { return m.mine && m.id == messageId }; return false }) { return i }
        return items.lastIndex(where: { if case .msg(let m) = $0 { return m.mine && m.id == nil }; return false })
    }

    /// Whether `emoji` is the "read" mark (Kelo is still on it), not a final one.
    public static func isRead(_ emoji: String) -> Bool { emoji == read }

    /// Where the reaction chip starts, measured from the bubble's start edge: `inset` in from the bubble's end edge, or centred under
    /// the bubble when that would bring it closer than that to the start edge (a short bubble: "ㅇㅇ", one emoji). Android
    /// Reactions.chipStart; the layout places it from the start, so in right-to-left it is mirrored to the left end.
    public static func chipStart(bubbleWidth: CGFloat, chipWidth: CGFloat, inset: CGFloat) -> CGFloat {
        max(bubbleWidth - inset - chipWidth, (bubbleWidth - chipWidth) / 2)
    }

    /// What VoiceOver says for the chip: `readWord` ("읽음" / "Read") for 👀, else `reacted` with the emoji filled in.
    public static func spoken(_ emoji: String, readWord: String, reacted: (String) -> String) -> String {
        isRead(emoji) ? readWord : reacted(emoji)
    }
}
