import Foundation

/// One piece of the data trail's question on an approval card (Android agent/RecipientAllows.kt): data of one `kind` (mail,
/// calendar...) would go to `recipient`. `titles` are the names of the items, never their content.
public struct TrailOffer: Equatable, Sendable {
    public var recipient: String
    public var kind: String
    public var titles: [String]

    public init(recipient: String, kind: String, titles: [String] = []) {
        self.recipient = recipient; self.kind = kind; self.titles = titles
    }

    /// The recipient as the user reads it: the server's internal prefixes dropped; nil for the public link (the app words that).
    public var shown: String? { RecipientRules.shown(recipient) }

    /// The card's `trail` list; an offer without a recipient or a kind is dropped.
    public static func parseList(_ e: JSON?) -> [TrailOffer] {
        (e?.arrayValue ?? []).compactMap { item -> TrailOffer? in
            guard let o = item.objectValue else { return nil }
            func t(_ k: String) -> String { o[k]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
            let recipient = t("recipient"), kind = t("kind")
            guard !recipient.isEmpty, !kind.isEmpty else { return nil }
            var seen = Set<String>()
            let titles = (o["titles"]?.arrayValue ?? []).compactMap { $0.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty && seen.insert($0).inserted }
            return TrailOffer(recipient: recipient, kind: kind, titles: titles)
        }
    }
}

/// A standing "always for this recipient" (GET /api/permissions/recipients items).
public struct RecipientAllow: Equatable, Sendable, Identifiable {
    public var id: Int64
    public var recipient: String
    public var kind: String
    public var createdAt: Double

    public init(id: Int64, recipient: String = "", kind: String = "", createdAt: Double = 0) {
        self.id = id; self.recipient = recipient; self.kind = kind; self.createdAt = createdAt
    }

    public static func parse(_ e: JSON) -> RecipientAllow? {
        guard e.objectValue != nil, let id = Lenient(e).optInt64("id") else { return nil }
        let o = Lenient(e)
        return RecipientAllow(id: id, recipient: o.str("recipient"), kind: o.str("kind"), createdAt: o.double("created_at"))
    }

    /// {"items": [...]} or a bare list.
    public static func parseList(_ e: JSON) -> [RecipientAllow] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap(parse)
    }
}

/// What the data-trail card and the list page decide, away from the screens so it can be tested.
public enum RecipientRules {
    /// The kinds the server names (data_trail.SOURCE_WORDS); any other kind is shown as plain "data".
    public static let kinds = ["mail", "file", "memory", "contacts", "calendar", "photos", "messages", "notes", "vault", "profile",
                               "web", "channel", "device", "connector"]
    public static func knownKind(_ kind: String) -> String { kinds.contains(kind) ? kind : "other" }

    public static let link = "link"

    /// The recipient without the server's internal prefixes; nil for the public link and for an empty one.
    public static func shown(_ recipient: String) -> String? {
        var r = recipient.trimmingCharacters(in: .whitespacesAndNewlines)
        for p in ["site:", "connector:"] where r.hasPrefix(p) { r.removeFirst(p.count) }
        r = r.trimmingCharacters(in: .whitespacesAndNewlines)
        return r.isEmpty || r == link ? nil : r
    }

    /// The card offers "always for this recipient" only when the server named at least one recipient.
    public static func canAlways(_ offers: [TrailOffer]) -> Bool {
        offers.contains { !$0.recipient.trimmingCharacters(in: .whitespaces).isEmpty && !$0.kind.trimmingCharacters(in: .whitespaces).isEmpty }
    }

    /// The three answers of a data-trail card: `approve` and the value of POST /api/approvals/{id}'s `remember`.
    public enum Answer: Sendable {
        case once, always, deny
        public var approve: Bool { self != .deny }
        public var remember: String? { self == .always ? "recipient" : nil }
    }

    /// The list page shows the newest first.
    public static func ordered(_ items: [RecipientAllow]) -> [RecipientAllow] { items.sorted { $0.id > $1.id } }
    public static func without(_ items: [RecipientAllow], _ id: Int64) -> [RecipientAllow] { items.filter { $0.id != id } }
}

public extension KeloApi {
    /// GET /api/permissions/recipients; an older server without it (404) has none.
    func recipientAllows() async throws -> [RecipientAllow] {
        do { return RecipientRules.ordered(RecipientAllow.parseList(try await request("GET", "/api/permissions/recipients"))) }
        catch let e as ApiError where e.status == 404 { return [] }
    }

    /// DELETE /api/permissions/recipients/{id}: the next such send asks again.
    func removeRecipientAllow(_ id: Int64) async throws { _ = try await request("DELETE", "/api/permissions/recipients/\(id)") }
}
