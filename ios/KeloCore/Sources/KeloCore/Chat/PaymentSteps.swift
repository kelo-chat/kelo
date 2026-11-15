import Foundation

// Payment steps that need the user (server payment_steps.py) and payment PIN releases (payment_pins.py), as Android
// chat/TurnEvents.kt reads them (PaymentStep, PinNeed, PinAsk, PaymentStepRules), and the task setup card's rules (Android
// agent/TaskGrantModels.kt TaskGrantRules).

/// A payment step that needs the user: approve in another app (`kindApp`, with a `code`, a `qr` picture and a countdown to
/// `expiresAt`), a one-time code (`kindOtp`), a passkey (`kindPasskey`), or the page's PIN keypad shown in the card (`kindPinPad`,
/// tapped there). `state`: waiting | expired | renewing | checking | finishing, then approved | done | cancelled | timeout | closed.
/// The same event comes again with each change (same `stepId`).
public struct PaymentStep: Equatable, Sendable, Identifiable {
    public static let kindApp = "app", kindOtp = "otp", kindPasskey = "passkey", kindPinPad = "pin_pad"
    public static let openStates: Set<String> = ["waiting", "expired", "renewing", "checking", "finishing"]

    public var stepId: String
    public var kind: String
    public var state: String
    public var site = ""
    public var summary = ""
    public var amountText = ""
    public var payWith = ""
    public var items: [String] = []
    public var code: String? = nil
    public var codeShown: String? = nil
    public var qr: String? = nil
    public var expiresAt: Int64? = nil
    public var secondsTotal: Int? = nil
    public var instruction = ""
    public var canResend = false
    public var needsCode = false
    public var taps = 0
    public var canClear = false
    public var tab = ""
    public var openedAt: Double = 0

    public init(stepId: String, kind: String, state: String = "waiting") { self.stepId = stepId; self.kind = kind; self.state = state }

    public var id: String { stepId }
    public var open: Bool { Self.openStates.contains(state) }
    public var isPad: Bool { kind == Self.kindPinPad }

    /// One `payment_step` event, or one GET /api/payment-steps item; nil without a step id.
    public static func parse(_ e: JSON?) -> PaymentStep? {
        let o = Lenient(e)
        let id = o.str("step_id")
        if id.isEmpty { return nil }
        let order = o.obj("order")
        var s = PaymentStep(stepId: id, kind: o.str("kind"), state: o.str("state").isEmpty ? "waiting" : o.str("state"))
        s.site = o.str("site")
        s.summary = order.str("summary")
        s.amountText = order.str("amount_text")
        s.payWith = order.str("pay_with")
        s.items = (order["items"]?.arrayValue ?? []).compactMap { v -> String? in
            guard v.objectValue != nil else { return nil }
            let i = Lenient(v)
            let q = i.optInt("quantity") ?? 1
            let name = i.str("name")
            let line = q > 1 ? "\(name) × \(q)" : name
            return line.trimmingCharacters(in: .whitespaces).isEmpty ? nil : line
        }
        s.code = o.optStr("code").flatMap { $0.isEmpty ? nil : $0 }
        s.codeShown = o.optStr("code_shown").flatMap { $0.isEmpty ? nil : $0 }
        let qr = o.str("qr")
        s.qr = qr.hasPrefix("data:image/") ? qr : nil
        s.expiresAt = o.optInt64("expires_at")
        s.secondsTotal = o.optInt("seconds_total")
        s.instruction = o.str("instruction")
        s.canResend = o.optBool("can_resend") == true
        s.needsCode = o.optBool("needs_code") == true
        s.taps = o.int("taps")
        s.canClear = o.optBool("can_clear") == true
        s.tab = o.str("tab")
        s.openedAt = o.double("opened_at")
        return s
    }

    /// GET /api/payment-steps: {"items": [...]} (or a bare list from an older shape).
    public static func parseList(_ e: JSON) -> [PaymentStep] {
        let arr = e.arrayValue ?? e["items"]?.arrayValue ?? []
        return arr.compactMap { parse($0) }
    }
}

/// A payment's PIN waits for this phone to release it (Face ID) until `expiresAt`. `urgent`: nobody asked on a card.
public struct PinNeed: Equatable, Sendable, Identifiable {
    public var slotId: String, pinId: String, pinLabel: String, site: String, amountText: String
    public var digits: Int
    public var expiresAt: Int64
    public var urgent: Bool

    public init(slotId: String, pinId: String, pinLabel: String = "", site: String = "", amountText: String = "", digits: Int = 0,
                expiresAt: Int64 = 0, urgent: Bool = false) {
        self.slotId = slotId; self.pinId = pinId; self.pinLabel = pinLabel; self.site = site; self.amountText = amountText
        self.digits = digits; self.expiresAt = expiresAt; self.urgent = urgent
    }

    public var id: String { slotId }

    public static func parse(_ e: JSON?) -> PinNeed? {
        let o = Lenient(e)
        guard o["pin"]?.objectValue != nil else { return nil }
        let pin = o.obj("pin")
        var slot = o.str("slot_id")
        if slot.isEmpty { slot = o.str("approval_id") }
        if slot.isEmpty { return nil }
        return PinNeed(slotId: slot, pinId: pin.str("id"), pinLabel: pin.str("label"), site: o.str("site"), amountText: o.str("amount_text"),
                       digits: pin.int("digits"), expiresAt: o.int64("expires_at"), urgent: o.optBool("urgent") == true)
    }
}

/// approval_request.pin: Allow also releases the site's payment PIN from this phone (after the owner's check).
public struct PinAsk: Equatable, Sendable {
    public var id: String, label: String, site: String
    public var digits: Int

    public init(id: String, label: String = "", site: String = "", digits: Int = 0) {
        self.id = id; self.label = label; self.site = site; self.digits = digits
    }

    public static func parse(_ e: JSON?) -> PinAsk? {
        let o = Lenient(e)
        guard o.optBool("needs_pin") == true, !o.str("id").isEmpty else { return nil }
        return PinAsk(id: o.str("id"), label: o.str("label"), site: o.str("site"), digits: o.int("digits"))
    }
}

public enum PaymentStepRules {
    static let max = 12

    /// The list after an event or a fresh load: the same step is replaced in place, a new one is added; ended ones stay folded.
    public static func upsert(_ steps: [PaymentStep], _ step: PaymentStep) -> [PaymentStep] {
        guard let i = steps.firstIndex(where: { $0.stepId == step.stepId }) else { return steps + [step] }
        // an older copy (a load that crossed an event) never reopens a step this phone saw end
        if !steps[i].open && step.open { return steps }
        var out = steps
        out[i] = step
        return out
    }

    /// GET /api/payment-steps: what the server lists, merged into what this phone has.
    public static func refresh(_ steps: [PaymentStep], _ fresh: [PaymentStep]) -> [PaymentStep] {
        var out = steps.filter { s in fresh.contains { $0.stepId == s.stepId } || !s.open }
        for f in fresh { out = upsert(out, f) }
        return Array(out.suffix(max))
    }

    /// Steps the chat shows: the open ones, then the last one that ended (folded), newest last.
    public static func visible(_ steps: [PaymentStep]) -> [PaymentStep] {
        let open = steps.filter(\.open)
        let ended = steps.filter { !$0.open }.suffix(1)
        return (Array(ended) + open).sorted { $0.openedAt < $1.openedAt }
    }

    /// "482 913" as shown on the page, else the code in groups of three.
    public static func codeText(_ step: PaymentStep) -> String {
        let shown = step.codeShown?.trimmingCharacters(in: .whitespaces) ?? ""
        if !shown.isEmpty { return shown }
        let c = step.code ?? ""
        guard (6...9).contains(c.count), c.allSatisfy(\.isASCIIDigit) else { return c }
        var parts: [String] = []
        var rest = Substring(c)
        while !rest.isEmpty { parts.append(String(rest.prefix(3))); rest = rest.dropFirst(3) }
        return parts.joined(separator: " ")
    }

    /// Seconds left (never below 0), or nil when the page gave no time.
    public static func secondsLeft(_ step: PaymentStep, nowS: Int64) -> Int64? { step.expiresAt.map { Swift.max(0, $0 - nowS) } }

    /// Share of the time left for the bar under the countdown (0...1), or nil.
    public static func progress(_ step: PaymentStep, nowS: Int64) -> Double? {
        guard let total = step.secondsTotal, let left = secondsLeft(step, nowS: nowS), total > 0 else { return nil }
        return Swift.min(1, Swift.max(0, Double(left) / Double(total)))
    }

    /// Whether the card shows the "expired, get a new one?" choice: the server said so, or the countdown reached zero here.
    public static func expired(_ step: PaymentStep, nowS: Int64) -> Bool {
        step.state == "expired" || (step.state == "waiting" && step.expiresAt != nil && nowS >= step.expiresAt!)
    }

    /// A tap at (`x`, `y`) of a picture shown `w` x `h`: fractions 0...1 of it, or nil outside.
    public static func tapFraction(x: Double, y: Double, w: Double, h: Double) -> (Double, Double)? {
        if w <= 0 || h <= 0 || x < 0 || y < 0 || x > w || y > h { return nil }
        return (x / w, y / h)
    }

    /// The dots under the keypad picture: one per tap counted, at most 8 shown.
    public static func dots(_ taps: Int) -> String { String(repeating: "●", count: Swift.min(8, Swift.max(0, taps))) }

    /// "1:05" / "0:09" for the countdown.
    public static func clock(_ seconds: Int64) -> String { String(format: "%d:%02d", seconds / 60, seconds % 60) }
}

private extension Character {
    var isASCIIDigit: Bool { ("0"..."9").contains(self) }
}

/// What the task setup card says about the payment card.
public enum CardLimitLine: Sendable {
    /// The card pays on its own up to its limit
    case autoLimit
    /// The card does not pay on its own: the task's limit is the only one
    case notAuto
    /// The card is kept on the phone only: each payment still asks
    case phoneOnly
    /// No card is named
    case none
}

public enum TaskGrantRules {
    /// approval_request.grant; nil when missing or when it names neither a trigger nor an action.
    public static func parse(_ e: JSON?) -> TaskGrantAsk? {
        guard let e, e.objectValue != nil, let g = try? JSON.decode(TaskGrantAsk.self, from: e) else { return nil }
        let blank = { (s: String) in s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        return blank(g.trigger) && blank(g.action) ? nil : g
    }

    public static func cardLine(_ p: TaskGrantPurchase) -> CardLimitLine {
        guard let card = p.card else { return .none }
        if card.phoneOnly { return .phoneOnly }
        if card.autoPay && !card.autoLimitText.trimmingCharacters(in: .whitespaces).isEmpty { return .autoLimit }
        return .notAuto
    }

    /// The task is allowed more than the card would pay on its own.
    public static func aboveCardLimit(_ p: TaskGrantPurchase) -> Bool {
        guard let card = p.card, let limit = card.autoLimit else { return false }
        return card.autoPay && p.maxAmount > limit
    }

    /// "받는 사람" for a message permission: the recipients, then the site it posts on.
    public static func recipients(_ s: TaskGrantSend) -> String {
        (s.to + (s.site.trimmingCharacters(in: .whitespaces).isEmpty ? [] : [s.site])).joined(separator: ", ")
    }

    /// Where a change permission applies, without the server's own prefixes ("connector:notion" → "notion", "site:x" → "x").
    public static func places(_ c: TaskGrantChange) -> String { c.places.map(stripPrefix).joined(separator: ", ") }

    public static func stripPrefix(_ s: String) -> String {
        for p in ["connector:", "site:"] where s.hasPrefix(p) { return String(s.dropFirst(p.count)) }
        return s
    }
}

extension ScheduleGrant {
    public static let kindSend = "send", kindChange = "change"
    public var isSend: Bool { kind == Self.kindSend }
    public var isChange: Bool { kind == Self.kindChange }
    /// The task's name for the wallet and login pages (a watcher's or job's grant has no schedule name).
    public var ownerName: String { taskName.trimmingCharacters(in: .whitespaces).isEmpty ? scheduleName : taskName }
    /// Where a send / change grant applies, without the server's prefixes.
    public var placesText: String { targets.map(TaskGrantRules.stripPrefix).joined(separator: ", ") }
    /// How many of the allowed times are left (nil without a count).
    public var left: Int? { maxCount > 0 ? Swift.max(0, maxCount - used) : nil }
    /// Where a grant is revoked: under its own task.
    public var revokePath: String {
        switch scope {
        case "watcher": "/api/watchers/\(KeloApi.segment(scopeRef))/grants/\(id)"
        case "job": "/api/workers/\(KeloApi.segment(scopeRef))/grants/\(id)"
        case "schedule": "/api/schedules/\(scheduleId > 0 ? String(scheduleId) : KeloApi.segment(scopeRef))/grants/\(id)"
        default: "/api/task-grants/\(id)"
        }
    }
}

/// The payment step endpoints (server.py PAYMENT STEPS).
public struct PaymentStepCalls: Sendable {
    let api: KeloApi
    public init(api: KeloApi) { self.api = api }

    public func list() async throws -> [PaymentStep] { PaymentStep.parseList(try await api.request("GET", "/api/payment-steps")) }

    public func answer(_ stepId: String, _ answer: String, code: String? = nil) async throws {
        var body: [String: JSON] = ["answer": .string(answer)]
        if let code { body["code"] = .string(code) }
        _ = try await api.request("POST", "/api/payment-steps/\(KeloApi.segment(stepId))", body: .object(body))
    }

    /// The page's keypad region now (JPEG bytes).
    public func pad(_ stepId: String) async throws -> Data { try await api.download("/api/payment-steps/\(KeloApi.segment(stepId))/pad") }

    /// A tap at fractions of the keypad picture: the taps counted.
    public func tap(_ stepId: String, x: Double, y: Double) async throws -> Int {
        Lenient(try await api.request("POST", "/api/payment-steps/\(KeloApi.segment(stepId))/pad/tap",
                                      body: ["x": .number(x), "y": .number(y)])).int("taps")
    }

    public func clear(_ stepId: String) async throws -> Int {
        Lenient(try await api.request("POST", "/api/payment-steps/\(KeloApi.segment(stepId))/pad/clear")).int("taps")
    }
}
