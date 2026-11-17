import Foundation

// Secure entries the agent asks for (server secret_requests.py; Android chat/vault/VaultModels.kt SecretRequest, SecretRules,
// SecretEntryRules, SecretCalls). A request never holds a value: the value goes from the entry straight to the vault (a key or a
// login) or the wallet (a card), never into the chat, the transcript, a log or a notification.

/// A card a card entry added (secret_request result): its nickname and last four digits, never the number.
public struct SecretCard: Equatable, Hashable, Sendable {
    public var id: String, label: String

    public init(id: String = "", label: String = "") {
        self.id = id; self.label = label
    }

    static func parse(_ o: Lenient) -> SecretCard {
        SecretCard(id: o.str("id"), label: o.str("label"))
    }
}

/// How an answered entry went: a key's test ("ok" / "unchecked"), how a login is kept, or the card that was added.
public struct SecretResult: Equatable, Hashable, Sendable {
    public var checked: String?, card: SecretCard?

    public init(checked: String? = nil, card: SecretCard? = nil) {
        self.checked = checked; self.card = card
    }

    static func parse(_ e: JSON?) -> SecretResult? {
        guard e?.objectValue != nil else { return nil }
        let o = Lenient(e)
        return SecretResult(checked: o.optStr("checked"),
                            card: o["card"]?.objectValue != nil ? SecretCard.parse(o.obj("card")) : nil)
    }
}

/// The user name a login request may carry as a non-secret prefill (`username_hint`; "" when none, and absent on an older server).
/// Kept only when it is one line of at most 200 characters without control characters; anything else counts as none.
public enum UsernameHint {
    public static let maxLength = 200

    public static func clean(_ raw: String?) -> String {
        let t = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        if t.count > maxLength || t.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) { return "" }
        return t
    }
}

/// A secure entry the agent asked for: a service key (kind "key", the vault item `name`), a website login (`kindLogin`) or a payment
/// card (`kindCard`, `name` empty). `hosts`: where the value may be sent (a card: where it will pay; may be empty). `state`: open,
/// saved, cancelled, expired or replaced. `messageId`: the agent message it goes under (only from GET /api/secret-requests; nil while
/// that reply is still being written). `source` "draft": a vault item waiting for its value that no agent request stands for; listed
/// in the Keychain only, never in the chat.
public struct SecretRequest: Equatable, Hashable, Sendable, Identifiable {
    public static let kindLogin = "login", kindCard = "card", kindPin = "payment_pin"
    public static let stateOpen = "open", stateSaved = "saved", stateCancelled = "cancelled", stateExpired = "expired",
                      stateReplaced = "replaced"
    public static let sourceAgent = "agent"

    public var id: String
    public var kind: String
    public var name: String
    public var label: String
    public var hosts: [String]
    public var reason: String
    public var check: Bool
    public var source: String
    public var state: String
    public var messageId: Int64?
    public var createdAt: Double
    public var result: SecretResult?
    /// A login's prefilled user name (`UsernameHint`; "" when the agent gave none).
    public var usernameHint: String

    public init(id: String, kind: String = "", name: String = "", label: String = "", hosts: [String] = [], reason: String = "",
                check: Bool = false, source: String = SecretRequest.sourceAgent,
                state: String = SecretRequest.stateOpen, messageId: Int64? = nil, createdAt: Double = 0,
                result: SecretResult? = nil, usernameHint: String = "") {
        self.id = id; self.kind = kind; self.name = name; self.label = label; self.hosts = hosts; self.reason = reason
        self.check = check; self.source = source
        self.state = state; self.messageId = messageId; self.createdAt = createdAt
        self.result = result; self.usernameHint = UsernameHint.clean(usernameHint)
    }

    public var open: Bool { state == Self.stateOpen }
    public var isLogin: Bool { kind == Self.kindLogin }
    public var isCard: Bool { kind == Self.kindCard }
    /// A site's payment PIN (request_payment_pin): sealed on the phone like a card, never typed into the chat.
    public var isPin: Bool { kind == Self.kindPin }
    public var fromAgent: Bool { source == Self.sourceAgent }

    /// The "request" object of a secret_request turn event (or one GET /api/secret-requests item); nil when it is not one.
    public static func parse(_ e: JSON?) -> SecretRequest? {
        guard e?.objectValue != nil else { return nil }
        let o = Lenient(e)
        let id = o.str("id")
        guard !id.isEmpty else { return nil }
        let source = o.str("source")
        let state = o.str("state")
        return SecretRequest(
            id: id, kind: o.str("kind"), name: o.str("name"), label: o.str("label"), hosts: o.strings("hosts"), reason: o.str("reason"),
            check: o.bool("check"), source: source.isEmpty ? sourceAgent : source,
            state: state.isEmpty ? stateOpen : state,
            messageId: o.optInt64("message_id"), createdAt: o.double("created_at"),
            result: SecretResult.parse(o["result"]), usernameHint: o.str("username_hint"))
    }

    /// {"items": [...]} or a bare list; unreadable entries are left out.
    public static func parseList(_ e: JSON) -> [SecretRequest] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap(parse)
    }
}

/// What a save answers: `turnId` is a reply the server started to carry on (follow it like this phone's own), or nil (the reply
/// that asked is still waiting and goes on by itself).
public struct SecretSaveAnswer: Equatable, Sendable {
    /// `reply` (newer servers): the reply that asked is still running and goes on by itself / a new reply started (`turnId`) / one
    /// will start later / no reply follows. nil from an older server.
    public static let replyNone = "none"

    public var checked: Bool, turnId: String?
    public var reply: String?

    public init(checked: Bool = false, turnId: String? = nil, reply: String? = nil) {
        self.checked = checked; self.turnId = turnId; self.reply = reply
    }

    /// The reply to follow here: `turnId` unless the server says none follows.
    public var turnToFollow: String? { reply == Self.replyNone ? nil : turnId }

    public static func parse(_ e: JSON) -> SecretSaveAnswer {
        let o = Lenient(e)
        let turn = o.optStr("turn_id")?.trimmingCharacters(in: .whitespaces)
        let reply = o.optStr("reply")?.trimmingCharacters(in: .whitespaces)
        return SecretSaveAnswer(checked: o.bool("checked"), turnId: turn?.isEmpty == false ? turn : nil,
                                reply: reply?.isEmpty == false ? reply : nil)
    }
}

/// How a typed login is kept (LoginIn.mode on the server): only once a sign-in with it works (the default), now, or for this one
/// sign-in only.
public enum SecretLoginMode: String, CaseIterable, Sendable { case afterSuccess = "after_success", now, once }

/// GET /api/secret-requests and the answers a secure entry gives (Android SecretCalls + VaultCalls.putSecret / putLogin).
public struct SecretCalls: Sendable {
    public let api: KeloApi
    public init(api: KeloApi) { self.api = api }

    /// The open requests (each with the agent message it goes under, or nil while that reply is still written).
    public func list() async throws -> [SecretRequest] {
        SecretRequest.parseList(try await api.request("GET", "/api/secret-requests"))
    }

    /// PUT /api/credentials/{name}/secret (422 when the service refused the key on its test request: nothing was saved).
    public func putSecret(name: String, secret: String) async throws -> SecretSaveAnswer {
        SecretSaveAnswer.parse(try await api.request("PUT", "/api/credentials/\(KeloApi.segment(name))/secret", body: ["secret": .string(secret)]))
    }

    /// PUT /api/vault/{name}/login
    public func putLogin(name: String, username: String, password: String, mode: SecretLoginMode) async throws -> SecretSaveAnswer {
        SecretSaveAnswer.parse(try await api.request("PUT", "/api/vault/\(KeloApi.segment(name))/login", body: [
            "username": .string(username), "password": .string(password), "mode": .string(mode.rawValue),
        ]))
    }

    /// The card form saved `cardId` in the wallet for the card entry `id`.
    public func cardSaved(id: String, cardId: String) async throws -> SecretSaveAnswer {
        SecretSaveAnswer.parse(try await api.request("POST", "/api/secret-requests/\(KeloApi.segment(id))/card", body: ["card_id": .string(cardId)]))
    }

    /// [Cancel]: the agent is told and does not ask again; an empty vault item made for it is removed.
    /// The answer may name a reply the server started to carry on (`turnToFollow`).
    @discardableResult
    public func cancel(id: String) async throws -> SecretSaveAnswer {
        SecretSaveAnswer.parse(try await api.request("POST", "/api/secret-requests/\(KeloApi.segment(id))/cancel"))
    }
}

/// Where secure entries show and how a new state lands (pure).
public enum SecretRules {
    /// A request after `incoming`: the newer state wins; an answered request never opens again.
    public static func merge(_ old: SecretRequest?, _ incoming: SecretRequest) -> SecretRequest {
        guard let old else { return incoming }
        if !old.open && incoming.open { return old }
        var r = incoming
        if r.messageId == nil { r.messageId = old.messageId }
        return r
    }

    /// The list kept for the chat after GET /api/secret-requests: only the server's open ones (merged with what was kept). An entry
    /// answered here lives on only in the chat's rows, and only while a reply on this phone goes on (`followsHere`) (Android
    /// SecretRules.refresh).
    public static func refresh(_ kept: [SecretRequest], _ fresh: [SecretRequest]) -> [SecretRequest] {
        settled(fresh.map { f in merge(kept.first { $0.id == f.id }, f) })
    }

    /// After an entry was answered here (saved, cancelled, or found gone): whether a reply on this phone will end later and take the
    /// entry's line away then. True when the answer names a reply to follow (unless it says "none"), or, without one, when a reply
    /// runs here now (`busy`) and the server does not say "none". Otherwise the line goes at once: nothing here would clear it.
    public static func followsHere(_ answer: SecretSaveAnswer?, busy: Bool) -> Bool {
        if answer?.reply == SecretSaveAnswer.replyNone { return false }
        if answer?.turnId != nil { return true }
        return busy
    }

    /// At every end of a reply (done, failed, stopped) and on a reload: only the entries still open are kept (Android
    /// SecretRules.settled; spec 5.16).
    public static func settled(_ list: [SecretRequest]) -> [SecretRequest] { list.allSatisfy(\.open) ? list : list.filter(\.open) }

    /// "api.telegram.org" / "a.com, b.com"; empty when there are none.
    public static func hostsText(_ hosts: [String]) -> String { hosts.joined(separator: ", ") }

    /// Requests for the Keychain's "waiting for input" list: open ones, newest first.
    public static func waiting(_ list: [SecretRequest]) -> [SecretRequest] {
        list.filter(\.open).sorted { $0.createdAt > $1.createdAt }
    }
}

/// When [저장] may be pressed (pure).
public enum SecretEntryRules {
    /// A key: at least 8 characters, one line, no spaces inside (the server refuses anything else).
    public static func keyReady(_ value: String) -> Bool {
        let t = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.count >= 8 && !t.contains { $0.isWhitespace }
    }

    /// A login: a user name and a password, both one line.
    public static func loginReady(user: String, pass: String) -> Bool {
        !user.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !pass.isEmpty && !(user + pass).contains { $0 == "\n" || $0 == "\r" || $0 == "\r\n" }
    }
}
