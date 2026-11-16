import Foundation
import KeloCore

// What 마이 → 사용량 reads from GET /api/usage and /api/usage/summary; the model page reads the background model's state from it too.

/// GET /api/usage, with the API-key providers' balances (`accounts`).
public struct MyUsage: Codable, Equatable, Sendable {
    public var today = Tokens()
    public var total = Tokens()
    public var subscription: Subscription?
    public var subscriptions: [Subscription] = []
    public var accounts: [ProviderAccount] = []
    /// Token use by kind of work, today and this month (nil from an older server)
    public var jobs: JobUsage?
    /// The background work model as set, and its last failure (nil from an older server)
    public var background: BackgroundModelState?

    public init(today: Tokens = Tokens(), total: Tokens = Tokens(), subscription: Subscription? = nil, subscriptions: [Subscription] = [],
                accounts: [ProviderAccount] = [], jobs: JobUsage? = nil, background: BackgroundModelState? = nil) {
        self.today = today; self.total = total; self.subscription = subscription; self.subscriptions = subscriptions; self.accounts = accounts
        self.jobs = jobs; self.background = background
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        today = j.decode("today") ?? Tokens()
        total = j.decode("total") ?? Tokens()
        subscription = j.decode("subscription")
        subscriptions = j.list("subscriptions")
        accounts = j.list("accounts")
        jobs = j.decode("jobs")
        background = j.decode("background")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(today, forKey: AnyKey("today"))
        try c.encode(total, forKey: AnyKey("total"))
        try c.encodeIfPresent(subscription, forKey: AnyKey("subscription"))
        try c.encode(subscriptions, forKey: AnyKey("subscriptions"))
        try c.encode(accounts, forKey: AnyKey("accounts"))
        try c.encodeIfPresent(jobs, forKey: AnyKey("jobs"))
        try c.encodeIfPresent(background, forKey: AnyKey("background"))
    }
}

/// GET /api/usage "jobs" (Android my/MyModels.kt JobUsage).
public struct JobUsage: Codable, Equatable, Sendable {
    public var today = UsagePart()
    public var month = UsagePart()

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        today = j.decode("today") ?? UsagePart()
        month = j.decode("month") ?? UsagePart()
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(today, forKey: AnyKey("today")); try c.encode(month, forKey: AnyKey("month"))
    }
}

/// The model calls since `since`, summed, and by kind of work (the largest first).
public struct UsagePart: Codable, Equatable, Sendable {
    public var since = ""
    public var calls: Int64 = 0, inputTokens: Int64 = 0, outputTokens: Int64 = 0, cacheReadTokens: Int64 = 0, cacheWriteTokens: Int64 = 0
    public var kinds: [KindUsage] = []

    public init(kinds: [KindUsage] = [], inputTokens: Int64 = 0, outputTokens: Int64 = 0, cacheReadTokens: Int64 = 0) {
        self.kinds = kinds; self.inputTokens = inputTokens; self.outputTokens = outputTokens; self.cacheReadTokens = cacheReadTokens
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        since = j.str("since"); calls = j.int64("calls"); inputTokens = j.int64("input_tokens"); outputTokens = j.int64("output_tokens")
        cacheReadTokens = j.int64("cache_read_tokens"); cacheWriteTokens = j.int64("cache_write_tokens"); kinds = j.list("kinds")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(since, forKey: AnyKey("since")); try c.encode(calls, forKey: AnyKey("calls"))
        try c.encode(inputTokens, forKey: AnyKey("input_tokens")); try c.encode(outputTokens, forKey: AnyKey("output_tokens"))
        try c.encode(cacheReadTokens, forKey: AnyKey("cache_read_tokens")); try c.encode(cacheWriteTokens, forKey: AnyKey("cache_write_tokens"))
        try c.encode(kinds, forKey: AnyKey("kinds"))
    }
}

/// One kind of work: chat, schedule, checklist, task, worker, reflection, memory, journal, proactive, compaction, followup, probe, other.
public struct KindUsage: Codable, Equatable, Sendable {
    public var kind = ""
    public var calls: Int64 = 0, inputTokens: Int64 = 0, outputTokens: Int64 = 0, cacheReadTokens: Int64 = 0, cacheWriteTokens: Int64 = 0

    public init(kind: String, inputTokens: Int64 = 0, outputTokens: Int64 = 0, cacheReadTokens: Int64 = 0) {
        self.kind = kind; self.inputTokens = inputTokens; self.outputTokens = outputTokens; self.cacheReadTokens = cacheReadTokens
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        kind = j.str("kind"); calls = j.int64("calls"); inputTokens = j.int64("input_tokens"); outputTokens = j.int64("output_tokens")
        cacheReadTokens = j.int64("cache_read_tokens"); cacheWriteTokens = j.int64("cache_write_tokens")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(kind, forKey: AnyKey("kind")); try c.encode(calls, forKey: AnyKey("calls"))
        try c.encode(inputTokens, forKey: AnyKey("input_tokens")); try c.encode(outputTokens, forKey: AnyKey("output_tokens"))
        try c.encode(cacheReadTokens, forKey: AnyKey("cache_read_tokens")); try c.encode(cacheWriteTokens, forKey: AnyKey("cache_write_tokens"))
    }
}

/// Balance and spend of an API-key provider that reports them; the others come with `error` set.
public struct ProviderAccount: Codable, Equatable, Sendable {
    public var provider = "", role = "", displayName = "", currency = ""
    public var balance, usedToday, usedWeek, usedMonth, limit, limitRemaining: Double?
    public var error: String?
    public var admin: Bool = false
    public var adminError: String?

    public init(provider: String = "", role: String = "", displayName: String = "", currency: String = "",
                balance: Double? = nil, usedToday: Double? = nil, usedWeek: Double? = nil, usedMonth: Double? = nil,
                limit: Double? = nil, limitRemaining: Double? = nil, error: String? = nil,
                admin: Bool = false, adminError: String? = nil) {
        self.provider = provider; self.role = role; self.displayName = displayName; self.currency = currency
        self.balance = balance; self.usedToday = usedToday; self.usedWeek = usedWeek; self.usedMonth = usedMonth
        self.limit = limit; self.limitRemaining = limitRemaining; self.error = error
        self.admin = admin; self.adminError = adminError
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        provider = j.str("provider"); role = j.str("role"); displayName = j.str("display_name"); currency = j.str("currency")
        balance = j.optDouble("balance"); usedToday = j.optDouble("used_today"); usedWeek = j.optDouble("used_week")
        usedMonth = j.optDouble("used_month"); limit = j.optDouble("limit"); limitRemaining = j.optDouble("limit_remaining")
        error = j.optStr("error")
        admin = j.bool("admin")
        adminError = j.optStr("admin_error")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(provider, forKey: AnyKey("provider")); try c.encode(role, forKey: AnyKey("role"))
        try c.encode(displayName, forKey: AnyKey("display_name"))
        try c.encode(currency, forKey: AnyKey("currency"))
        for (k, v) in [("balance", balance), ("used_today", usedToday), ("used_week", usedWeek), ("used_month", usedMonth),
                       ("limit", limit), ("limit_remaining", limitRemaining)] { try c.encodeIfPresent(v, forKey: AnyKey(k)) }
        try c.encodeIfPresent(error, forKey: AnyKey("error"))
        try c.encode(admin, forKey: AnyKey("admin"))
        try c.encodeIfPresent(adminError, forKey: AnyKey("admin_error"))
    }
}

