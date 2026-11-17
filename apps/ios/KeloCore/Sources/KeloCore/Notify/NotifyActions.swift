import Foundation

/// The buttons under a phone notification that answer without opening the app, apart from UserNotifications so the rules are tested
/// here (Android notify/NotifyActions.kt ApprovalActionRules and ReplyActionRules; the same rules on every app).
public enum NotifyActionRules {
    /// The categories the app registers: Allow + Deny, Deny alone (a payment: its Allow needs the phone's signature in the app), Reply.
    public static let approvalCategory = "kelo.approval"
    public static let denyOnlyCategory = "kelo.approval.deny"
    public static let replyCategory = "kelo.reply"
    /// The action identifiers under those categories.
    public static let allowAction = "kelo.allow"
    public static let denyAction = "kelo.deny"
    public static let replyAction = "kelo.reply.text"

    /// The notify_actions names the server uses.
    public static let allow = "allow"
    public static let deny = "deny"

    /// The category of an approval's notification, from the names the server put on the item's notify_actions; nil = no buttons
    /// (tapping the notification opens the app).
    public static func category(offered: [String]) -> String? {
        if offered.contains(allow) && offered.contains(deny) { return approvalCategory }
        if offered.contains(deny) { return denyOnlyCategory }
        return nil
    }

    /// Whether an answer may be sent at all: an Allow only when the item offers "allow", a Deny only when it offers "deny".
    /// A payment offers "deny" alone, so its Allow is never sent; an item that needs input offers nothing.
    public static func mayAnswer(approve: Bool, offered: [String]) -> Bool {
        offered.contains(approve ? allow : deny)
    }

    /// The decision of an approval button, nil for any other action (the default tap, the reply field).
    public static func decision(action: String) -> Bool? {
        switch action {
        case allowAction: return true
        case denyAction: return false
        default: return nil
        }
    }

    /// What the server's answer means for the notification: accepted (2xx) or already answered (404) takes it down; anything else leaves
    /// "Couldn't send" behind. A status of 0 (not reached) is a failure.
    public static func isDone(status: Int) -> Bool { (200...299).contains(status) || status == 404 }

    /// A typed reply, trimmed; blank text sends nothing.
    public static func cleanReply(_ text: String?) -> String? {
        guard let t = text?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        return t
    }
}
