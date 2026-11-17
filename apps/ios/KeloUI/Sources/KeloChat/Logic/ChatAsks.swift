import Foundation
import KeloCore

/*
 * Questions waiting on the user that the chat is not following live (Android chat/ChatAsks.kt): a background job's approval (the
 * reply only handed the job off), a scheduled run's, or a reply this phone stopped following. They come from
 * GET /api/approvals/pending, are shown at the bottom of the chat as live cards with the same answers as the 작업 page, and leave
 * when answered there or here. A card the followed reply already shows is not shown twice.
 */

/// What asked, for the small line on top of the card.
public enum AskOrigin: Sendable { case job, schedule, auto, chat }

/// How a question answered from the chat ended. solved / skipped: a human check (solved in the live view, or the site skipped). progressed: a sign-in's step done, the sign-in goes on.
public enum AskEnd: Sendable { case allowed, always, alwaysRecipient, keptForSchedule, denied, gone, solved, skipped, progressed }

/// One waiting question in the chat. `working`: the answer (or the card for a payment) is on its way. `end`: answered here; its short
/// line stays while `endedAfter` (the chat's newest row then) is still the newest row, so it goes once the conversation moves on.
public struct ChatAsk: Equatable, Sendable, Identifiable {
    public var approval: PendingApproval
    public var working = false
    public var cardError: CardSendError?
    public var end: AskEnd?
    public var endedAfter: String?

    public init(approval: PendingApproval, working: Bool = false, cardError: CardSendError? = nil, end: AskEnd? = nil, endedAfter: String? = nil) {
        self.approval = approval; self.working = working; self.cardError = cardError; self.end = end; self.endedAfter = endedAfter
    }

    public var id: String { approval.id }
    public var open: Bool { end == nil }
}

public enum ChatAsks {
    public static func origin(_ a: PendingApproval) -> AskOrigin {
        if !(a.workerId ?? "").trimmingCharacters(in: .whitespaces).isEmpty || a.source == "worker" { return .job }
        if a.source.hasPrefix("schedule") { return .schedule }
        if a.unattended { return .auto }
        return .chat
    }

    /// A fresh pending list joined with what the chat shows: new questions join, open ones answered elsewhere (or expired) leave,
    /// one whose answer is on its way stays until that settles, ones answered here keep their line while `newestKey` has not
    /// changed. Oldest question first.
    public static func merge(_ current: [ChatAsk], pending: [PendingApproval], newestKey: String?, resolved: [String: String] = [:]) -> [ChatAsk] {
        let ids = Set(pending.map(\.id))
        // a human check that ended on the server (solved in the live view, skipped elsewhere, ran out) folds to its outcome here
        let folded = current.map { a -> ChatAsk in
            guard a.open, !ids.contains(a.id), let out = resolved[a.id] else { return a }
            var b = a
            b.working = false; b.cardError = nil; b.end = resolvedEnd(out); b.endedAfter = newestKey
            return b
        }
        var known: [String: ChatAsk] = [:]
        for a in folded { known[a.id] = a }
        let live = pending.map { p -> ChatAsk in
            if var k = known[p.id] {
                if k.open { k.approval = p }
                return k
            }
            return ChatAsk(approval: p)
        }
        let rest = folded.filter { !ids.contains($0.id) && (($0.open && $0.working) || (!$0.open && $0.endedAfter == newestKey)) }
        return (live + rest).enumerated().sorted { l, r in
            l.element.approval.askedAt == r.element.approval.askedAt ? l.offset < r.offset : l.element.approval.askedAt < r.element.approval.askedAt
        }.map(\.element)
    }

    /// What the chat draws: not a question the followed reply shows as its own card, and an ended line only until the chat moves on.
    public static func visible(_ asks: [ChatAsk], items: [ChatItem]) -> [ChatAsk] {
        if asks.isEmpty { return asks }
        let own = Set(items.compactMap { $0.asApproval?.id })
        let newest = items.last?.key
        return asks.filter { !own.contains($0.id) && ($0.open || $0.endedAfter == newest) }
    }

    public static func working(_ asks: [ChatAsk], _ id: String, on: Bool, error: CardSendError? = nil) -> [ChatAsk] {
        asks.map { a in
            guard a.id == id else { return a }
            var b = a
            b.working = on; b.cardError = error
            return b
        }
    }

    public static func ended(_ asks: [ChatAsk], _ id: String, _ end: AskEnd, newestKey: String?) -> [ChatAsk] {
        asks.map { a in
            guard a.id == id else { return a }
            var b = a
            b.working = false; b.cardError = nil; b.end = end; b.endedAfter = newestKey
            return b
        }
    }

    /// A hand-over's outcome from the server as the card's last line.
    public static func resolvedEnd(_ outcome: String) -> AskEnd {
        switch outcome {
        case "solved": .solved
        case "skipped": .skipped
        case "progressed": .progressed
        default: .gone
        }
    }

    /// How an answer given here ended: a human check's "skip" is skipped (its "done" from an older card, solved).
    public static func answerEnd(_ a: PendingApproval, approve: Bool, rememberSite: Bool, rememberSchedule: Bool, rememberRecipient: Bool = false) -> AskEnd {
        if a.isHumanCheck {
            if approve {
                return a.isVerification ? .progressed : .solved
            }
            return .skipped
        }
        if !approve { return .denied }
        return allowEnd(a, rememberSite: rememberSite, rememberSchedule: rememberSchedule, rememberRecipient: rememberRecipient)
    }

    /// How an allow ended: remembered for the site or the scheduled job only when the card offered it.
    public static func allowEnd(_ a: PendingApproval, rememberSite: Bool, rememberSchedule: Bool, rememberRecipient: Bool = false) -> AskEnd {
        if rememberRecipient && RecipientRules.canAlways(a.event.offers) { return .alwaysRecipient }
        if rememberSite && a.canRemember { return .always }
        if rememberSchedule && a.canRememberSchedule { return .keptForSchedule }
        return .allowed
    }

    /// When the pending list is asked again: the status poll's count of waiting approvals or of unseen notifications changed.
    public static func pollKey(_ s: AgentStatus?) -> String? { s.map { "\($0.pendingApprovals)/\($0.unseenNotifications)" } }

    /// Nothing waits and nothing open is shown: the list need not be asked for, it is simply empty.
    public static func skipFetch(_ s: AgentStatus?, _ asks: [ChatAsk]) -> Bool {
        guard let s else { return false }
        return s.pendingApprovals == 0 && !asks.contains { $0.open }
    }
}
