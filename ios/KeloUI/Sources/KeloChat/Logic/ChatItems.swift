import Foundation
import KeloCore
import KeloShared

/// What the chat shows, one entry per row (Android chat/ChatItems.kt). Text that comes from the app itself is a kind, resolved in the UI.
public enum ChatItem: Equatable, Sendable, Identifiable {
    case msg(Msg)
    case tool(Tool)
    case approval(Approval)
    case browser(Browser)
    case cardNeeded(CardNeeded)
    case paymentAuto(PaymentAuto)
    case vault(Vault)
    case connect(Connect)
    case shipping(Shipping)
    /// A secure entry the agent asked for (a key, a login or a card), right where it asked; it stays open after the reply ends
    /// (the server keeps it until it is answered) and folds into one line once answered.
    case secret(Secret)
    /// The reply needs this phone's place (location_request).
    case locationAsk(LocationAsk)
    case notice(key: String, ChatNotice)
    case error(key: String, ChatError)

    public var key: String {
        switch self {
        case .msg(let m): m.key
        case .tool(let t): t.key
        case .approval(let a): a.key
        case .browser(let b): b.key
        case .cardNeeded(let c): c.key
        case .paymentAuto(let p): p.key
        case .vault(let v): v.key
        case .connect(let c): c.key
        case .shipping(let s): s.key
        case .secret(let s): s.key
        case .locationAsk(let l): l.key
        case .notice(let k, _): k
        case .error(let k, _): k
        }
    }

    public var id: String { key }

    public var asMsg: Msg? { if case .msg(let m) = self { return m } else { return nil } }
    public var asApproval: Approval? { if case .approval(let a) = self { return a } else { return nil } }
    public var isMine: Bool { asMsg?.mine == true }

    /// A message; `id` is the server's (nil while a reply is still being written here). `at`: when it was written (unix ms; the
    /// server's created_at, or this device's clock for a message made here; 0 = not known, then no time line shows for it).
    public struct Msg: Equatable, Sendable {
        public var key: String
        public var mine: Bool
        public var text: String
        public var id: Int64?
        public var replies: ReplySet?
        public var at: Int64
        /// The emoji Kelo put on the user's message (Reactions); "" none.
        public var reaction: String
        /// The morning briefing's card: shown instead of the plain bubble.
        public var briefing: BriefingCard?
        /// Screenshots Kelo showed with this reply, under its text (a reply of only pictures has no bubble).
        public var images: [ChatImage]
        /// The user's message: the files sent with it (pictures as thumbnails, other files as chips).
        public var files: [MessageFile]
        /// The text is the app's own sentence ("read and summarise …"): only `files` show.
        public var autoText: Bool
        public init(key: String, mine: Bool, text: String, id: Int64? = nil, replies: ReplySet? = nil, at: Int64 = 0, reaction: String = "",
                    briefing: BriefingCard? = nil, images: [ChatImage] = [], files: [MessageFile] = [], autoText: Bool = false) {
            self.key = key; self.mine = mine; self.text = text; self.id = id; self.replies = replies; self.at = at; self.reaction = reaction
            self.briefing = briefing; self.images = images; self.files = files; self.autoText = autoText
        }
    }

    /// The reply needs this phone's place (location_request): Share / Not now, or the phone's settings when the owner refused before
    /// (`settings`). `state`: waiting, sending (this phone is reading and sending it), allowed, refused, expired, cancelled.
    public struct LocationAsk: Equatable, Sendable {
        public static let waiting = "waiting", sending = "sending", allowed = "allowed", refused = "refused", expired = "expired"
        public var key: String, approvalId: String, reason: String, settings: Bool
        public var state: String
        public init(key: String, approvalId: String, reason: String = "", settings: Bool = false, state: String = LocationAsk.waiting) {
            self.key = key; self.approvalId = approvalId; self.reason = reason; self.settings = settings; self.state = state
        }
        public var open: Bool { state == Self.waiting || state == Self.sending }
    }

    /// `label`: the words shown for the tool; an older server sends none, then the raw `name` shows. `group` / `done`: calls of one
    /// group in a row fold into one chip (`ChatReducer.foldTools`) whose words, once more than one is done, are `done` with {n}
    /// filled in. `detail`: what the call was about, in a few words (a search, a site), for the folded list. `members`: on a folded
    /// chip only, the calls it stands for (oldest first); empty on a plain chip.
    public struct Tool: Equatable, Sendable {
        public var key: String, id: String, name: String, state: ToolState, service: String?, label: String
        public var group = ""
        public var done = ""
        public var detail = ""
        public var members: [Tool] = []
        public var shown: String { label.isEmpty ? name : label }
        public var groupKey: String { group.isEmpty ? name : group }
        public var folded: Bool { members.count > 1 }
    }

    /// A tool waiting for the user's yes or no. `answer`: what this phone said (true = allowed), or what the tool's result shows when
    /// answered elsewhere. `expired`: nobody answered and it can no longer be answered.
    public struct Approval: Equatable, Sendable {
        public var key: String, id: String, name: String, args: String, detail: String?
        public var answer: Bool? = nil
        public var expired = false
        public var label = ""
        public var payment: PaymentInfo? = nil
        public var card: ApprovalCardInfo? = nil
        public var send: CardSend = .idle
        public var canRememberSchedule = false
        public var lines: [ApprovalLine] = []
        /// The server's whole question for the title ("" from an older server).
        public var ask = ""
        /// The setup card of a "when X happens, do Y" task (its own layout: trigger, action, limits, the card's auto-pay limit).
        public var grant: TaskGrantAsk? = nil
        /// Asked in a run of a task marked urgent at setup; `missed`: what a late answer would miss.
        public var urgent = false
        public var missed = ""
        /// A permission for many items: the items it covers (behind "see the list").
        public var items: [String] = []
        /// The site's saved payment PIN that Allow also releases from this phone.
        public var pin: PinAsk? = nil

        public var answered: Bool { answer != nil }
        public var open: Bool { answer == nil && !expired }
        public var shown: String { label.isEmpty ? name : label }
        public var needsCard: Bool { card?.needsCard == true }
    }

    /// The assistant's browser tab: ONE live row per reply and tab, updated in place as the agent works (the newest step's status,
    /// site and picture; `id` is the newest step's tool call). `recent`: the last few different pictures, oldest first. `hosts`: the
    /// sites it went through in this reply, in order.
    public struct Browser: Equatable, Sendable {
        public var key: String, id: String, title: String, host: String, url: String, status: String
        public var image: String?
        public var width = 0
        public var height = 0
        public var recent: [String] = []
        /// The tab the pictures came from ("" = an older server: this phone's own tab).
        public var tab = ""
        public var hosts: [String] = []
    }

    /// An allowed payment waits for this phone's card until `expiresAt` (unix seconds); `ended`: the reply is over.
    public struct CardNeeded: Equatable, Sendable {
        public var key: String, approvalId: String, card: CardBrief, site: String, amountText: String, expiresAt: Int64
        public var send: CardSend = .idle
        public var ended = false
    }

    public struct PaymentAuto: Equatable, Sendable {
        public var key: String, site: String, amountText: String, cardLabel: String
        /// The order's items and where it goes, when the server sent them.
        public var order: PaymentInfo? = nil
    }

    /// A secure entry (`request`: what it is for and where the value goes; never the value).
    public struct Secret: Equatable, Sendable {
        public var key: String
        public var request: SecretRequest
        public init(key: String, request: SecretRequest) { self.key = key; self.request = request }
    }

    public struct Vault: Equatable, Sendable {
        public var key: String, name: String
        public var state: CardState = .open
    }

    public struct Connect: Equatable, Sendable {
        public var key: String, service: String
        public var state: CardState = .open
    }

    /// The agent asked where an order goes (shipping_form): the address form. OPEN takes input; DONE folds into one line
    /// (`doneLine`: the nickname and the address); CANCELLED; EXPIRED: past its time, the reply ended, or the server no longer has it.
    /// `error`: the server's 422 detail, shown under the form (the form stays open).
    public struct Shipping: Equatable, Sendable {
        public var key: String
        public var form: TurnEvent.ShippingForm
        public var state: CardState = .open
        public var sending = false
        public var error: String? = nil
        public var doneLine = ""
        public var formId: String { form.formId }
    }
}

public enum ToolState: Sendable { case running, ok, error, denied }

/// What a follow-up button under a message did.
public enum ActionOutcome: Sendable { case approved, denied, resumed, dropped, snoozed, signedIn, gone, resolved }

/// Why sending an end-to-end card did not work (Android wallet CardSendError).
public enum CardSendError: Sendable { case notWaiting, mismatch, notOnPhone, noLock, cancelled, network, other }

/// Sending an end-to-end card for a payment: not yet, on its way, there, or why not.
public enum CardSend: Equatable, Sendable {
    case idle, working, sent
    case failed(CardSendError)
}

/// A secure or connect card. OPEN takes input; DONE and CANCELLED fold into one line; EXPIRED: a newer request replaced it (an address
/// form: its time ran out or the reply ended); CLOSED (an address form only): answered elsewhere, another phone.
public enum CardState: Sendable { case open, done, cancelled, expired, closed }

public enum ChatNotice: Equatable, Sendable {
    case fallback(model: String, reason: String)
    case retry
    case reconnecting
    case uploaded(String)
}

public enum ChatError: Equatable, Sendable {
    case stepLimit
    case message(String)
    /// A 400 when starting a reply: no usable model
    case pickModel(String)
    case network
    case tooLarge
    case unsupported(String)
    case shareFailed
    case cameraDenied
    case cameraMissing
}

public enum PhaseKind: Sendable { case waiting, thinking, writing, tool, approval, switching }

/// What the status line says while a reply is in progress; `since` (seconds) drives the elapsed seconds.
public struct Phase: Equatable, Sendable {
    public var kind: PhaseKind
    public var label: String?
    public var since: Double
    public init(_ kind: PhaseKind, label: String? = nil, since: Double) { self.kind = kind; self.label = label; self.since = since }
}

/// Android ChatReducer: the list after each event, the status line, answers, the end of a reply.
public enum ChatReducer {
    /// A finished tool's chip: denied only when the user said no (approved == false), otherwise error or ok.
    public static func toolState(_ ev: TurnEvent.ToolResult) -> ToolState {
        if ev.approved == false { return .denied }
        return ev.isError ? .error : .ok
    }

    /// Names a vault entry quoted in request_login / request_api_access output, e.g. 'login-example-com'.
    public static func vaultName(_ output: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: "'([a-z0-9][a-z0-9._-]{1,62}[a-z0-9])'") else { return nil }
        let ns = output as NSString
        guard let m = re.firstMatch(in: output, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range(at: 1))
    }

    /// How long (seconds) a reply may stay in its reasoning before the status line says it is a long think (the server's line agrees)
    public static let longThinkingSeconds: Double = 60

    /// Still reasoning `longThinkingSeconds` after the reasoning began: the long-think words instead of "Thinking"
    public static func longThinking(_ p: Phase, now: Double) -> Bool { p.kind == .thinking && now - p.since >= longThinkingSeconds }

    /// The status line after `ev`; the same phase (kind and label) keeps its start time.
    public static func phase(_ current: Phase?, _ ev: TurnEvent, now: Double) -> Phase? {
        func to(_ kind: PhaseKind, _ label: String? = nil) -> Phase {
            if let c = current, c.kind == kind, c.label == label { return c }
            return Phase(kind, label: label, since: now)
        }
        switch ev {
        case .thinking: return to(.thinking)
        case .text: return to(.writing)
        case .toolStarted(let t): return t.hidden ? to(.thinking) : to(.tool, t.label.isEmpty ? t.name : t.label)
        case .toolResult: return to(.waiting)
        case .approvalRequest(let a): return to(.approval, a.label.isEmpty ? a.name : a.label)
        case .fallback(_, let model, _): return to(.switching, model)
        case .retry: return to(.thinking)
        default: return current
        }
    }

    /// The list after one event; `now` (unix ms) dates a message this event starts.
    public static func apply(_ items: [ChatItem], _ ev: TurnEvent, newKey: () -> String, now: Int64 = 0) -> [ChatItem] {
        var next = items
        switch ev {
        case .text(let text):
            if case .msg(var last)? = next.last, !last.mine {
                last.text += text
                next[next.count - 1] = .msg(last)
            } else {
                next.append(.msg(.init(key: newKey(), mine: false, text: text, at: now)))
            }
        case .toolStarted(let t):
            // a hidden call (the assistant reading its own notes) shows nothing at all
            if t.hidden { return items }
            next.append(.tool(.init(key: newKey(), id: t.id, name: t.name, state: .running, service: t.service, label: t.label,
                                    group: t.group, done: t.done, detail: detailOf(t.arguments))))
        case .toolResult(let r):
            // the newest chip with this id that is still running (some CLIs reuse ids from one step to the next)
            var chip: ChatItem.Tool?
            if let i = next.lastIndex(where: { if case .tool(let c) = $0 { return c.id == r.id && c.state == .running }; return false }),
               case .tool(var c) = next[i] {
                c.state = toolState(r)
                next[i] = .tool(c)
                chip = c
            }
            // the assistant asked the user to connect a service: the card appears right here
            if r.name == "connect_service" && !r.isError && !r.output.contains("already connected"), let service = chip?.service {
                supersede(&next) { if case .connect(let c) = $0 { return c.service == service }; return false }
                next.append(.connect(.init(key: newKey(), service: service)))
            }
            // a login or a key was asked for: the secure form appears right here (a refused request shows no form). A server that
            // shows secure entries sent a secret_request for it already: no second form.
            if (r.name == "request_login" || r.name == "request_api_access") && r.approved != false && !r.isError,
               let name = vaultName(r.output),
               !next.contains(where: { if case .secret(let x) = $0 { return x.request.name == name }; return false }) {
                supersede(&next) { if case .vault(let v) = $0 { return v.name == name }; return false }
                next.append(.vault(.init(key: newKey(), name: name)))
            }
            // the address question's tool finished while its form is still open here: it was answered elsewhere (or gave up)
            if !r.id.isEmpty, let f = next.lastIndex(where: { if case .shipping(let x) = $0 { return x.state == .open && x.form.id == r.id }; return false }),
               case .shipping(var x) = next[f] {
                x.state = .closed
                x.sending = false
                next[f] = .shipping(x)
            }
            // a question still open here was settled elsewhere or ran out: its tool's result says which
            if let approved = r.approved,
               let q = next.lastIndex(where: { if case .approval(let a) = $0 { return a.open && a.name == r.name }; return false }),
               case .approval(var a) = next[q] {
                if approved { a.answer = true } else { a.expired = true }
                next[q] = .approval(a)
            }
        case .approvalRequest(let a):
            next.append(.approval(.init(
                key: newKey(), id: a.approvalId, name: a.name, args: TurnEvent.prettyArgs(a.arguments), detail: a.detail,
                label: a.label, payment: a.payment, card: a.card,
                canRememberSchedule: a.canRememberSchedule && a.payment == nil, lines: a.lines, ask: a.ask,
                grant: a.grant, urgent: a.urgent, missed: a.missed, items: a.items, pin: a.pin)))
        case .browser(let b):
            // one live card per reply and tab: a new step, or another site in the same tab, updates it; only a new tab adds one
            let start = (next.lastIndex(where: \.isMine) ?? -1) + 1
            var i: Int?
            if start < next.count {
                i = (start..<next.count).reversed().first { j in
                    guard case .browser(let x) = next[j] else { return false }
                    return x.id == b.id || x.tab == b.tab || (b.tab.isEmpty && !x.tab.isEmpty) || (x.tab.isEmpty && !b.tab.isEmpty)
                }
            }
            var old: ChatItem.Browser?
            if let i, case .browser(let x) = next[i] { old = x }
            var hosts = old?.hosts ?? []
            if !b.host.isEmpty && !hosts.contains(b.host) { hosts.append(b.host) }
            let card = ChatItem.Browser(
                key: old?.key ?? newKey(), id: b.id, title: b.title, host: b.host, url: b.url, status: b.status,
                image: b.image ?? old?.image,
                width: b.image != nil ? b.width : (old?.width ?? 0), height: b.image != nil ? b.height : (old?.height ?? 0),
                recent: filmstrip(old?.recent ?? [], b.image), tab: b.tab.isEmpty ? (old?.tab ?? "") : b.tab, hosts: hosts)
            if let i { next[i] = .browser(card) } else { next.append(.browser(card)) }
        case .cardNeeded(let c):
            // this phone already sent the card on Allow: nothing more to ask
            if next.contains(where: { if case .approval(let a) = $0 { return a.id == c.approvalId && a.send == .sent }; return false }) {
                return items
            }
            if let i = next.firstIndex(where: { if case .cardNeeded(let x) = $0 { return x.approvalId == c.approvalId }; return false }),
               case .cardNeeded(var x) = next[i] {
                x.expiresAt = c.expiresAt
                next[i] = .cardNeeded(x)
            } else {
                next.append(.cardNeeded(.init(key: newKey(), approvalId: c.approvalId, card: c.card, site: c.site,
                                              amountText: c.amountText, expiresAt: c.expiresAt)))
            }
        case .paymentAuto(let p):
            next.append(.paymentAuto(.init(key: newKey(), site: p.site, amountText: p.amountText, cardLabel: p.card?.label ?? "",
                                           order: p.items.isEmpty && p.shipping == nil ? nil
                                               : PaymentInfo(site: p.site, summary: p.summary, amount: 0, currency: "", amountText: p.amountText,
                                                             items: p.items, shipping: p.shipping))))
        case .shippingForm(let f):
            // the same form again (a resend): it keeps its row and its state
            if let i = next.firstIndex(where: { if case .shipping(let s) = $0 { return s.formId == f.formId }; return false }),
               case .shipping(var x) = next[i] {
                x.form = f
                next[i] = .shipping(x)
            } else {
                next.append(.shipping(.init(key: newKey(), form: f)))
            }
        case .secretRequest(_, let req):
            if !req.fromAgent { return items }
            if let i = next.firstIndex(where: { if case .secret(let x) = $0 { return x.request.id == req.id }; return false }),
               case .secret(var old) = next[i] {
                old.request = SecretRules.merge(old.request, req)
                next[i] = .secret(old)
            } else if req.open {
                // a newer entry for the same thing: the older one folds (the server replaced it)
                for j in next.indices {
                    if case .secret(var o) = next[j], o.request.open, o.request.kind == req.kind, o.request.name == req.name {
                        o.request.state = SecretRequest.stateReplaced
                        next[j] = .secret(o)
                    }
                }
                next.append(.secret(.init(key: newKey(), request: req)))
            } else {
                return items
            }
        case .fallback(_, let model, let reason): next.append(.notice(key: newKey(), .fallback(model: model, reason: reason)))
        case .retry: next.append(.notice(key: newKey(), .retry))
        case .reconnecting: next.append(.notice(key: newKey(), .reconnecting))
        // a newer server says what follows in a line of its own (and a Continue button, or a background job): no error bubble
        case .stepLimit(_, let following, _):
            if !following.isEmpty { return items }
            next.append(.error(key: newKey(), .stepLimit))
        case .error(let message): next.append(.error(key: newKey(), .message(message)))
        case .reaction(let messageId, let emoji):
            guard let i = Reactions.target(next, messageId: messageId), case .msg(var m) = next[i] else { return items }
            m.reaction = emoji
            next[i] = .msg(m)
        // a screenshot Kelo shows: under the reply's text so far, or as the reply's first row when nothing was written yet
        case .image(let img):
            if case .msg(var last)? = next.last, !last.mine, last.briefing == nil {
                last.images.append(img)
                next[next.count - 1] = .msg(last)
            } else {
                next.append(.msg(.init(key: newKey(), mine: false, text: "", at: now, images: [img])))
            }
        case .locationRequest(let ev):
            if let i = next.firstIndex(where: { if case .locationAsk(let l) = $0 { return l.approvalId == ev.approvalId }; return false }) {
                // the server's end state wins; a repeated "waiting" does not undo this phone's "sending"
                guard ev.state != ChatItem.LocationAsk.waiting, case .locationAsk(var l) = next[i] else { return items }
                l.state = ev.state
                next[i] = .locationAsk(l)
            } else if ev.state == ChatItem.LocationAsk.waiting {
                next.append(.locationAsk(.init(key: newKey(), approvalId: ev.approvalId, reason: ev.reason, settings: ev.settings)))
            } else {
                return items
            }
        case .userMessage(let ev):
            // the message this reply answers: the newest of the user's own (the bubble this phone just added)
            guard let i = next.lastIndex(where: { $0.asMsg?.mine == true }), case .msg(var m) = next[i] else { return items }
            if m.id == nil, ev.messageId > 0 { m.id = ev.messageId }
            if !ev.attachments.isEmpty { m.files = ev.attachments }
            m.autoText = ev.autoText
            next[i] = .msg(m)
        default: return items
        }
        return next
    }

    /// What a call was about, in a few words, from its arguments (a search, a site, a name), for a folded chip's list.
    public static func detailOf(_ args: JSONObject) -> String {
        for k in ["query", "q", "url", "site", "subject", "name", "service", "path", "title", "uid"] {
            guard let v = args[k]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !v.isEmpty else { continue }
            var shown = v
            if k == "url" || k == "site" {
                if let r = shown.range(of: "://") { shown = String(shown[r.upperBound...]) }
                shown = String(shown.prefix { $0 != "/" })
                if shown.hasPrefix("www.") { shown = String(shown.dropFirst(4)) }
            }
            return shown.count > 60 ? String(shown.prefix(59)) + "…" : shown
        }
        if let many = args["uids"]?.arrayValue, !many.isEmpty { return String(many.count) }
        return ""
    }

    static let browserGroup = "browser"

    private static func groupKeyOf(_ t: ChatItem.Tool) -> String {
        t.group.isEmpty && t.name.hasPrefix("browser_") ? browserGroup : t.groupKey
    }

    /// The rows the chat draws: tool chips of one group in a row fold into ONE chip that grows in place (`members`; its key is the
    /// first call's, so the row stays put). Browser steps are not shown as chips at all in a reply that has a browser card (the card
    /// shows them); without one they fold like any group. Everything else passes through as it is.
    public static func foldTools(_ items: [ChatItem]) -> [ChatItem] {
        guard items.contains(where: { if case .tool = $0 { return true }; return false }) else { return items }
        var cardInReply = [Bool](repeating: false, count: items.count)
        var replyStart = 0
        for i in items.indices {
            let mine = items[i].isMine
            if mine || i == items.count - 1 {
                let end = mine ? i : i + 1
                if replyStart < end, items[replyStart..<end].contains(where: { if case .browser = $0 { return true }; return false }) {
                    for j in replyStart..<end { cardInReply[j] = true }
                }
                replyStart = i + 1
            }
        }
        var out: [ChatItem] = []
        out.reserveCapacity(items.count)
        for (i, item) in items.enumerated() {
            guard case .tool(let t) = item else { out.append(item); continue }
            if groupKeyOf(t) == browserGroup && cardInReply[i] { continue }
            // the chip this call joins: the last row, or the last chip before approval cards that came between the calls (adding
            // two events asks twice: one chip, the cards stay under it in their order)
            var j = out.count - 1
            while j >= 0, case .approval = out[j] { j -= 1 }
            if j >= 0, case .tool(var last) = out[j], groupKeyOf(t) == groupKeyOf(last) {
                last.members = (last.members.isEmpty ? [last] : last.members) + [t]
                if !t.done.isEmpty { last.done = t.done }
                if !t.label.isEmpty { last.label = t.label }
                out[j] = .tool(last)
            } else {
                out.append(item)
            }
        }
        return out
    }

    /// A folded chip's state: running while any call runs; else OK when any call did its work; else the first call's state.
    public static func foldedState(_ t: ChatItem.Tool) -> ToolState {
        let m = t.members.isEmpty ? [t] : t.members
        if m.contains(where: { $0.state == .running }) { return .running }
        if m.contains(where: { $0.state == .ok }) { return .ok }
        return m[0].state
    }

    /// How many things the calls of a folded chip that did their work were about: the same photo (or page, message, event) looked at
    /// twice counts once (a retry, or another model taking over the reply, repeats the call). Calls with nothing to tell them apart
    /// each count.
    public static func doneCount(_ t: ChatItem.Tool) -> Int {
        let ok = (t.members.isEmpty ? [t] : t.members).filter { $0.state == .ok }
        var seen = Set<String>()
        var n = 0
        for one in ok {
            if one.detail.isEmpty { n += 1 } else if seen.insert(one.name + "\u{0}" + one.detail).inserted { n += 1 }
        }
        return n
    }

    /// The words of a folded chip that is done: the server's words with the count, or "label · n" from an older server.
    public static func foldedText(_ t: ChatItem.Tool, _ n: Int) -> String {
        t.done.contains("{n}") ? t.done.replacingOccurrences(of: "{n}", with: String(n)) : "\(t.shown) · \(n)"
    }

    /// Older open cards for the same thing become a record only.
    private static func supersede(_ items: inout [ChatItem], _ same: (ChatItem) -> Bool) {
        for i in items.indices {
            switch items[i] {
            case .vault(var v) where v.state == .open && same(items[i]): v.state = .expired; items[i] = .vault(v)
            case .connect(var c) where c.state == .open && same(items[i]): c.state = .expired; items[i] = .connect(c)
            case .shipping(var s) where s.state == .open && same(items[i]): s.state = .expired; s.sending = false; items[i] = .shipping(s)
            default: break
            }
        }
    }

    /// This phone answered the question `approvalId`. An expired or already answered one stays as it is.
    public static func answer(_ items: [ChatItem], _ approvalId: String, _ ok: Bool) -> [ChatItem] {
        items.map { item in
            guard case .approval(var a) = item, a.id == approvalId, a.open else { return item }
            a.answer = ok
            return .approval(a)
        }
    }

    /// The reply is over: the server denies every question still waiting, so they expire; no card is waited for any more. Secure
    /// entries that are no longer open (saved, cancelled, expired, replaced) go: only their status line was left, and it must never
    /// stay above the composer (spec 5.16). Open entries stay.
    public static func endTurn(_ items: [ChatItem]) -> [ChatItem] {
        items.filter { !isSettledEntry($0) }.map { item in
            switch item {
            case .approval(var a) where a.open: a.expired = true; return .approval(a)
            case .cardNeeded(var c) where !c.ended && c.send != .sent: c.ended = true; return .cardNeeded(c)
            // the agent no longer waits for an address once its reply is over
            case .shipping(var s) where s.state == .open: s.state = .expired; s.sending = false; return .shipping(s)
            // the place is no longer waited for once the reply is over
            case .locationAsk(var l) where l.open: l.state = ChatItem.LocationAsk.expired; return .locationAsk(l)
            default: return item
            }
        }
    }

    /// The location cards after GET /api/approvals/pending (`pending`: its location_request items): a waiting one no row shows comes
    /// back at the end (key "loc:<id>", after a reload or a restart); a waiting card no longer listed (answered on another phone, or
    /// run out) ends as expired, unless a reply is followed here (`busy`: its own events say how it ended). A card this phone is
    /// sending stays as it is. Only a fetched list may end a card (Android ChatReducer.withPendingLocations).
    public static func withPendingLocations(_ items: [ChatItem], _ pending: [TurnEvent.LocationRequest], busy: Bool) -> [ChatItem] {
        let waiting = pending.filter { $0.state == ChatItem.LocationAsk.waiting && !$0.approvalId.isEmpty }
        let ids = Set(waiting.map(\.approvalId))
        var shown = Set<String>()
        var changed = false
        var out = items.map { item -> ChatItem in
            guard case .locationAsk(var l) = item else { return item }
            shown.insert(l.approvalId)
            if !busy && l.state == ChatItem.LocationAsk.waiting && !ids.contains(l.approvalId) {
                changed = true
                l.state = ChatItem.LocationAsk.expired
                return .locationAsk(l)
            }
            return item
        }
        for p in waiting where !shown.contains(p.approvalId) {
            out.append(.locationAsk(.init(key: "loc:" + p.approvalId, approvalId: p.approvalId, reason: p.reason, settings: p.settings)))
            changed = true
        }
        return changed ? out : items
    }

    /// The location card `approvalId` in `state` (this phone is sending, or what came of it). Only an open card changes.
    public static func locationState(_ items: [ChatItem], _ approvalId: String, _ state: String) -> [ChatItem] {
        items.map { item in
            guard case .locationAsk(var l) = item, l.approvalId == approvalId, l.open else { return item }
            l.state = state
            return .locationAsk(l)
        }
    }

    /// How sending the card for `approvalId` goes, on its question and on a card_needed row for it.
    public static func cardSend(_ items: [ChatItem], _ approvalId: String, _ send: CardSend) -> [ChatItem] {
        items.map { item in
            switch item {
            case .approval(var a) where a.id == approvalId: a.send = send; return .approval(a)
            case .cardNeeded(var c) where c.approvalId == approvalId: c.send = send; return .cardNeeded(c)
            default: return item
            }
        }
    }

    /// Changes the address form `key` (the chat's answer to it, its expiry).
    public static func shipping(_ items: [ChatItem], _ key: String, _ change: (inout ChatItem.Shipping) -> Void) -> [ChatItem] {
        items.map { item in
            guard case .shipping(var s) = item, s.key == key else { return item }
            change(&s)
            return .shipping(s)
        }
    }

    /// The briefing cards on screen after GET /api/messages again (`fresh`: message id -> that message's card now): a card already
    /// shown takes its new state (its hours line goes once answered on another device); every other row, and the order, stay as they
    /// are (Android ChatReducer.withBriefings).
    public static func withBriefings(_ items: [ChatItem], _ fresh: [Int64: BriefingCard]) -> [ChatItem] {
        items.map { item in
            guard case .msg(var m) = item, m.briefing != nil, let id = m.id, let card = fresh[id], card != m.briefing else { return item }
            m.briefing = card
            return .msg(m)
        }
    }

    /// A secure entry that is no longer open: only its one status line is left (`endTurn` removes it).
    public static func isSettledEntry(_ item: ChatItem) -> Bool {
        if case .secret(let s) = item { return !s.request.open }
        return false
    }

    /// Every answered entry's line out of the rows (no reply runs here that would end and clear them).
    public static func dropSettled(_ items: [ChatItem]) -> [ChatItem] {
        items.contains(where: isSettledEntry) ? items.filter { !isSettledEntry($0) } : items
    }

    /// The rows after the entry `done` (its new state) was answered here. `followsHere` (`SecretRules.followsHere`): its line folds in
    /// place, or joins the rows when only the server's list showed it (`placeAnswered`), and the reply's end clears it. Otherwise the
    /// line goes at once and is never added back (an answer that arrives after the reply already ended). Android
    /// ChatReducer.afterAnswer.
    public static func afterAnswer(_ items: [ChatItem], _ done: SecretRequest, followsHere: Bool) -> [ChatItem] {
        func isIt(_ item: ChatItem) -> Bool { if case .secret(let s) = item { return s.request.id == done.id }; return false }
        if !followsHere { return items.filter { !isIt($0) } }
        if items.contains(where: isIt) { return settleSecret(items, done.id, state: done.state, result: done.result) }
        return placeAnswered(items, done)
    }

    /// The secure entry `requestId` was answered here (or found gone): its card folds with `state` (and `result` when saved).
    public static func settleSecret(_ items: [ChatItem], _ requestId: String, state: String, result: SecretResult? = nil) -> [ChatItem] {
        items.map { item in
            guard case .secret(var s) = item, s.request.id == requestId, s.request.open else { return item }
            s.request.state = state
            if let result { s.request.result = result }
            return .secret(s)
        }
    }

    /// The rows the chat shows: `items` plus the open secure entries from GET /api/secret-requests (`pending`) that no row shows yet,
    /// each right under the agent message it belongs to (`messageId`), or at the end while that message is not on screen (the reply
    /// asking is still being written, or it is older than the loaded page). A reload, another phone or a lost connection thus still
    /// shows them. Requests the Keychain made from a waiting vault item (source "draft") never show here. Only OPEN requests are
    /// placed: an answered one from the list never shows here (it would stick at the end of the chat when its message is not stored).
    public static func withPending(_ items: [ChatItem], _ pending: [SecretRequest]) -> [ChatItem] {
        place(items, pending.filter(\.open))
    }

    /// An entry answered here that no row showed yet (it came from the server's list): it joins the rows once (under its message,
    /// else at the end), so a reply that carries on starts below it. Only `SecretEntries.settle` uses this; `endTurn` and a reload
    /// drop it again (Android ChatReducer.placeAnswered).
    public static func placeAnswered(_ items: [ChatItem], _ answered: SecretRequest) -> [ChatItem] { place(items, [answered]) }

    private static func place(_ items: [ChatItem], _ pending: [SecretRequest]) -> [ChatItem] {
        var shown = Set<String>()
        var ids = Set<Int64>()
        for item in items {
            switch item {
            case .secret(let s): shown.insert(s.request.id)
            case .msg(let m) where !m.mine: if let id = m.id { ids.insert(id) }
            default: break
            }
        }
        let extra = pending.filter { $0.fromAgent && !shown.contains($0.id) }
        if extra.isEmpty { return items }
        var under: [Int64: [SecretRequest]] = [:]
        var loose: [SecretRequest] = []
        for r in extra {
            if let m = r.messageId, ids.contains(m) { under[m, default: []].append(r) } else { loose.append(r) }
        }
        var out: [ChatItem] = []
        out.reserveCapacity(items.count + extra.count)
        for item in items {
            out.append(item)
            if case .msg(let m) = item, !m.mine, let id = m.id, let rs = under[id] {
                for r in rs { out.append(.secret(.init(key: "secret:" + r.id, request: r))) }
            }
        }
        for r in loose.sorted(by: { $0.createdAt < $1.createdAt }) { out.append(.secret(.init(key: "secret:" + r.id, request: r))) }
        return out
    }

    /// How many pictures a browser row keeps for its filmstrip.
    public static let filmstripSize = 3

    public static func filmstrip(_ recent: [String], _ image: String?) -> [String] {
        guard let image, recent.last != image else { return recent }
        return Array((recent + [image]).suffix(filmstripSize))
    }

    /// What a finished step means for the line that replaces the buttons.
    public static func outcome(_ step: FollowupStep, _ result: StepResult) -> ActionOutcome? {
        switch result {
        case .failed: return nil
        case .gone: return .gone
        case .done:
            switch step {
            case .answer(_, let approve): return approve ? .approved : .denied
            case .resumeWorker, .retry: return .resumed
            case .drop: return .dropped
            case .snooze: return .snoozed
            case .openLogin: return .signedIn
            }
        }
    }

    /// The line under a message whose follow-up is no longer open: what this phone did, else what the server says happened.
    public static func closedOutcome(_ item: Followup, local: ActionOutcome?) -> ActionOutcome? {
        if let local { return local }
        if item.open { return nil }
        return item.status == Followup.statusDropped ? .dropped : .resolved
    }

    /// The user finished or closed the card `key`; only an open card changes.
    public static func settleCard(_ items: [ChatItem], _ key: String, _ state: CardState) -> [ChatItem] {
        items.map { item in
            guard item.key == key else { return item }
            switch item {
            case .vault(var v) where v.state == .open: v.state = state; return .vault(v)
            case .connect(var c) where c.state == .open: c.state = state; return .connect(c)
            default: return item
            }
        }
    }

    /// A stored message as a row.
    public static func item(_ m: ChatMessage) -> ChatItem {
        .msg(.init(key: "m\(m.id)", mine: m.role == "user", text: m.content, id: m.id, replies: m.replies, at: Int64(m.createdAt * 1000),
                   reaction: m.reaction, briefing: m.briefing, images: m.images, files: m.attachments, autoText: m.autoText))
    }

    /// The server's "replies" event (answer buttons on the stored message): KeloCore keeps it as an unknown type.
    public static func isReplies(_ ev: TurnEvent) -> Bool {
        if case .unknown("replies") = ev { return true }
        return false
    }
}

/// How an approval ended, for the chip on its card.
public enum Outcome: Sendable { case allowed, denied, expired }

public enum Outcomes {
    public static func of(_ a: ChatItem.Approval) -> Outcome? {
        if a.open { return nil }
        if a.answer == true { return .allowed }
        if a.answer == false { return .denied }
        return .expired
    }
}

/// A payment PIN waiting for this phone (pin_needed) and how sending it went.
public struct PinNeedRow: Equatable, Sendable, Identifiable {
    public var need: PinNeed
    public var send: CardSend = .idle
    public var id: String { need.slotId }

    /// The list after an event: the same slot is replaced in place (keeping how sending went), a new one is added.
    public static func upsert(_ rows: [PinNeedRow], _ need: PinNeed) -> [PinNeedRow] {
        guard let i = rows.firstIndex(where: { $0.need.slotId == need.slotId }) else { return rows + [PinNeedRow(need: need)] }
        var out = rows
        out[i].need = need
        return out
    }

    static func set(_ rows: [PinNeedRow], _ slotId: String, _ send: CardSend) -> [PinNeedRow] {
        rows.map { var r = $0; if r.need.slotId == slotId { r.send = send }; return r }
    }
}

extension CardSendError {
    init(_ p: WalletSendProblem) {
        switch p {
        case .notWaiting: self = .notWaiting
        case .mismatch: self = .mismatch
        case .notOnPhone: self = .notOnPhone
        case .noLock: self = .noLock
        case .cancelled: self = .cancelled
        case .network: self = .network
        case .other: self = .other
        }
    }
}
