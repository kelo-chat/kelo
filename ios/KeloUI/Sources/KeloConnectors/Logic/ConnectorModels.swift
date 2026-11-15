import Foundation
import KeloCore

// The connectors tab's own models where KeloCore's are behind the server (Android connectors/ConnectorModels.kt): a connector row
// with every field GET /api/connectors sends today (fallback, method, account, connect_state, note, support_url), the Google
// Calendar state, the operator's OAuth app with its "open to all" switch, and the endpoints the panels call.

/// The other way to connect a service whose main way is a sign-in (Google Calendar: a Google service account key).
public struct ConnectorFallback: Codable, Equatable, Hashable, Sendable {
    public var connect: String
    public var via: String
    public var item: String
    public var blurb: String
    public var connected: Bool
    /// The link that opens this other way, in the server's words (an MCP sign-in that may refuse this app: "Connect with a key instead").
    public var title: String

    public init(connect: String = "", via: String = "", item: String = "", blurb: String = "", connected: Bool = false, title: String = "") {
        self.connect = connect; self.via = via; self.item = item; self.blurb = blurb; self.connected = connected; self.title = title
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        connect = j.str("connect"); via = j.str("via"); item = j.str("item"); blurb = j.str("blurb"); connected = j.bool("connected")
        title = j.str("title")
    }
}

/// GET /api/connectors items. `status`: connected | disabled | coming_soon | available | browser | unsupported. `connect`: how it is
/// connected here (device, mcp, mail, key, browser, google) or nil when it cannot be.
public struct ConnectorInfo: Codable, Equatable, Hashable, Sendable, Identifiable {
    public var id: String
    public var name: String
    public var description: String
    public var category: String
    public var status: String
    public var connect: String?
    public var item: String
    public var via: String
    public var site: String
    public var steps: [String]
    public var featured: Bool
    public var rank: Int?
    public var featuredRegions: [String]
    public var fallback: ConnectorFallback?
    /// How it is connected now, when the service has two ways: google | password | key.
    public var method: String
    /// Who is connected, when the server knows.
    public var account: String
    /// "pending_review": the Google sign-in is not open yet; `note` says so in the server's words.
    public var connectState: String
    public var note: String
    public var supportUrl: String
    /// The service's own app icon on the server (256 px, then 128 px for small tiles); empty when there is none (initials tile).
    public var icon: String
    public var iconSmall: String
    /// The icon's main colour ("#RRGGBB"), the tile's colour while it loads.
    public var iconColor: String

    public init(id: String, name: String = "", description: String = "", category: String = "", status: String = "",
                connect: String? = nil, item: String = "", via: String = "", site: String = "", steps: [String] = [],
                featured: Bool = false, rank: Int? = nil, featuredRegions: [String] = [], fallback: ConnectorFallback? = nil,
                method: String = "", account: String = "", connectState: String = "", note: String = "", supportUrl: String = "",
                icon: String = "", iconSmall: String = "", iconColor: String = "") {
        self.id = id; self.name = name; self.description = description; self.category = category; self.status = status
        self.connect = connect; self.item = item; self.via = via; self.site = site; self.steps = steps; self.featured = featured
        self.rank = rank; self.featuredRegions = featuredRegions; self.fallback = fallback; self.method = method
        self.account = account; self.connectState = connectState; self.note = note; self.supportUrl = supportUrl
        self.icon = icon; self.iconSmall = iconSmall; self.iconColor = iconColor
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optStr("id"))
        name = j.str("name"); description = j.str("description"); category = j.str("category"); status = j.str("status")
        connect = j.optStr("connect").flatMap { $0.isEmpty ? nil : $0 }
        item = j.str("item"); via = j.str("via"); site = j.str("site"); steps = j.strings("steps")
        featured = j.bool("featured"); rank = j.optInt("rank"); featuredRegions = j.strings("featured_regions")
        fallback = j.decode("fallback", as: ConnectorFallback.self)
        method = j.str("method"); account = j.str("account"); connectState = j.str("connect_state"); note = j.str("note")
        supportUrl = j.str("support_url")
        icon = j.str("icon"); iconSmall = j.str("icon_small"); iconColor = j.str("icon_color")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(id, forKey: AnyKey("id")); try c.encode(name, forKey: AnyKey("name"))
        try c.encode(description, forKey: AnyKey("description")); try c.encode(category, forKey: AnyKey("category"))
        try c.encode(status, forKey: AnyKey("status")); try c.encodeIfPresent(connect, forKey: AnyKey("connect"))
        try c.encode(item, forKey: AnyKey("item")); try c.encode(via, forKey: AnyKey("via")); try c.encode(site, forKey: AnyKey("site"))
        try c.encode(steps, forKey: AnyKey("steps")); try c.encode(featured, forKey: AnyKey("featured"))
        try c.encodeIfPresent(rank, forKey: AnyKey("rank")); try c.encode(featuredRegions, forKey: AnyKey("featured_regions"))
        try c.encodeIfPresent(fallback, forKey: AnyKey("fallback")); try c.encode(method, forKey: AnyKey("method"))
        try c.encode(account, forKey: AnyKey("account")); try c.encode(connectState, forKey: AnyKey("connect_state"))
        try c.encode(note, forKey: AnyKey("note")); try c.encode(supportUrl, forKey: AnyKey("support_url"))
        try c.encode(icon, forKey: AnyKey("icon")); try c.encode(iconSmall, forKey: AnyKey("icon_small"))
        try c.encode(iconColor, forKey: AnyKey("icon_color"))
    }

    /// KeloCore's connector (what ConnectorList's grouping, search and reconnect rules take).
    public var core: Connector {
        Connector(id: id, name: name, description: description, category: category, status: status, connect: connect, item: item,
                  via: via, site: site, steps: steps, featured: featured, rank: rank, featuredRegions: featuredRegions)
    }

    /// A copy with other connect fields (Google Calendar's service account panel, a broken connection shown as "available").
    public func with(status: String? = nil, connect: String? = nil, via: String? = nil, item: String? = nil, description: String? = nil) -> ConnectorInfo {
        var c = self
        if let status { c.status = status }
        if let connect { c.connect = connect }
        if let via { c.via = via }
        if let item { c.item = item }
        if let description { c.description = description }
        return c
    }
}

/// GET /api/google/calendar: whether the Google sign-in can be used on this server (and why not), and the connected accounts.
public struct GoogleCalendarState: Codable, Equatable, Sendable {
    public var available: Bool
    public var reason: String
    public var accounts: [GoogleCalendarAccount]
    /// New Google sign-ins wait for Google's review; accounts already connected keep working.
    public var pendingReview: Bool

    public init(available: Bool = false, reason: String = "", accounts: [GoogleCalendarAccount] = [], pendingReview: Bool = false) {
        self.available = available; self.reason = reason; self.accounts = accounts; self.pendingReview = pendingReview
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        available = j.bool("available"); reason = j.str("reason"); accounts = j.list("accounts"); pendingReview = j.bool("pending_review")
    }
}

public struct GoogleCalendarAccount: Codable, Equatable, Hashable, Sendable, Identifiable {
    public var name: String
    public var address: String
    public var canWrite: Bool
    /// Google stopped renewing the sign-in: connect again.
    public var problem: Bool
    public var id: String { name }

    public init(name: String, address: String = "", canWrite: Bool = false, problem: Bool = false) {
        self.name = name; self.address = address; self.canWrite = canWrite; self.problem = problem
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = try j.require("name", j.optStr("name")); address = j.str("address"); canWrite = j.bool("can_write"); problem = j.bool("problem")
    }
}

/// GET /api/oauth-apps/{provider}: the operator's OAuth app. The secret never comes back, only whether one is stored.
public struct OperatorApp: Codable, Equatable, Sendable {
    public var clientId: String
    public var hasSecret: Bool
    public var isBroker: Bool
    public var callback: String
    /// Google only: new sign-ins are open to everyone.
    public var openToAll: Bool

    public init(clientId: String = "", hasSecret: Bool = false, isBroker: Bool = false, callback: String = "", openToAll: Bool = false) {
        self.clientId = clientId; self.hasSecret = hasSecret; self.isBroker = isBroker; self.callback = callback; self.openToAll = openToAll
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        clientId = j.str("client_id"); hasSecret = j.bool("has_secret"); isBroker = j.bool("is_broker"); callback = j.str("callback")
        openToAll = j.bool("open_to_all")
    }
}

/// How a site login is kept (PUT /api/vault/{name}/login `mode`).
public enum VaultSaveMode: String, CaseIterable, Sendable {
    case afterSuccess = "after_success", now, once
}

/// The connector endpoints of server.py (Android ConnectorCalls). `baseUrl` is sent as the origin to come back to.
extension KeloApi {
    /// A list of models from a JSON array; entries that cannot be read (no id) are left out.
    static func lenientList<T: Decodable>(_ value: JSON, of type: T.Type) -> [T] {
        (value.arrayValue ?? []).compactMap { try? JSON.decode(T.self, from: $0) }
    }

    public func connectorList() async throws -> [ConnectorInfo] {
        KeloApi.lenientList(try await request("GET", "/api/connectors"), of: ConnectorInfo.self)
    }

    /// The keychain's items (never a value): who is connected and which connections need doing again.
    public func credentials() async throws -> [VaultEntry] {
        KeloApi.lenientList(try await request("GET", "/api/credentials"), of: VaultEntry.self)
    }

    public func saveClientId(_ provider: String, clientId: String) async throws -> OAuthProvider {
        try await put("/api/oauth/\(KeloApi.segment(provider))/client", body: ["client_id": .string(clientId)])
    }

    public func githubWebStart(scope: String) async throws -> String {
        try await post("/api/github/web/start", body: ["origin": .string(baseUrl), "scope": .string(scope)], as: UrlAnswer.self).url
    }

    public func operatorApp(_ provider: String) async throws -> OperatorApp { try await get("/api/oauth-apps/\(KeloApi.segment(provider))") }

    public func saveOperatorApp(_ provider: String, clientId: String, secret: String) async throws -> OperatorApp {
        var body: [String: JSON] = ["client_id": .string(clientId)]
        if !secret.isEmpty { body["client_secret"] = .string(secret) }
        return try await put("/api/oauth-apps/\(KeloApi.segment(provider))", body: .object(body))
    }

    public func setGoogleOpen(_ open: Bool) async throws -> OperatorApp {
        try await put("/api/oauth-apps/google/open", body: ["open": .bool(open)])
    }

    public func addMail(provider: String, address: String, password: String) async throws {
        try await request("POST", "/api/mail/accounts", body: ["provider": .string(provider), "address": .string(address),
                                                                "password": .string(password)])
    }

    public func googleWebStart(product: String = "gmail") async throws -> String {
        try await post("/api/google/web/start", body: ["origin": .string(baseUrl), "product": .string(product)], as: UrlAnswer.self).url
    }

    public func googleCalendar() async throws -> GoogleCalendarState { try await get("/api/google/calendar") }

    public func saveKey(_ id: String, values: [String: String]) async throws {
        try await request("POST", "/api/keys/\(KeloApi.segment(id))", body: ["values": .object(values.mapValues { .string($0) })])
    }

    /// A browser site's login to be entered on the secure form; returns the keychain item's name.
    public func addLogin(site: String, label: String) async throws -> String {
        try await post("/api/vault/logins", body: ["site": .string(site), "label": .string(label)], as: NameAnswer.self).name
    }

    public func putLogin(_ name: String, username: String, password: String, mode: VaultSaveMode) async throws {
        try await request("PUT", "/api/vault/\(KeloApi.segment(name))/login",
                          body: ["username": .string(username), "password": .string(password), "mode": .string(mode.rawValue)])
    }

    public func cancelLoginDraft(_ name: String) async throws {
        try await request("DELETE", "/api/vault/\(KeloApi.segment(name))/draft")
    }
}
