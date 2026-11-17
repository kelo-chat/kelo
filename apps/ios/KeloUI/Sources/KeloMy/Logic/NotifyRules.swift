import Foundation
import KeloCore
import KeloShared

/// The server's notification types the app turns into phone notifications (Android notify/NotifyRules.kt NoteTypes).
public enum NoteTypes {
    public static let taskDone = "task_done"
    public static let taskFailed = "task_failed"
    public static let approvalWaiting = "approval_waiting"
    public static let paymentAuto = "payment_auto"
    public static let scheduleDisabled = "schedule_disabled"
    public static let schedulePaused = "schedule_paused"
    public static let watchFired = "watch_fired"
    public static let watchDisabled = "watch_disabled"
    public static let proactive = "proactive"
    public static let followup = "followup"
    /// Someone asked to try Google sign-in (only on the operator's server; title and body name the address)
    public static let betaRequest = "beta_request"
    /// A newer claude / agy CLI has been out for a few days (title and body from the server: the versions and the update command)
    public static let cliUpdate = "cli_update"
    /// The Kelo server itself: a newer version is out, or a (nightly) update finished or went back (title and body from the server)
    public static let serverUpdate = "server_update"
    /// Storage is filling up: 80% / 95% of the cap, or the server's own disk almost full (title and body from the server)
    public static let storage = "storage"
    /// The morning briefing (title "아침 브리핑", body its one-line summary); its card waits in the chat
    public static let briefing = "briefing"
    /// Keyguard: the server restarted and holds no key until an owner phone unlocks it (urgent)
    public static let keyguardLocked = "keyguard_locked"
    public static let all: Set<String> = [taskDone, taskFailed, approvalWaiting, paymentAuto, scheduleDisabled, schedulePaused,
                                          watchFired, watchDisabled, proactive, followup, betaRequest, cliUpdate, serverUpdate, storage,
                                          briefing, keyguardLocked]
}

/// Which group a notification belongs to (Android channels; on iOS the thread identifier that bundles them).
public enum NoteChannel: String, Sendable {
    /// `urgent`: a card of a task marked urgent at setup (a cancelled seat): time matters, posted time-sensitive
    case replies, approvals, payments, schedules, urgent
}

/// When and how a local notification is posted (Android NotifyRules). Nothing while the app is on screen, nothing the server already
/// counts as seen, nothing twice.
public enum NotifyRules {
    /// Every Kelo notification is in one group (Android NotifyRules.GROUP): the phone stacks them, a tap on the stack shows them all.
    public static let thread = "kelo"

    /// The userInfo key that keeps a local notification's channel, so opening the app knows which ones were only news.
    public static let channelKey = "channel"

    /// Notifications that only tell something (a reply, a finished job, a schedule's result, payment news): opening the app takes them
    /// down. Ones that wait for an answer (an approval, an urgent card) stay until answered (Android NotifyRules.isNews).
    public static func isNews(_ channel: NoteChannel) -> Bool { channel == .replies || channel == .schedules || channel == .payments }

    /// The identifiers of delivered notifications that were only news, from their userInfo (pure, so it is tested). Notifications
    /// without a channel (a dial request, a push banner) are left alone.
    public static func newsToTakeDown(_ delivered: [(id: String, userInfo: [AnyHashable: Any])]) -> [String] {
        delivered.compactMap { d in
            guard let raw = d.userInfo[channelKey] as? String, let ch = NoteChannel(rawValue: raw), isNews(ch) else { return nil }
            return d.id
        }
    }

    public static func channel(_ type: String, scheduleId: Int64?) -> NoteChannel {
        switch type {
        // asked again about something that waits for the owner: grouped with the approvals (Android: the channel that may badge)
        case NoteTypes.approvalWaiting, NoteTypes.followup: .approvals
        case NoteTypes.paymentAuto: .payments
        case NoteTypes.scheduleDisabled, NoteTypes.schedulePaused, NoteTypes.watchFired, NoteTypes.watchDisabled: .schedules
        case NoteTypes.proactive, NoteTypes.briefing: .replies
        default: scheduleId != nil ? .schedules : .replies
        }
    }

    /// The group of a server notification: an urgent one (a task marked urgent at setup) has its own, time-sensitive one.
    public static func channelOf(_ type: String, scheduleId: Int64?, urgent: Bool) -> NoteChannel {
        urgent ? .urgent : channel(type, scheduleId: scheduleId)
    }

    /// Where a server notification opens.
    public static func target(_ n: ServerNotification) -> OpenTarget {
        switch n.type {
        // an approval waits in the chat as a live card at the bottom (a background job's or a scheduled run's too)
        case NoteTypes.approvalWaiting: return .chat
        case NoteTypes.paymentAuto: return .wallet
        case NoteTypes.scheduleDisabled, NoteTypes.schedulePaused: return n.scheduleId.map { .work(.schedule($0)) } ?? .work(.schedules)
        case NoteTypes.watchFired, NoteTypes.watchDisabled: return .work(.watchers)
        case NoteTypes.proactive, NoteTypes.briefing: return .chat
        case NoteTypes.followup: return n.messageId.map { .chatAt($0) } ?? .chat
        case NoteTypes.betaRequest: return .operatorPage
        case NoteTypes.cliUpdate: return .modelSettings(n.provider)   // at that CLI's box (an older server names none)
        case NoteTypes.serverUpdate: return .serverUpdate
        case NoteTypes.keyguardLocked: return .unlock   // Kelo is locked: unlock it with this phone
        case NoteTypes.storage: return .storage   // 마이 → Kelo 서버 자원 (its 정리하기 action: the cleanup screen)
        default: return n.taskId.map { .work(.task($0)) } ?? .chat
        }
    }

    /// The buttons under a notification besides tapping it: a storage warning offers 정리하기 (the cleanup screen).
    public static func actions(_ type: String) -> [OpenTarget] { type == NoteTypes.storage ? [.storageCleanup] : [] }

    /// The ones to post from a page of notifications: every type (also ones this app does not know yet), not seen, newer than `lastPosted`; oldest first.
    public static func fresh(_ items: [ServerNotification], lastPosted: Int64) -> [ServerNotification] {
        items.filter { $0.id > lastPosted && !$0.seen }.sorted { $0.id < $1.id }
    }

    /// The title of a notification of a type this app does not know yet: the server's own, else the generic "open Kelo" hint (nil for a known type).
    public static func unknownTitle(_ n: ServerNotification, hint: String) -> String? {
        NoteTypes.all.contains(n.type) ? nil : (n.title.isEmpty ? hint : n.title)
    }

    /// The cursor after a check: never backwards.
    public static func nextCursor(_ lastPosted: Int64, serverLast: Int64, items: [ServerNotification]) -> Int64 {
        max(lastPosted, serverLast, items.map(\.id).max() ?? 0)
    }

    /// The first line of an answer, Markdown marks off, at most `max` characters.
    public static func firstLine(_ answer: String, max: Int = 120) -> String {
        var inCode = false
        var line = ""
        for raw in answer.components(separatedBy: .newlines) {
            let t = raw.trimmingCharacters(in: .whitespaces)
            if t.hasPrefix("```") { inCode.toggle(); continue }
            if inCode || t.isEmpty || t.range(of: #"^[-*_=|:\s]+$"#, options: .regularExpression) != nil { continue }
            line = t
            break
        }
        var plain = line
        for (p, r) in [(#"^#{1,6}\s+"#, ""), (#"^([-*+]|\d+[.)])\s+"#, ""), (#"^>\s*"#, ""), (#"\[([^\]]*)\]\([^)]*\)"#, "$1"), (#"[*_`~]+"#, "")] {
            plain = plain.replacingOccurrences(of: p, with: r, options: .regularExpression)
        }
        plain = plain.trimmingCharacters(in: .whitespaces)
        if plain.count <= max { return plain }
        return String(plain.prefix(max - 1)).trimmingCharacters(in: .whitespaces) + "…"
    }

    /// The id of the waiting item a notification is about (the server's approval id), or nil when it only tells something. The same
    /// scheme on every app: Android tags its notification with it, the iPhone uses it as the notification's identifier, the desktop keys
    /// its notes by it. So one list of settled ids (a settled push, or GET /api/notifications/settled) works everywhere.
    public static func itemId(_ n: ServerNotification) -> String? {
        guard let a = n.approvalId?.trimmingCharacters(in: .whitespaces), !a.isEmpty else { return nil }
        return a
    }

    /// The waiting item of a delivered notification: a local one keeps it under `itemKey`, a push carries it as "approval_id".
    public static func approvalItem(userInfo: [AnyHashable: Any]) -> String? {
        let raw = (userInfo[itemKey] as? String) ?? (userInfo["approval_id"] as? String)
        guard let raw, !raw.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return raw
    }

    /// The userInfo key that keeps a local notification's waiting item id (only notifications that wait for an item have one).
    public static let itemKey = "item"

    /// The item ids of the delivered notifications that wait for an item: what to ask the server about. A notification with no item
    /// (news, a follow-up, a bare push) is never asked about, so the server's "unknown id = settled" never takes it down.
    public static func itemsToAskAbout(_ delivered: [(id: String, userInfo: [AnyHashable: Any])]) -> [String] {
        delivered.compactMap { d in
            guard let item = d.userInfo[itemKey] as? String, !item.isEmpty, d.id == item else { return nil }
            return item
        }
    }

    /// The notification's own id: a waiting item's own id (see [itemId]); else per kind, so one task never shows twice and a newer
    /// one replaces the older.
    public static func identifier(_ n: ServerNotification) -> String {
        if let item = itemId(n) { return item }
        if let t = n.taskId { return "task:\(t)" }
        return "note:\(n.id)"
    }
}
