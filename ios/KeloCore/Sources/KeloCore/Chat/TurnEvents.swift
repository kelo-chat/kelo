import Foundation

/// A tool call's arguments as the server sent them (shown only through `ApprovalLine`s, never raw).
public typealias JSONObject = [String: JSON]

/// One event of a reply (server.py "turn events": SSE `data:` lines of /api/talk and /api/chat, `events` of GET /api/turns/{id}).
/// Unknown types are kept as `.unknown` and ignored, so a newer server never breaks the chat.
public enum TurnEvent: Equatable, Sendable {
    case thinking
    case text(String)
    /// `label`: the short words to show instead of `name` (empty from an older server); `hidden`: show nothing for it.
    case toolStarted(ToolStarted)
    case toolResult(ToolResult)
    case approvalRequest(ApprovalRequest)
    /// A picture of the assistant's browser tab; one card per tool call `id`, updated in place.
    case browser(BrowserShot)
    /// The user took over the tab this reply works in (`controller` "user") or handed it back ("agent").
    case browserControl(id: String, controller: String)
    /// A human check appeared in a tab; Kelo never solves it, the app offers the live view.
    case browserCheck(BrowserCheck)
    /// A human check handed to the user (state "waiting"), or how that ended (solved | skipped | expired | cancelled). The card itself
    /// comes from GET /api/approvals/pending (kind "human_check").
    case humanCheck(HumanCheck)
    /// An allowed payment waits for this phone to send an end-to-end card before `expiresAt` (unix seconds).
    case cardNeeded(CardNeeded)
    /// A payment went ahead without asking, under the user's auto-pay rules.
    case paymentAuto(PaymentAuto)
    /// The agent needs a delivery address for an order and none is known (`ask_shipping`): the chat shows the address form; the
    /// agent waits for POST /api/shipping-forms/{form_id}.
    case shippingForm(ShippingForm)
    /// The agent asked for a secret (add_service_key, request_login, request_card): the chat shows a secure entry for `request` right
    /// here; the same event comes again with a new state when it is answered while the reply runs. `id`: the tool call.
    case secretRequest(id: String, request: SecretRequest)
    /// The reply used its step budget. `next` from a newer server: "background" (the rest goes on in a job), "continue" or "stopped"
    /// (a Continue button follows); the server then also writes a line saying so. "" = an older server.
    case stepLimit(steps: Int, next: String = "", progress: Int = 0)
    /// A payment step that needs the user (approve in another app, a code, the PIN keypad), or the same one again after a change.
    case paymentStep(PaymentStep)
    /// A payment's PIN waits for this phone to release it.
    case pinNeeded(PinNeed)
    case fallback(provider: String, model: String, reason: String)
    case retry(reason: String)
    /// Made by the app (never sent by the server): the connection is being tried again.
    case reconnecting
    case done(inputTokens: Int64, outputTokens: Int64, steps: Int)
    case error(String)
    /// The agent opened, snoozed or dropped a follow-up during the reply: the chat loads the follow-ups again.
    case followup(followupId: String, status: String)
    /// A background job this reply started (status "running") or one it belongs to changed status.
    case worker(id: String, status: String, title: String)
    /// The emoji on the user's message this reply answers (`messageId`): 👀 when it starts, the chosen one when the work is over.
    case reaction(messageId: Int64, emoji: String)
    /// A screenshot Kelo shows in the chat (server chat_images.py): under the reply's text so far. Only the server's own urls.
    case image(ChatImage)
    /// The reply waits for this phone's place (server LOCATION REQUEST): `state` waiting, then allowed | refused | expired |
    /// cancelled; `settings`: the owner refused before, so the card offers the phone's settings.
    case locationRequest(LocationRequest)
    /// The first event of a reply to a message that carried files: the stored message's id, its files as the server keeps them (with
    /// thumbnails) and whether the text is the app's own sentence (`autoText`: the bubble shows only the files).
    case userMessage(UserMessage)
    case unknown(String)

    public struct LocationRequest: Equatable, Sendable {
        public var approvalId: String, state: String, reason: String, settings: Bool
        public init(approvalId: String, state: String = "waiting", reason: String = "", settings: Bool = false) {
            self.approvalId = approvalId; self.state = state; self.reason = reason; self.settings = settings
        }
    }

    public struct UserMessage: Equatable, Sendable {
        public var messageId: Int64, attachments: [MessageFile], autoText: Bool
        public init(messageId: Int64, attachments: [MessageFile] = [], autoText: Bool = false) {
            self.messageId = messageId; self.attachments = attachments; self.autoText = autoText
        }
    }

    /// `group`: calls of one group in a row fold into one chip ("" = the name); `done`: that chip's words with {n} ("Read {n} messages").
    public struct ToolStarted: Equatable, Sendable {
        public var id: String, name: String, arguments: JSONObject, label: String, hidden: Bool
        public var group: String, done: String
        public init(id: String, name: String, arguments: JSONObject = [:], label: String = "", hidden: Bool = false, group: String = "",
                    done: String = "") {
            self.id = id; self.name = name; self.arguments = arguments; self.label = label; self.hidden = hidden
            self.group = group; self.done = done
        }
        /// arguments.service when it is a string (connect_service names the connector there).
        public var service: String? { arguments["service"]?.stringValue }
    }

    public struct ToolResult: Equatable, Sendable {
        public var id: String, name: String, output: String, isError: Bool, approved: Bool?
        public init(id: String, name: String, output: String, isError: Bool, approved: Bool?) {
            self.id = id; self.name = name; self.output = output; self.isError = isError; self.approved = approved
        }
    }

    public struct ApprovalRequest: Equatable, Sendable {
        public var approvalId: String
        public var name: String
        public var detail: String?
        public var label: String
        public var payment: PaymentInfo?
        public var card: ApprovalCardInfo?
        /// The card may offer "keep allowing this in that scheduled job" (remember "schedule").
        public var canRememberSchedule: Bool
        /// Up to four readable label / value pairs (hosts rather than URLs); the raw arguments are never shown.
        public var lines: [ApprovalLine]
        /// The card's title as a whole question in the user's language ("" from an older server: build it from the label).
        public var ask: String
        /// "human_check": a site's check whether a person is there, handed to the user (solve it in the live view, or skip the site).
        public var kind: String
        /// The browser tab it is about (a human check).
        public var tab: String
        /// The setup card of a "when X happens, do Y" task (task_allow): trigger, action, limits, the card's own auto-pay limit.
        public var grant: TaskGrantAsk?
        /// Asked in a run of a task marked urgent at setup: time matters; `missed` says what a late answer would miss.
        public var urgent: Bool
        public var missed: String
        /// The items a permission for many covers, one short line each (the card's "see the list"); empty for most cards.
        public var items: [String]
        /// A payment whose site has a saved payment PIN: Allow also releases it from this phone (Face ID).
        public var pin: PinAsk?
        /// What a human check is (`kind` "human_check"): a person check, or a sign-in verification (`CheckInfo.isVerification`).
        public var check: CheckInfo?
        /// The site the step is on and whether "don't ask again on this site" may be offered.
        public var site: String = ""
        public var canRemember: Bool = false

        public init(approvalId: String, name: String, detail: String? = nil, label: String = "",
                    payment: PaymentInfo? = nil, card: ApprovalCardInfo? = nil, canRememberSchedule: Bool = false,
                    lines: [ApprovalLine] = [],
                    ask: String = "", kind: String = "", tab: String = "",
                    grant: TaskGrantAsk? = nil, urgent: Bool = false, missed: String = "", items: [String] = [], pin: PinAsk? = nil,
                    check: CheckInfo? = nil, site: String = "", canRemember: Bool = false) {
            self.approvalId = approvalId; self.name = name; self.detail = detail; self.label = label
            self.payment = payment; self.card = card; self.canRememberSchedule = canRememberSchedule
            self.lines = lines
            self.ask = ask; self.kind = kind; self.tab = tab
            self.grant = grant; self.urgent = urgent; self.missed = missed; self.items = items; self.pin = pin
            self.check = check
            self.site = site; self.canRemember = canRemember
        }

        /// The server's whole question for the card's title, or nil (an older server: build it from the label).
        public var question: String? { Self.question(ask) }

        public static func question(_ ask: String) -> String? {
            let t = ask.trimmingCharacters(in: .whitespacesAndNewlines)
            return t.isEmpty ? nil : t
        }
    }

    public struct HumanCheck: Equatable, Sendable {
        public var approvalId: String, state: String, site: String, tab: String
        public var check: CheckInfo?
        public init(approvalId: String, state: String, site: String, tab: String, check: CheckInfo? = nil) {
            self.approvalId = approvalId; self.state = state; self.site = site; self.tab = tab; self.check = check
        }
    }

    public struct BrowserShot: Equatable, Sendable {
        /// `image` is a data:image/... URL or nil.
        public var id: String, title: String, host: String, status: String, image: String?
        public var width: Int, height: Int
        /// The tab the picture came from ("" = an older server: this phone's own tab).
        public var tab: String = ""

        public init(id: String, title: String = "", host: String = "", status: String = "", image: String? = nil,
                    width: Int = 0, height: Int = 0, tab: String = "") {
            self.id = id; self.title = title; self.host = host; self.status = status; self.image = image
            self.width = width; self.height = height; self.tab = tab
        }
    }

    public struct BrowserCheck: Equatable, Sendable {
        /// vendor: cloudflare | recaptcha | hcaptcha | ...; kind: interstitial | widget.
        public var id: String, tab: String, site: String, vendor: String, name: String, kind: String
    }

    public struct CardNeeded: Equatable, Sendable {
        public var approvalId: String, card: CardBrief, site: String
        public var amountText: String, expiresAt: Int64
    }

    public struct PaymentAuto: Equatable, Sendable {
        public var card: CardBrief?
        public var site: String, amountText: String, summary: String
        /// What was bought and where it goes, when the server tells (an older server: empty / nil).
        public var items: [OrderItem] = []
        public var shipping: OrderShipping? = nil
    }

    /// shipping_form: `country` the form starts with; `prefill` what Kelo remembers (field keys, "phone", "note"; may be empty or
    /// partial: the user sees and confirms it); `save` / `label`: "use for next orders" starts on, with that nickname; `askPayment`:
    /// also ask how to pay ("card" | "site"); `expiresAt` unix seconds.
    public struct ShippingForm: Equatable, Sendable {
        public var id: String, formId: String, site: String, what: String, country: String
        public var prefill: [String: String]
        public var label: String, save: Bool, askPayment: Bool
        public var expiresAt: Double?

        public init(id: String = "", formId: String, site: String = "", what: String = "", country: String = "", prefill: [String: String] = [:],
                    label: String = "", save: Bool = true, askPayment: Bool = false, expiresAt: Double? = nil) {
            self.id = id; self.formId = formId; self.site = site; self.what = what; self.country = country; self.prefill = prefill
            self.label = label; self.save = save; self.askPayment = askPayment; self.expiresAt = expiresAt
        }

        static func parse(_ o: Lenient) -> ShippingForm {
            var prefill: [String: String] = [:]
            for (k, v) in o["prefill"]?.objectValue ?? [:] {
                if let t = v.text?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { prefill[k] = t }
            }
            return ShippingForm(id: o.str("id"), formId: o.str("form_id"), site: o.str("site"), what: o.str("what"),
                                country: o.str("country").uppercased(), prefill: prefill, label: o.str("label"),
                                save: o.optBool("save") ?? true, askPayment: o.optBool("ask_payment") == true,
                                expiresAt: o.optDouble("expires_at"))
        }
    }

    /// Reads one event; anything unreadable is `.unknown`.
    public static func parse(_ e: JSON) -> TurnEvent {
        guard e.objectValue != nil else { return .unknown("") }
        let o = Lenient(e)
        func obj(_ k: String) -> JSONObject { o[k]?.objectValue ?? [:] }
        switch o.str("type") {
        case "thinking": return .thinking
        case "text": return .text(o.str("text"))
        case "tool_started":
            return .toolStarted(ToolStarted(id: o.str("id"), name: o.str("name"), arguments: obj("arguments"), label: o.str("label"),
                                            hidden: o.optBool("hidden") == true, group: o.str("group"), done: o.str("done")))
        case "tool_result":
            return .toolResult(ToolResult(id: o.str("id"), name: o.str("name"), output: o.str("output"), isError: o.optBool("is_error") == true,
                                          approved: o.optBool("approved")))
        case "approval_request":
            let detail = o.str("detail")
            return .approvalRequest(ApprovalRequest(
                approvalId: o.str("approval_id"), name: o.str("name"), detail: detail.isEmpty ? nil : detail,
                label: o.str("label"), payment: o["payment"]?.objectValue.map { PaymentInfo.parse(Lenient(.object($0))) },
                card: o["card"]?.objectValue.map { ApprovalCardInfo.parse(Lenient(.object($0))) },
                canRememberSchedule: o.optBool("can_remember_schedule") == true, lines: ApprovalLine.parseList(o["lines"]),
                ask: o.str("ask"), kind: o.str("kind"),
                tab: o.str("tab"), grant: TaskGrantRules.parse(o["grant"]), urgent: o.optBool("urgent") == true, missed: o.str("missed"),
                items: (o["items"]?.arrayValue ?? []).compactMap { $0.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) }
                    .filter { !$0.isEmpty },
                pin: PinAsk.parse(o["pin"]), check: CheckInfo.parse(o["check"]),
                site: o.str("site"), canRemember: o.optBool("can_remember") == true))
        case "payment_step":
            guard let step = PaymentStep.parse(e) else { return .unknown("payment_step") }
            return .paymentStep(step)
        case "pin_needed":
            guard let need = PinNeed.parse(e) else { return .unknown("pin_needed") }
            return .pinNeeded(need)
        case "location_request":
            let state = o.str("state")
            return .locationRequest(LocationRequest(approvalId: o.str("approval_id"), state: state.isEmpty ? "waiting" : state,
                                                    reason: o.str("reason"), settings: o.optBool("settings") == true))
        case "user_message":
            return .userMessage(UserMessage(messageId: o.int64("message_id"), attachments: MessageFile.parseList(o["attachments"]),
                                            autoText: o.optBool("auto_text") == true))
        case "human_check":
            return .humanCheck(HumanCheck(approvalId: o.str("approval_id"), state: o.str("state"), site: o.str("site"), tab: o.str("tab"),
                                          check: CheckInfo.parse(o["check"])))
        case "browser":
            let image = o.str("image")
            return .browser(BrowserShot(id: o.str("id"), title: o.str("title"), host: o.str("host"), status: o.str("status"),
                                        image: image.hasPrefix("data:image/") ? image : nil, width: o.int("width"), height: o.int("height"),
                                        tab: o.str("tab")))
        case "browser_control":
            return .browserControl(id: o.str("id"), controller: o.str("controller"))
        case "browser_check":
            return .browserCheck(BrowserCheck(id: o.str("id"), tab: o.str("tab"), site: o.str("site"), vendor: o.str("vendor"),
                                              name: o.str("name"), kind: o.str("kind")))
        case "card_needed":
            return .cardNeeded(CardNeeded(approvalId: o.str("approval_id"), card: CardBrief.parse(o.obj("card")),
                                          site: o.str("site"),
                                          amountText: o.str("amount_text"), expiresAt: o.int64("expires_at")))
        case "payment_auto":
            let p = o.obj("payment")
            let site = p.str("site")
            return .paymentAuto(PaymentAuto(card: o["card"]?.objectValue.map { CardBrief.parse(Lenient(.object($0))) },
                                            site: site.isEmpty ? p.str("host") : site, amountText: p.str("amount_text"), summary: p.str("summary"),
                                            items: OrderItem.parseList(p["items"]), shipping: OrderShipping.parse(p["shipping"])))
        case "shipping_form":
            let f = ShippingForm.parse(o)
            return f.formId.isEmpty ? .unknown("shipping_form") : .shippingForm(f)
        case "step_limit": return .stepLimit(steps: o.int("steps"), next: o.str("next"), progress: o.int("progress"))
        case "fallback": return .fallback(provider: o.str("provider"), model: o.str("model"), reason: o.str("reason"))
        case "retry": return .retry(reason: o.str("reason"))
        case "reconnecting": return .reconnecting
        case "done": return .done(inputTokens: o.int64("input_tokens"), outputTokens: o.int64("output_tokens"), steps: o.int("steps"))
        case "error": return .error(o.str("message"))
        case "followup": return .followup(followupId: o.str("followup_id"), status: o.str("status"))
        case "worker": return .worker(id: o.str("id"), status: o.str("status"), title: o.str("title"))
        case "reaction": return .reaction(messageId: o.int64("message_id"), emoji: o.str("emoji"))
        case "image":
            let url = o.str("url")
            guard url.hasPrefix("/api/") else { return .unknown("image") }
            return .image(ChatImage(id: o.str("image_id"), url: url, width: o.int("width"), height: o.int("height"), caption: o.str("caption")))
        case "secret_request":
            guard let r = SecretRequest.parse(o["request"]) else { return .unknown("secret_request") }
            return .secretRequest(id: o.str("id"), request: r)
        case let other: return .unknown(other)
        }
    }
}

/// approval_request.payment of confirm_purchase: what will be paid, where; for an order also the items, where it goes, the coupon and
/// what was ordered (all empty / nil from an older server: the card then shows the summary only).
public struct PaymentInfo: Equatable, Sendable {
    public var site: String, summary: String, amount: Double, currency: String, amountText: String
    public var items: [OrderItem]
    public var shipping: OrderShipping?
    /// The coupon code used ("" none).
    public var coupon: String
    /// order.what ("블루 머그 2개"), nil when the payment is not an order.
    public var orderWhat: String?

    public init(site: String, summary: String, amount: Double, currency: String, amountText: String, items: [OrderItem] = [],
                shipping: OrderShipping? = nil, coupon: String = "", orderWhat: String? = nil) {
        self.site = site; self.summary = summary; self.amount = amount; self.currency = currency; self.amountText = amountText
        self.items = items; self.shipping = shipping; self.coupon = coupon; self.orderWhat = orderWhat
    }

    static func parse(_ o: Lenient) -> PaymentInfo {
        let currency = o.str("currency")
        let order = o.obj("order")
        return PaymentInfo(site: o.str("site"), summary: o.str("summary"), amount: o.double("amount"),
                           currency: currency.isEmpty ? "KRW" : currency, amountText: o.str("amount_text"),
                           items: OrderItem.parseList(o["items"]), shipping: OrderShipping.parse(o["shipping"]),
                           coupon: o.str("coupon").trimmingCharacters(in: .whitespacesAndNewlines),
                           orderWhat: o.has("order") ? order.str("what") : nil)
    }
}

/// One line of an order: name, options ("색상: 네이비"), how many, and its price as the site shows it.
public struct OrderItem: Equatable, Sendable {
    public var name: String, options: String, quantity: Int, priceText: String

    public init(name: String, options: String = "", quantity: Int = 1, priceText: String = "") {
        self.name = name; self.options = options; self.quantity = quantity; self.priceText = priceText
    }

    /// Only entries with a name; at most `max` (a long cart is cut; the total still says it all).
    public static let max = 20

    static func parseList(_ e: JSON?) -> [OrderItem] {
        guard let arr = e?.arrayValue else { return [] }
        let items = arr.compactMap { v -> OrderItem? in
            guard v.objectValue != nil else { return nil }
            let o = Lenient(v)
            let name = o.str("name").trimmingCharacters(in: .whitespacesAndNewlines)
            guard !name.isEmpty else { return nil }
            return OrderItem(name: name, options: o.str("options").trimmingCharacters(in: .whitespacesAndNewlines),
                             quantity: Swift.max(1, o.int("quantity", 1)), priceText: o.str("price_text").trimmingCharacters(in: .whitespaces))
        }
        return Array(items.prefix(max))
    }
}

/// Where an order goes. source: "profile" (a saved address, `label` its nickname) | "once" (an address for this order only) |
/// "site" (the address the site account already had: name / phone may be empty and `formatted` is the page's text).
/// `formatted` never holds the name.
public struct OrderShipping: Equatable, Sendable {
    public static let once = "once", fromSite = "site"
    public var source: String, label: String, name: String, phone: String, formatted: String

    public init(source: String, label: String = "", name: String = "", phone: String = "", formatted: String = "") {
        self.source = source; self.label = label; self.name = name; self.phone = phone; self.formatted = formatted
    }

    /// The address lines, blank ones left out.
    public var lines: [String] {
        formatted.split(whereSeparator: \.isNewline).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    static func parse(_ e: JSON?) -> OrderShipping? {
        guard e?.objectValue != nil else { return nil }
        let o = Lenient(e)
        let s = OrderShipping(source: o.str("source"), label: o.str("label").trimmingCharacters(in: .whitespaces),
                              name: o.str("name").trimmingCharacters(in: .whitespaces), phone: o.str("phone").trimmingCharacters(in: .whitespaces),
                              formatted: o.str("formatted"))
        return s.lines.isEmpty && s.name.isEmpty && s.label.isEmpty ? nil : s
    }
}

/// A wallet card as chat events name it (never a number).
public struct CardBrief: Equatable, Sendable {
    public var id: String, label: String, brand: String, last4: String

    public init(id: String, label: String, brand: String, last4: String) {
        self.id = id; self.label = label; self.brand = brand; self.last4 = last4
    }

    static func parse(_ o: Lenient) -> CardBrief {
        CardBrief(id: o.str("id"), label: o.str("label"), brand: o.str("brand"), last4: o.str("last4"))
    }
}

/// approval_request.card: the card named for a payment. `needsCard`: an end-to-end card, so Allow also asks for Face ID and sends
/// the card from this phone. `reason` / `reasonText`: why it asks instead of paying automatically.
public struct ApprovalCardInfo: Equatable, Sendable {
    public var card: CardBrief, autoPay: Bool, needsCard: Bool, reasonText: String

    static func parse(_ o: Lenient) -> ApprovalCardInfo {
        ApprovalCardInfo(card: CardBrief.parse(o), autoPay: o.optBool("auto_pay") == true, needsCard: o.optBool("needs_card") == true,
                         reasonText: o.str("reason_text"))
    }
}

/// One readable pair of an approval card ("Site" · "github.com").
public struct ApprovalLine: Equatable, Sendable {
    public static let max = 4
    public var label: String, value: String
    /// Shown whole, never cut (a text message the phone will send).
    public var full: Bool

    public init(label: String, value: String, full: Bool = false) { self.label = label; self.value = value; self.full = full }

    /// Only string fields count; a pair without a value is left out; at most `max`.
    public static func parseList(_ e: JSON?) -> [ApprovalLine] {
        guard let arr = e?.arrayValue else { return [] }
        let lines = arr.compactMap { item -> ApprovalLine? in
            guard let o = item.objectValue else { return nil }
            func t(_ k: String) -> String { o[k]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
            let value = t("value")
            let full = o["full"].map { $0.boolValue == true || $0.stringValue == "true" } ?? false
            return value.isEmpty ? nil : ApprovalLine(label: t("label"), value: value, full: full)
        }
        return Array(lines.prefix(max))
    }
}

/// GET /api/turns/{id}?after=n&wait=20
public struct TurnPoll: Equatable, Sendable {
    public var events: [TurnEvent], next: Int, done: Bool

    public init(events: [TurnEvent], next: Int, done: Bool) { self.events = events; self.next = next; self.done = done }

    public static func parse(_ e: JSON) -> TurnPoll {
        let o = Lenient(e)
        return TurnPoll(events: (o["events"]?.arrayValue ?? []).map(TurnEvent.parse), next: o.int("next"), done: o.optBool("done") == true)
    }
}

/// An approval waiting for an answer (GET /api/approvals/pending items: the approval_request event without arguments, plus
/// turn_id, asked_at, unattended).
public struct PendingApproval: Equatable, Sendable {
    public var event: TurnEvent.ApprovalRequest
    public var site: String
    public var canRemember: Bool
    public var askedAt: Double
    public var unattended: Bool
    /// "keep allowing this in that scheduled job" may be offered (never for a payment).
    public var canRememberSchedule: Bool
    /// What asked: the run's task source (chat, worker, schedule:<id>, background…); "" from an older server.
    public var source: String
    /// The background job the asking run is a step of.
    public var workerId: String?
    /// That run's title (a job's own title).
    public var title: String

    public init(event: TurnEvent.ApprovalRequest, site: String = "", canRemember: Bool = false, askedAt: Double = 0, unattended: Bool = false,
                canRememberSchedule: Bool = false, source: String = "", workerId: String? = nil, title: String = "") {
        self.event = event; self.site = site; self.canRemember = canRemember; self.askedAt = askedAt; self.unattended = unattended
        self.canRememberSchedule = canRememberSchedule
        self.source = source; self.workerId = workerId; self.title = title
    }

    public var id: String { event.approvalId }
    public static let humanCheck = "human_check"
    /// A site's check whether a person is there, handed to the user: "Solve it" opens `tab` live, "Skip this site" answers no.
    public var isHumanCheck: Bool { event.kind == PendingApproval.humanCheck }
    /// A sign-in's second step (approve the sign-in on the phone, or type the code) handed over, not a person check: its own words.
    public var isVerification: Bool { isHumanCheck && event.check?.isVerification == true }
    /// The browser tab a human check is on.
    public var tab: String { event.tab.isEmpty ? "owner" : event.tab }

    public static func parse(_ e: JSON) -> PendingApproval? {
        guard case .approvalRequest(let ev) = TurnEvent.parse(e), !ev.approvalId.isEmpty else { return nil }
        let o = Lenient(e)
        return PendingApproval(event: ev, site: o["site"]?.stringValue ?? "", canRemember: o.optBool("can_remember") == true,
                               askedAt: o.double("asked_at"), unattended: o.optBool("unattended") == true,
                               canRememberSchedule: ev.payment == nil && o.optBool("can_remember_schedule") == true,
                               source: o["source"]?.stringValue ?? "",
                               workerId: (o["worker_id"]?.stringValue).flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0 },
                               title: o["title"]?.stringValue ?? "")
    }

    /// {"items": [...]} or a bare list.
    public static func parseList(_ e: JSON) -> [PendingApproval] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap(parse)
    }

    /// The pending list's "resolved": hand-overs that ended on the server, approval id -> outcome (solved | skipped | expired |
    /// cancelled). Empty from an older server.
    public static func parseResolved(_ e: JSON) -> [String: String] {
        var out: [String: String] = [:]
        for item in e["resolved"]?.arrayValue ?? [] {
            guard let o = item.objectValue, let id = o["approval_id"]?.stringValue, let outcome = o["outcome"]?.stringValue,
                  !id.isEmpty, !outcome.isEmpty else { continue }
            out[id] = outcome
        }
        return out
    }
}

/// The "check" of a human check (Android CheckInfo): `vendor` (cloudflare, recaptcha, kakao, …), `name` (the server's words, in the
/// owner's language) and `kind`: "interstitial" or "widget" (a person check), or "verification": a sign-in's second step, where
/// `name` is that step ("휴대폰에서 로그인을 승인해 주세요").
public struct CheckInfo: Equatable, Sendable {
    public static let kindVerification = "verification"
    public static let stepApprove = "approve"
    public var name: String, kind: String, step: String

    public init(name: String = "", kind: String = "", step: String = "") {
        self.name = name; self.kind = kind; self.step = step
    }

    public var isVerification: Bool { kind == Self.kindVerification }
    /// A sign-in verification the owner approves on the phone (an app's push), not a code typed in the page
    public var isApproveStep: Bool { isVerification && step == Self.stepApprove }

    public static func parse(_ e: JSON?) -> CheckInfo? {
        guard let o = e?.objectValue else { return nil }
        func s(_ k: String) -> String { o[k]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
        return CheckInfo(name: s("name"), kind: s("kind"), step: s("step"))
    }
}

extension MessageFile {
    /// Shown in the picture grid (a thumbnail exists, or will once the server has it).
    public var visual: Bool { kind == "image" || kind == "video" }

    /// A list as the server sends it; entries without a name are left out.
    public static func parseList(_ e: JSON?) -> [MessageFile] {
        (e?.arrayValue ?? []).compactMap { try? JSON.decode(MessageFile.self, from: $0) }.filter { !$0.name.isEmpty }
    }
}
