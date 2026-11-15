import Foundation
import KeloCore

// The AI provider and its sign-in, as the chat's model sheet and 마이 → 모델 both read them (Android shares one model page).

/// An optional admin (billing) key for this provider (server balances.ADMIN): kept in the key container, used only to read spend / balance.
public struct AdminKeyInfo: Codable, Equatable, Sendable {
    public var supported: Bool = false
    public var set: Bool = false
    public var hint: String?

    public init(supported: Bool = false, set: Bool = false, hint: String? = nil) {
        self.supported = supported
        self.set = set
        self.hint = hint
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        supported = j.bool("supported")
        set = j.bool("set")
        hint = j.optStr("hint")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(supported, forKey: AnyKey("supported"))
        try c.encode(set, forKey: AnyKey("set"))
        try c.encodeIfPresent(hint, forKey: AnyKey("hint"))
    }
}


/// One AI service from GET /api/providers: its state (ready, signed in, key, address), what it needs the owner to agree to, and
/// the thinking levels it takes.
public struct ModelProvider: Codable, Equatable, Sendable, Identifiable {
    public var name: String
    public var displayName = ""
    public var oauth = false
    public var loggedIn: Bool?
    /// true: ready; false: needs a key or a sign-in; nil: cannot be told
    public var configured: Bool?
    public var cli: CliStatus?
    public var effortLevels: [String] = []
    public var effortNote: String?
    public var takesKey = false
    /// "app" (saved in the app), "env" (the server's .env), or nil (none)
    public var keySource: String?
    public var keyHint: String?
    public var takesUrl = false
    public var baseUrl: String?
    public var userEndpoint = false
    /// Signs in with a consumer account whose maker has not confirmed it allows this use
    public var unofficial = false
    public var vendor = ""
    public var consent: ProviderConsentRecord?
    public var consentVersion = 0
    /// Signed in now through a CLI or an account sign-in (true), signed out (false), or neither / unknown (nil)
    public var signedIn: Bool?
    public var account: String?
    /// The app can sign this provider in itself (the server enables it); absent on older servers
    public var signin = false
    /// Where chats go with this provider (the company), or nil for the owner's own network (`AiConsent`)
    public var company: String?
    public var adminKey = AdminKeyInfo()

    public var id: String { name }

    /// GET /api/providers as a list (a bare array or `items`); an entry without a name is left out.
    public static func list(_ json: JSON) -> [ModelProvider] {
        (json.arrayValue ?? json["items"]?.arrayValue ?? []).compactMap { try? JSON.decode(ModelProvider.self, from: $0) }
    }

    public init(name: String, displayName: String = "", configured: Bool? = nil, effortLevels: [String] = [], effortNote: String? = nil,
                unofficial: Bool = false, vendor: String = "", consent: ProviderConsentRecord? = nil, consentVersion: Int = 0,
                signedIn: Bool? = nil, account: String? = nil, signin: Bool = false, keySource: String? = nil, userEndpoint: Bool = false,
                adminKey: AdminKeyInfo = AdminKeyInfo()) {
        self.name = name; self.displayName = displayName; self.configured = configured; self.effortLevels = effortLevels
        self.effortNote = effortNote; self.unofficial = unofficial; self.vendor = vendor; self.consent = consent
        self.consentVersion = consentVersion; self.signedIn = signedIn; self.account = account; self.signin = signin
        self.keySource = keySource; self.userEndpoint = userEndpoint; self.adminKey = adminKey
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = try j.require("name", j.optStr("name"))
        displayName = j.str("display_name", name); oauth = j.bool("oauth"); loggedIn = j.optBool("logged_in")
        configured = j.optBool("configured"); cli = j.decode("cli"); effortLevels = j.strings("effort_levels")
        effortNote = j.optStr("effort_note"); takesKey = j.bool("takes_key"); keySource = j.optStr("key_source")
        keyHint = j.optStr("key_hint"); takesUrl = j.bool("takes_url"); baseUrl = j.optStr("base_url")
        userEndpoint = j.bool("user_endpoint")
        unofficial = j.bool("unofficial"); vendor = j.str("vendor"); consent = j.decode("consent")
        consentVersion = j.int("consent_version"); signedIn = j.optBool("signed_in"); account = j.optStr("account")
        signin = j.bool("signin"); company = j.optStr("company")
        adminKey = j.decode("admin_key") ?? AdminKeyInfo()
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(name, forKey: AnyKey("name"))
        try c.encode(displayName, forKey: AnyKey("display_name"))
        try c.encode(oauth, forKey: AnyKey("oauth"))
        try c.encodeIfPresent(loggedIn, forKey: AnyKey("logged_in"))
        try c.encodeIfPresent(configured, forKey: AnyKey("configured"))
        try c.encodeIfPresent(cli, forKey: AnyKey("cli"))
        try c.encode(effortLevels, forKey: AnyKey("effort_levels"))
        try c.encodeIfPresent(effortNote, forKey: AnyKey("effort_note"))
        try c.encode(takesKey, forKey: AnyKey("takes_key"))
        try c.encodeIfPresent(keySource, forKey: AnyKey("key_source"))
        try c.encodeIfPresent(keyHint, forKey: AnyKey("key_hint"))
        try c.encode(takesUrl, forKey: AnyKey("takes_url"))
        try c.encodeIfPresent(baseUrl, forKey: AnyKey("base_url"))
        try c.encode(userEndpoint, forKey: AnyKey("user_endpoint"))
        try c.encode(unofficial, forKey: AnyKey("unofficial"))
        try c.encode(vendor, forKey: AnyKey("vendor"))
        try c.encodeIfPresent(consent, forKey: AnyKey("consent"))
        try c.encode(consentVersion, forKey: AnyKey("consent_version"))
        try c.encodeIfPresent(signedIn, forKey: AnyKey("signed_in"))
        try c.encodeIfPresent(account, forKey: AnyKey("account"))
        try c.encode(signin, forKey: AnyKey("signin"))
        try c.encodeIfPresent(company, forKey: AnyKey("company"))
        try c.encode(adminKey, forKey: AnyKey("admin_key"))
    }
}

/// POST /api/providers/{name}/login/start, GET …/login/status and POST …/login/finish answer with this.
public struct SignInSession: Decodable, Equatable, Sendable {
    public var session = ""
    /// "device_code": open `url`, type `userCode` there, the app notices by itself. "url_code": open `url`, paste back the code.
    public var kind = ""
    public var url = ""
    public var userCode: String?
    public var expiresIn: Int64 = 0
    /// pending | done | failed | expired
    public var state = "pending"
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
    public var ok = false, removed = false
    /// A token the server's own settings hold: the app cannot take it away
    public var stillSignedIn = false
    /// The fallback model was on this provider and is now empty
    public var fallbackCleared = false

    public init(stillSignedIn: Bool = false, fallbackCleared: Bool = false) {
        self.stillSignedIn = stillSignedIn; self.fallbackCleared = fallbackCleared
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        ok = j.bool("ok"); removed = j.bool("removed"); stillSignedIn = j.bool("still_signed_in"); fallbackCleared = j.bool("fallback_cleared")
    }
}

/// The background work model as set ("" = the main model) and its last failure.
public struct BackgroundModelState: Codable, Equatable, Sendable {
    public var provider = "", model = ""
    public var lastFallback: BackgroundFallback?

    public init(provider: String = "", model: String = "", lastFallback: BackgroundFallback? = nil) {
        self.provider = provider; self.model = model; self.lastFallback = lastFallback
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        provider = j.str("provider"); model = j.str("model"); lastFallback = j.decode("last_fallback")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(provider, forKey: AnyKey("provider")); try c.encode(model, forKey: AnyKey("model"))
        try c.encodeIfPresent(lastFallback, forKey: AnyKey("last_fallback"))
    }
}

/// The background model failed at `at` (epoch seconds) and the main model did the job.
public struct BackgroundFallback: Codable, Equatable, Sendable {
    public var at: Double = 0
    public var kind = "", reason = ""

    public init(at: Double = 0, kind: String = "", reason: String = "") { self.at = at; self.kind = kind; self.reason = reason }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        at = j.optDouble("at") ?? 0; kind = j.str("kind"); reason = j.str("reason")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(at, forKey: AnyKey("at")); try c.encode(kind, forKey: AnyKey("kind")); try c.encode(reason, forKey: AnyKey("reason"))
    }
}

public struct UsageTotals: Codable, Equatable, Sendable {
    public var calls: Int64
    public var inputTokens: Int64
    public var outputTokens: Int64
    public var cacheReadTokens: Int64

    public init(calls: Int64 = 0, inputTokens: Int64 = 0, outputTokens: Int64 = 0, cacheReadTokens: Int64 = 0) {
        self.calls = calls; self.inputTokens = inputTokens; self.outputTokens = outputTokens; self.cacheReadTokens = cacheReadTokens
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        calls = j.int64("calls")
        inputTokens = j.int64("input_tokens")
        outputTokens = j.int64("output_tokens")
        cacheReadTokens = j.int64("cache_read_tokens")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(calls, forKey: AnyKey("calls"))
        try c.encode(inputTokens, forKey: AnyKey("input_tokens"))
        try c.encode(outputTokens, forKey: AnyKey("output_tokens"))
        try c.encode(cacheReadTokens, forKey: AnyKey("cache_read_tokens"))
    }
}

public struct ModelUsage: Codable, Equatable, Sendable {
    public var provider: String
    public var model: String
    public var calls: Int64
    public var inputTokens: Int64
    public var outputTokens: Int64
    public var cacheReadTokens: Int64

    public init(provider: String = "", model: String = "", calls: Int64 = 0, inputTokens: Int64 = 0, outputTokens: Int64 = 0, cacheReadTokens: Int64 = 0) {
        self.provider = provider; self.model = model; self.calls = calls; self.inputTokens = inputTokens; self.outputTokens = outputTokens; self.cacheReadTokens = cacheReadTokens
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        provider = j.str("provider")
        model = j.str("model")
        calls = j.int64("calls")
        inputTokens = j.int64("input_tokens")
        outputTokens = j.int64("output_tokens")
        cacheReadTokens = j.int64("cache_read_tokens")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(provider, forKey: AnyKey("provider"))
        try c.encode(model, forKey: AnyKey("model"))
        try c.encode(calls, forKey: AnyKey("calls"))
        try c.encode(inputTokens, forKey: AnyKey("input_tokens"))
        try c.encode(outputTokens, forKey: AnyKey("output_tokens"))
        try c.encode(cacheReadTokens, forKey: AnyKey("cache_read_tokens"))
    }
}

/// GET /api/usage/summary?days=N: the background model's state, the period's sums and the same by provider and model.
public struct UsageSummary: Codable, Equatable, Sendable {
    public var background: BackgroundModelState?
    public var total: UsageTotals
    public var models: [ModelUsage]

    public init(background: BackgroundModelState? = nil, total: UsageTotals = UsageTotals(), models: [ModelUsage] = []) {
        self.background = background
        self.total = total
        self.models = models
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        background = j.decode("background")
        total = j.decode("total") ?? UsageTotals()
        models = j.list("models")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encodeIfPresent(background, forKey: AnyKey("background"))
        try c.encode(total, forKey: AnyKey("total"))
        try c.encode(models, forKey: AnyKey("models"))
    }
}

