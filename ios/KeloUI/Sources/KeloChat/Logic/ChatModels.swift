import Foundation
import KeloCore

// The chat's own server models (Android chat/ChatModels.kt, SignIn.kt, SignOut.kt, vault/VaultModels.kt): KeloCore's generated
// models lack a few fields the chat needs (answer buttons on a message, a provider's sign-in state, a model's facts).

/// GET /api/messages items (oldest first). role is "user" or "assistant". `replies`: answers to tap under an agent message.
public struct ChatMessage: Codable, Equatable, Sendable {
    public var id: Int64
    public var role: String
    public var content: String
    public var createdAt: Double
    public var replies: ReplySet?
    /// The emoji on a user message (Reactions): 👀 while Kelo works on it, then one that fits; "" none (older servers send none).
    public var reaction: String
    /// On the morning briefing's message: its card (server briefing.py); the text is the same in plain words.
    public var briefing: BriefingCard?
    /// Screenshots Kelo showed with an agent reply (server chat_images.py); none on older servers.
    public var images: [ChatImage]
    /// The files the user sent with a message (server chat_attachments.py); none on older servers.
    public var attachments: [MessageFile]
    /// The text is the app's own sentence (nothing was typed): the bubble shows only the files.
    public var autoText: Bool

    public init(id: Int64, role: String = "", content: String = "", createdAt: Double = 0, replies: ReplySet? = nil, reaction: String = "",
                briefing: BriefingCard? = nil, images: [ChatImage] = [], attachments: [MessageFile] = [], autoText: Bool = false) {
        self.id = id; self.role = role; self.content = content; self.createdAt = createdAt; self.replies = replies; self.reaction = reaction
        self.briefing = briefing; self.images = images; self.attachments = attachments; self.autoText = autoText
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = try j.require("id", j.optInt64("id"))
        role = j.str("role")
        content = j.str("content")
        createdAt = j.double("created_at")
        replies = j.decode("replies", as: ReplySet.self)
        reaction = j.str("reaction")
        briefing = j.decode("briefing", as: BriefingCard.self)
        images = j.list("images", of: ChatImage.self)
        attachments = MessageFile.parseList(j["attachments"])
        autoText = j.bool("auto_text")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(id, forKey: AnyKey("id"))
        try c.encode(role, forKey: AnyKey("role"))
        try c.encode(content, forKey: AnyKey("content"))
        try c.encode(createdAt, forKey: AnyKey("created_at"))
        try c.encodeIfPresent(replies, forKey: AnyKey("replies"))
        if !reaction.isEmpty { try c.encode(reaction, forKey: AnyKey("reaction")) }
        try c.encodeIfPresent(briefing, forKey: AnyKey("briefing"))
        if !images.isEmpty { try c.encode(images, forKey: AnyKey("images")) }
        if !attachments.isEmpty { try c.encode(attachments, forKey: AnyKey("attachments")) }
        if autoText { try c.encode(true, forKey: AnyKey("auto_text")) }
    }

    /// The server sends the list bare; anything without an id is left out.
    public static func parseList(_ e: JSON) -> [ChatMessage] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap { try? KeloApi.decode(ChatMessage.self, $0) }
    }
}

/// Questions in an agent message with answers to tap; `goalId`: the goal the answers are for, if any.
public struct ReplySet: Codable, Equatable, Sendable {
    public var questions: [ReplyQuestion]
    public var goalId: Int64?

    public init(questions: [ReplyQuestion] = [], goalId: Int64? = nil) { self.questions = questions; self.goalId = goalId }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        questions = j.list("questions", of: ReplyQuestion.self)
        goalId = j.optInt64("goal_id")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(questions, forKey: AnyKey("questions"))
        try c.encodeIfPresent(goalId, forKey: AnyKey("goal_id"))
    }
}

/// `typed`: the options that mean the user writes the answer (a text box opens); `hint`: what to write there. Both may be missing
/// (an older server): see `QuickReplyRules.isTyped`. `kind`: the kind of answer (empty = choice; `ReplyFields`); `defaultValue`: where
/// its picker starts (plain form); `min`/`max`/`unit`: a number's.
public struct ReplyQuestion: Codable, Equatable, Sendable {
    public var question: String
    public var options: [String]
    public var typed: [String]
    public var hint: String
    public var kind: String
    public var defaultValue: String
    public var min: Double?
    public var max: Double?
    public var unit: String

    public init(question: String = "", options: [String] = [], typed: [String] = [], hint: String = "", kind: String = "",
                defaultValue: String = "", min: Double? = nil, max: Double? = nil, unit: String = "") {
        self.question = question; self.options = options; self.typed = typed; self.hint = hint
        self.kind = kind; self.defaultValue = defaultValue; self.min = min; self.max = max; self.unit = unit
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        question = j.str("question")
        options = j.strings("options")
        typed = j.strings("typed")
        hint = j.str("hint")
        kind = j.str("kind")
        defaultValue = j.str("default")
        min = j.optDouble("min")
        max = j.optDouble("max")
        unit = j.str("unit")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(question, forKey: AnyKey("question"))
        try c.encode(options, forKey: AnyKey("options"))
        if !typed.isEmpty { try c.encode(typed, forKey: AnyKey("typed")) }
        if !hint.isEmpty { try c.encode(hint, forKey: AnyKey("hint")) }
        if !kind.isEmpty { try c.encode(kind, forKey: AnyKey("kind")) }
        if !defaultValue.isEmpty { try c.encode(defaultValue, forKey: AnyKey("default")) }
        try c.encodeIfPresent(min, forKey: AnyKey("min"))
        try c.encodeIfPresent(max, forKey: AnyKey("max"))
        if !unit.isEmpty { try c.encode(unit, forKey: AnyKey("unit")) }
    }
}

/// How much chat history is kept on the phone for the next open (Android ChatHistoryCache, same limits).
public enum ChatHistory {
    public static let maxMessages = ChatHistoryCache.maxMessages
    public static let maxChars = ChatHistoryCache.maxChars

    public static func trim(_ messages: [ChatMessage]) -> [ChatMessage] {
        let last = messages.count > maxMessages ? Array(messages.suffix(maxMessages)) : messages
        var total = last.reduce(0) { $0 + $1.content.utf16.count }
        var from = 0
        while total > maxChars && from < last.count - 1 { total -= last[from].content.utf16.count; from += 1 }
        return from == 0 ? last : Array(last[from...])
    }
}

/// GET /api/providers items: what the chat, the chooser and the model sheet use.
public struct ChatProvider: Codable, Equatable, Sendable {
    public var name: String
    public var displayName: String
    /// true: ready; false: needs a key or a sign-in; nil: cannot be told
    public var configured: Bool?
    public var effortLevels: [String]
    public var effortNote: String?
    /// Signs in with a consumer account whose maker has not confirmed it allows this use
    public var unofficial: Bool
    public var vendor: String
    public var consent: ProviderConsentRecord?
    public var consentVersion: Int
    /// Reached by signing in and signed in now: true; signed out: false; nil: not a sign-in provider or unknown
    public var signedIn: Bool?
    public var account: String?
    /// The app can sign this provider in itself (POST /api/providers/{name}/login/start); absent on older servers
    public var signin: Bool
    /// Where chats go with this provider (the company), or nil for the owner's own network (`AiConsent`)
    public var company: String? = nil

    public init(name: String, displayName: String = "", configured: Bool? = nil, effortLevels: [String] = [], effortNote: String? = nil,
                unofficial: Bool = false, vendor: String = "", consent: ProviderConsentRecord? = nil, consentVersion: Int = 0,
                signedIn: Bool? = nil, account: String? = nil, signin: Bool = false) {
        self.name = name; self.displayName = displayName; self.configured = configured; self.effortLevels = effortLevels
        self.effortNote = effortNote; self.unofficial = unofficial; self.vendor = vendor; self.consent = consent
        self.consentVersion = consentVersion; self.signedIn = signedIn; self.account = account; self.signin = signin
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = try j.require("name", j.optStr("name"))
        displayName = j.str("display_name")
        configured = j.optBool("configured")
        effortLevels = j.strings("effort_levels")
        effortNote = j.optStr("effort_note")
        unofficial = j.bool("unofficial")
        vendor = j.str("vendor")
        consent = j.decode("consent", as: ProviderConsentRecord.self)
        consentVersion = j.int("consent_version")
        signedIn = j.optBool("signed_in")
        account = j.optStr("account")
        signin = j.bool("signin")
        company = j.optStr("company")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(name, forKey: AnyKey("name"))
        try c.encodeIfPresent(company, forKey: AnyKey("company"))
        try c.encode(displayName, forKey: AnyKey("display_name"))
        try c.encodeIfPresent(configured, forKey: AnyKey("configured"))
        try c.encode(effortLevels, forKey: AnyKey("effort_levels"))
        try c.encodeIfPresent(effortNote, forKey: AnyKey("effort_note"))
        try c.encode(unofficial, forKey: AnyKey("unofficial"))
        try c.encode(vendor, forKey: AnyKey("vendor"))
        try c.encodeIfPresent(consent, forKey: AnyKey("consent"))
        try c.encode(consentVersion, forKey: AnyKey("consent_version"))
        try c.encodeIfPresent(signedIn, forKey: AnyKey("signed_in"))
        try c.encodeIfPresent(account, forKey: AnyKey("account"))
        try c.encode(signin, forKey: AnyKey("signin"))
    }

    public static func parseList(_ e: JSON) -> [ChatProvider] {
        (e.arrayValue ?? e["items"]?.arrayValue ?? []).compactMap { try? KeloApi.decode(ChatProvider.self, $0) }
    }
}

/// One model's facts from GET /api/models: can it be shown pictures, its context window when known, and the thinking levels the
/// provider's own list states for it (nil: the list does not say, the provider's levels apply; empty: none).
public struct ModelFacts: Codable, Equatable, Sendable {
    public var images: Bool
    public var context: Int64?
    public var effortLevels: [String]?
    /// With empty `effortLevels`: why there are none (a custom server that turned the level away)
    public var effortNote: String?
    public init(images: Bool = false, context: Int64? = nil, effortLevels: [String]? = nil, effortNote: String? = nil) {
        self.images = images; self.context = context; self.effortLevels = effortLevels; self.effortNote = effortNote
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        images = j.bool("images")
        context = j.optInt64("context")
        effortLevels = j.has("effort_levels") ? j.strings("effort_levels") : nil
        effortNote = j.optStr("effort_note")
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(images, forKey: AnyKey("images"))
        try c.encodeIfPresent(context, forKey: AnyKey("context"))
        try c.encodeIfPresent(effortLevels, forKey: AnyKey("effort_levels"))
        try c.encodeIfPresent(effortNote, forKey: AnyKey("effort_note"))
    }
}

/// GET /api/models?provider=
public struct ChatModelList: Decodable, Equatable, Sendable {
    public var provider: String
    public var models: [String]
    public var labels: [String: String]
    public var info: [String: ModelFacts]
    public var error: String?
    /// The list could not be fetched just now: this is the last good one (fetched at `fetchedAt`, epoch seconds)
    public var stale: Bool
    public var fetchedAt: Int64?

    public init(provider: String = "", models: [String] = [], labels: [String: String] = [:], info: [String: ModelFacts] = [:], error: String? = nil,
                stale: Bool = false, fetchedAt: Int64? = nil) {
        self.provider = provider; self.models = models; self.labels = labels; self.info = info; self.error = error
        self.stale = stale; self.fetchedAt = fetchedAt
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        provider = j.str("provider")
        models = j.strings("models")
        var l: [String: String] = [:]
        for (k, v) in j["labels"]?.objectValue ?? [:] { if let t = v.text { l[k] = t } }
        labels = l
        var f: [String: ModelFacts] = [:]
        for (k, v) in j["info"]?.objectValue ?? [:] { if let facts = try? KeloApi.decode(ModelFacts.self, v) { f[k] = facts } }
        info = f
        error = j.optStr("error")
        stale = j.bool("stale")
        fetchedAt = j.optInt64("fetched_at")
    }
}

/// POST /api/providers/{name}/login/start, GET …/login/status and POST …/login/finish answer with this.
public struct SignInSession: Decodable, Equatable, Sendable {
    public var session: String
    /// "device_code": open `url`, type `userCode` there, the app notices by itself. "url_code": open `url`, paste back the code
    public var kind: String
    public var url: String
    public var userCode: String?
    public var expiresIn: Int64
    /// pending | done | failed | expired
    public var state: String
    public var message: String?
    public var account: String?

    public init(session: String = "", kind: String = "", url: String = "", userCode: String? = nil, expiresIn: Int64 = 0,
                state: String = "pending", message: String? = nil, account: String? = nil) {
        self.session = session; self.kind = kind; self.url = url; self.userCode = userCode; self.expiresIn = expiresIn
        self.state = state; self.message = message; self.account = account
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        session = j.str("session"); kind = j.str("kind"); url = j.str("url"); userCode = j.optStr("user_code")
        expiresIn = j.int64("expires_in"); state = j.str("state", "pending"); message = j.optStr("message"); account = j.optStr("account")
    }
}

/// DELETE /api/providers/{name}/login answer.
public struct SignOutResult: Decodable, Equatable, Sendable {
    public var ok: Bool
    public var removed: Bool
    /// A token the server's own settings hold: the app cannot take it away
    public var stillSignedIn: Bool
    /// The fallback model was on this provider and is now empty
    public var fallbackCleared: Bool

    public init(ok: Bool = false, removed: Bool = false, stillSignedIn: Bool = false, fallbackCleared: Bool = false) {
        self.ok = ok; self.removed = removed; self.stillSignedIn = stillSignedIn; self.fallbackCleared = fallbackCleared
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        ok = j.bool("ok"); removed = j.bool("removed"); stillSignedIn = j.bool("still_signed_in"); fallbackCleared = j.bool("fallback_cleared")
    }
}

/// GET /api/vault items. kind is "api", "login" or "email". Values never come back from the server.
public struct ChatVaultItem: Decodable, Equatable, Sendable {
    public var name: String
    public var label: String
    public var kind: String
    public var hosts: [String]

    public init(name: String, label: String = "", kind: String = "", hosts: [String] = []) {
        self.name = name; self.label = label; self.kind = kind; self.hosts = hosts
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = try j.require("name", j.optStr("name"))
        label = j.str("label"); kind = j.str("kind"); hosts = j.strings("hosts")
    }

    public var isLogin: Bool { kind == "login" }
}

/// GET /api/vault: `items` hold a value; `pending` are requests without one yet.
public struct ChatVaultInfo: Decodable, Equatable, Sendable {
    public var items: [ChatVaultItem]
    public var pending: [ChatVaultItem]

    public init(items: [ChatVaultItem] = [], pending: [ChatVaultItem] = []) { self.items = items; self.pending = pending }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        items = j.list("items", of: ChatVaultItem.self)
        pending = j.list("pending", of: ChatVaultItem.self)
    }

    public func find(_ name: String) -> ChatVaultItem? { (items + pending).first { $0.name == name } }
}

/// How a typed login is kept (LoginIn.mode on the server).
public enum VaultMode: String, CaseIterable, Sendable {
    case afterSuccess = "after_success", now = "now", once = "once"
}
