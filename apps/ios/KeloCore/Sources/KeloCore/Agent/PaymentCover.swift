import Foundation

/// One thing in an order approved in advance.
public struct PreapprovalItem: Equatable, Sendable {
    public var description: String
    public var qty: Int
    public init(description: String, qty: Int = 1) { self.description = description; self.qty = qty }

    static func parseList(_ e: JSON?) -> [PreapprovalItem] {
        (e?.arrayValue ?? []).compactMap { i in
            guard i.objectValue != nil else { return nil }
            let o = Lenient(i)
            let d = (i["description"]?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            return d.isEmpty ? nil : PreapprovalItem(description: d, qty: max(1, o.optInt("qty") ?? 1))
        }
    }
}

/// Read-only helpers for the payment-cover shapes: only real strings count, trimmed.
enum CoverJSON {
    static func string(_ e: JSON?, _ k: String) -> String { (e?[k]?.stringValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }

    static func lines(_ e: JSON?, _ k: String) -> [String] {
        (e?[k]?.arrayValue ?? []).compactMap { $0.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }
}

/// One order the user approved in advance (GET /api/permissions/preapprovals items; Android agent/PaymentCoverModels.kt): what, the
/// all-in cap, until when, for which task or schedule.
public struct Preapproval: Equatable, Sendable, Identifiable {
    public var id: String
    public var ownerKind: String
    /// The schedule's name; "" for a task.
    public var ownerName: String
    public var intent: String
    public var items: [PreapprovalItem]
    public var maxTotal: Double
    public var currency: String
    /// Unix seconds.
    public var expiresAt: Int64

    public init(id: String, ownerKind: String = "", ownerName: String = "", intent: String = "", items: [PreapprovalItem] = [],
                maxTotal: Double = 0, currency: String = "", expiresAt: Int64 = 0) {
        self.id = id; self.ownerKind = ownerKind; self.ownerName = ownerName; self.intent = intent; self.items = items
        self.maxTotal = maxTotal; self.currency = currency; self.expiresAt = expiresAt
    }

    public var forSchedule: Bool { ownerKind == "schedule" }

    public static func parse(_ e: JSON) -> Preapproval? {
        guard e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let id = o.str("id").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty else { return nil }
        return Preapproval(id: id, ownerKind: o.str("owner_kind"), ownerName: o.str("owner_name"), intent: o.str("intent"),
                           items: PreapprovalItem.parseList(o["items"]), maxTotal: o.double("max_total"),
                           currency: o.str("currency").uppercased(), expiresAt: o.int64("expires_at"))
    }
}

/// What an Allow on a scheduled task's payment card also covers: the same shop and the same payment method
/// (GET /api/permissions/pay-allows items).
public struct PayAllow: Equatable, Sendable, Identifiable {
    public var id: String
    public var scheduleName: String
    public var merchantOrigin: String
    public var paymentLabel: String
    public var currency: String

    public init(id: String, scheduleName: String = "", merchantOrigin: String = "", paymentLabel: String = "", currency: String = "") {
        self.id = id; self.scheduleName = scheduleName; self.merchantOrigin = merchantOrigin; self.paymentLabel = paymentLabel
        self.currency = currency
    }

    public static func parse(_ e: JSON) -> PayAllow? {
        guard e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let id = o.str("id").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty else { return nil }
        return PayAllow(id: id, scheduleName: o.str("schedule_name"), merchantOrigin: o.str("merchant_origin"),
                        paymentLabel: o.str("payment_label"), currency: o.str("currency"))
    }

    /// The shop as people read it: no scheme, no trailing slash.
    public var shop: String {
        var s = merchantOrigin.trimmingCharacters(in: .whitespacesAndNewlines)
        for p in ["https://", "http://"] where s.hasPrefix(p) { s.removeFirst(p.count) }
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }
}

/// The "Payments approved in advance" section: the switch and both lists.
public struct PaymentCover: Equatable, Sendable {
    public var enabled: Bool
    public var orders: [Preapproval]
    public var allows: [PayAllow]
    public init(enabled: Bool, orders: [Preapproval] = [], allows: [PayAllow] = []) { self.enabled = enabled; self.orders = orders; self.allows = allows }
    public var isEmpty: Bool { orders.isEmpty && allows.isEmpty }
}

/// The approval card of `order_preapprove`: the server's extra field `preapproval` (the spec, the all-in cap and when it ends).
/// `maxTotalText` is the server's own amount text; the card falls back to the number and currency.
public struct PreapprovalAsk: Equatable, Sendable {
    public var intent: String
    public var items: [PreapprovalItem]
    public var requirements: [String]
    public var shipping: String
    public var merchantPolicy: String
    public var delegatedChoices: [String]
    public var maxTotal: Double
    public var currency: String
    public var maxTotalText: String
    public var expiresAt: Int64
    public var ownerKind: String

    public init(intent: String = "", items: [PreapprovalItem] = [], requirements: [String] = [], shipping: String = "",
                merchantPolicy: String = "", delegatedChoices: [String] = [], maxTotal: Double = 0, currency: String = "",
                maxTotalText: String = "", expiresAt: Int64 = 0, ownerKind: String = "") {
        self.intent = intent; self.items = items; self.requirements = requirements; self.shipping = shipping
        self.merchantPolicy = merchantPolicy; self.delegatedChoices = delegatedChoices; self.maxTotal = maxTotal
        self.currency = currency; self.maxTotalText = maxTotalText; self.expiresAt = expiresAt; self.ownerKind = ownerKind
    }

    public var forSchedule: Bool { ownerKind == "schedule" }

    /// Nil when the card carries nothing a person could read (no what, no cap): it then shows as an ordinary card.
    public static func parse(_ e: JSON?) -> PreapprovalAsk? {
        guard let e, e.objectValue != nil else { return nil }
        let o = Lenient(e)
        let ask = PreapprovalAsk(
            intent: CoverJSON.string(e, "intent"), items: PreapprovalItem.parseList(e["items"]),
            requirements: CoverJSON.lines(e, "requirements"), shipping: CoverJSON.string(e, "shipping"),
            merchantPolicy: CoverJSON.string(e, "merchant_policy"), delegatedChoices: CoverJSON.lines(e, "delegated_choices"),
            maxTotal: o.double("max_total"), currency: CoverJSON.string(e, "currency").uppercased(),
            maxTotalText: CoverJSON.string(e, "max_total_text"), expiresAt: o.int64("expires_at"), ownerKind: CoverJSON.string(e, "owner_kind"))
        return ask.maxTotal > 0 && (!ask.intent.isEmpty || !ask.items.isEmpty) ? ask : nil
    }
}

/// The `payment_auto` event's note that the payment was covered in advance: `kind` "preapproval" or "schedule_allow", `via` the way it paid.
public struct PreapprovalCover: Equatable, Sendable {
    public static let kindOrder = "preapproval"
    public static let kindAllow = "schedule_allow"

    public var kind: String
    public var via: String
    public init(kind: String, via: String = "") { self.kind = kind; self.via = via }

    public var bySchedule: Bool { kind == Self.kindAllow }

    public static func parse(_ e: JSON?) -> PreapprovalCover? {
        guard let e, e.objectValue != nil else { return nil }
        let kind = CoverJSON.string(e, "kind")
        return kind.isEmpty ? nil : PreapprovalCover(kind: kind, via: CoverJSON.string(e, "via"))
    }
}

/// What the permissions section and the cards decide, away from the screens so it can be tested.
public enum PaymentCoverRules {
    /// The order that ends first on top (it needs the owner's eye soonest).
    public static func ordered(_ items: [Preapproval]) -> [Preapproval] {
        items.enumerated().sorted { a, b in
            let x = a.element.expiresAt > 0 ? a.element.expiresAt : Int64.max, y = b.element.expiresAt > 0 ? b.element.expiresAt : Int64.max
            return x != y ? x < y : a.offset < b.offset
        }.map(\.element)
    }

    public static func withoutOrder(_ c: PaymentCover, _ id: String) -> PaymentCover {
        var n = c; n.orders = c.orders.filter { $0.id != id }; return n
    }

    public static func withoutAllow(_ c: PaymentCover, _ id: String) -> PaymentCover {
        var n = c; n.allows = c.allows.filter { $0.id != id }; return n
    }

    /// What the order is: its sentence, else its items ("2 × running shoes, size 42").
    public static func what(intent: String, items: [PreapprovalItem]) -> String {
        let t = intent.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? items.map(item).joined(separator: ", ") : t
    }

    /// The card's "what": the order's sentence, then its items, one per line.
    public static func whatLines(_ p: PreapprovalAsk) -> [String] {
        (p.intent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? [] : [p.intent]) + p.items.map(item)
    }

    public static func item(_ i: PreapprovalItem) -> String { i.qty > 1 ? "\(i.qty) × \(i.description)" : i.description }

    /// A card's all-in cap as text: the server's own text, else the number and the currency code.
    public static func capText(_ ask: PreapprovalAsk) -> String { ask.maxTotalText.isEmpty ? amount(ask.maxTotal, currency: ask.currency) : ask.maxTotalText }

    public static func amount(_ v: Double, currency: String) -> String {
        let n = v == v.rounded(.down) && v < 1e12 ? String(Int64(v)) : String(format: "%.2f", v)
        return [n, currency].filter { !$0.isEmpty }.joined(separator: " ")
    }
}

public extension KeloApi {
    /// The payment cover (switch, orders, schedule allows); nil from a server that does not have it (404), so the section stays hidden.
    func paymentCover() async throws -> PaymentCover? {
        do {
            let orders = try await request("GET", "/api/permissions/preapprovals")
            let allows = try await request("GET", "/api/permissions/pay-allows")
            let sw = try await request("GET", "/api/permissions/payment-cover")
            return PaymentCover(enabled: sw["enabled"]?.boolValue ?? true,
                                orders: PaymentCoverRules.ordered((orders["items"]?.arrayValue ?? []).compactMap(Preapproval.parse)),
                                allows: (allows["items"]?.arrayValue ?? []).compactMap(PayAllow.parse))
        } catch let e as ApiError where e.status == 404 { return nil }
    }

    /// DELETE /api/permissions/preapprovals/{id}: it asks when it is paid. 404 = already gone (not thrown).
    func removePreapproval(_ id: String) async throws {
        do { _ = try await request("DELETE", "/api/permissions/preapprovals/\(KeloApi.segment(id))") }
        catch let e as ApiError where e.status == 404 {}
    }

    /// DELETE /api/permissions/pay-allows/{id}: the next payment of that schedule asks. 404 = already gone (not thrown).
    func removePayAllow(_ id: String) async throws {
        do { _ = try await request("DELETE", "/api/permissions/pay-allows/\(KeloApi.segment(id))") }
        catch let e as ApiError where e.status == 404 {}
    }

    /// PUT /api/permissions/payment-cover {"enabled"}: the switch as the server now has it.
    func setPaymentCover(_ on: Bool) async throws -> Bool {
        let r = try await request("PUT", "/api/permissions/payment-cover", body: .object(["enabled": .bool(on)]))
        return r["enabled"]?.boolValue ?? on
    }
}
