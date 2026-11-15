import Foundation
import KeloCore

// The My page's own answers (Android my/MyModels.kt and the pages' @Serializable classes), where KeloCore's generated models
// lack fields the latest server sends. All decode leniently: a missing or mistyped field takes its default.

/// GET /api/usage, with the API-key providers' balances (`accounts`).
public struct MyUsage: Codable, Equatable, Sendable {
    public var today = Tokens()
    public var total = Tokens()
    public var subscription: Subscription?
    public var accounts: [ProviderAccount] = []
    /// Token use by kind of work, today and this month (nil from an older server)
    public var jobs: JobUsage?
    /// The background work model as set, and its last failure (nil from an older server)
    public var background: BackgroundModelState?

    public init(today: Tokens = Tokens(), total: Tokens = Tokens(), subscription: Subscription? = nil, accounts: [ProviderAccount] = [],
                jobs: JobUsage? = nil, background: BackgroundModelState? = nil) {
        self.today = today; self.total = total; self.subscription = subscription; self.accounts = accounts
        self.jobs = jobs; self.background = background
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        today = j.decode("today") ?? Tokens()
        total = j.decode("total") ?? Tokens()
        subscription = j.decode("subscription")
        accounts = j.list("accounts")
        jobs = j.decode("jobs")
        background = j.decode("background")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(today, forKey: AnyKey("today"))
        try c.encode(total, forKey: AnyKey("total"))
        try c.encodeIfPresent(subscription, forKey: AnyKey("subscription"))
        try c.encode(accounts, forKey: AnyKey("accounts"))
        try c.encodeIfPresent(jobs, forKey: AnyKey("jobs"))
        try c.encodeIfPresent(background, forKey: AnyKey("background"))
    }
}

/// GET /api/usage "jobs" (Android my/MyModels.kt JobUsage).
public struct JobUsage: Codable, Equatable, Sendable {
    public var today = UsagePart()
    public var month = UsagePart()

    public init(today: UsagePart = UsagePart(), month: UsagePart = UsagePart()) { self.today = today; self.month = month }

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

/// GET /api/usage/summary (only the part the app reads).
public struct UsageSummary: Codable, Equatable, Sendable {
    public var background: BackgroundModelState?

    public init(background: BackgroundModelState? = nil) { self.background = background }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        background = j.decode("background")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encodeIfPresent(background, forKey: AnyKey("background"))
    }
}

/// Balance and spend of an API-key provider that reports them; the others come with `error` set.
public struct ProviderAccount: Codable, Equatable, Sendable {
    public var provider = "", displayName = "", currency = ""
    public var balance, usedToday, usedWeek, usedMonth, limit, limitRemaining: Double?
    public var error: String?

    public init(provider: String = "", displayName: String = "", currency: String = "", balance: Double? = nil, error: String? = nil) {
        self.provider = provider; self.displayName = displayName; self.currency = currency; self.balance = balance; self.error = error
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        provider = j.str("provider"); displayName = j.str("display_name"); currency = j.str("currency")
        balance = j.optDouble("balance"); usedToday = j.optDouble("used_today"); usedWeek = j.optDouble("used_week")
        usedMonth = j.optDouble("used_month"); limit = j.optDouble("limit"); limitRemaining = j.optDouble("limit_remaining")
        error = j.optStr("error")
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: AnyKey.self)
        try c.encode(provider, forKey: AnyKey("provider")); try c.encode(displayName, forKey: AnyKey("display_name"))
        try c.encode(currency, forKey: AnyKey("currency"))
        for (k, v) in [("balance", balance), ("used_today", usedToday), ("used_week", usedWeek), ("used_month", usedMonth),
                       ("limit", limit), ("limit_remaining", limitRemaining)] { try c.encodeIfPresent(v, forKey: AnyKey(k)) }
        try c.encodeIfPresent(error, forKey: AnyKey("error"))
    }
}

/// GET /api/uploads items: a file the user uploaded. `kind` is document | image | video | audio.
public struct MyUpload: Decodable, Equatable, Hashable, Sendable, Identifiable {
    public var path: String
    public var bytes: Int64 = 0
    public var modified: Double = 0
    public var kind = "document"
    public var type = ""
    public var duration: Double?
    public var width: Int?
    public var height: Int?

    public var id: String { path }
    public var name: String { path.replacingOccurrences(of: "uploads/", with: "") }

    /// GET /api/uploads as a list; an entry without a path is left out instead of failing the whole page.
    public static func list(_ json: JSON) -> [MyUpload] { (json.arrayValue ?? []).compactMap { try? JSON.decode(MyUpload.self, from: $0) } }
    public var isMedia: Bool { kind == "image" || kind == "video" }

    public init(path: String, bytes: Int64 = 0, modified: Double = 0, kind: String = "document", type: String = "", duration: Double? = nil) {
        self.path = path; self.bytes = bytes; self.modified = modified; self.kind = kind; self.type = type; self.duration = duration
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        path = try j.require("path", j.optStr("path"))
        bytes = j.int64("bytes"); modified = j.double("modified"); kind = j.str("kind", MediaKind.of(path)); type = j.str("type")
        duration = j.optDouble("duration"); width = j.optInt("width"); height = j.optInt("height")
    }
}

/// GET /api/providers items, with what the model page, the vault's AI key page and the sign-in need.
public struct MyProvider: Decodable, Equatable, Sendable, Identifiable {
    public var name: String
    public var displayName = ""
    public var oauth = false
    public var loggedIn: Bool?
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

    public var id: String { name }

    /// GET /api/providers as a list; an entry without a name is left out.
    public static func list(_ json: JSON) -> [MyProvider] { (json.arrayValue ?? []).compactMap { try? JSON.decode(MyProvider.self, from: $0) } }

    public init(name: String, displayName: String = "", configured: Bool? = nil, unofficial: Bool = false, consentVersion: Int = 0,
                consent: ProviderConsentRecord? = nil, signedIn: Bool? = nil, signin: Bool = false, keySource: String? = nil) {
        self.name = name; self.displayName = displayName; self.configured = configured; self.unofficial = unofficial
        self.consentVersion = consentVersion; self.consent = consent; self.signedIn = signedIn; self.signin = signin
        self.keySource = keySource
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        name = try j.require("name", j.optStr("name"))
        displayName = j.str("display_name", name); oauth = j.bool("oauth"); loggedIn = j.optBool("logged_in")
        configured = j.optBool("configured"); cli = j.decode("cli"); effortLevels = j.strings("effort_levels")
        effortNote = j.optStr("effort_note"); takesKey = j.bool("takes_key"); keySource = j.optStr("key_source")
        keyHint = j.optStr("key_hint"); takesUrl = j.bool("takes_url"); baseUrl = j.optStr("base_url")
        unofficial = j.bool("unofficial"); vendor = j.str("vendor"); consent = j.decode("consent")
        consentVersion = j.int("consent_version"); signedIn = j.optBool("signed_in"); account = j.optStr("account")
        signin = j.bool("signin"); company = j.optStr("company")
    }
}

/// What the My home page shows; each part arrives on its own and keeps its last value when its call fails. Kept (encrypted) for
/// the next open.
public struct MyOverview: Codable, Equatable, Sendable {
    public var usage: MyUsage?
    public var devices: Int?
    public var creds: [VaultEntry] = []
    public var locked = false
    public var documents: Int?
    public var sizes: String?
    /// The storage warning (StorageRules.key: warn | critical | host_low); nil when there is none
    public var storage: String?
    /// How full the storage cap is (percent), for the 80% line; nil without a cap
    public var storagePercent: Int?
    public var version: String?
    public var walletUnseen: Int?
    public var cards: Int?
    /// Signed in with this server's own access key (an operator GET answered); nil = not known yet.
    public var `operator`: Bool?
    /// GET /api/server-update for the owner (state ok or local); nil = nothing to show (another device, an older server, unavailable).
    public var serverUpdate: ServerUpdateStatus?

    public init() {}

    /// The refresh did not reach the server: every part failed, at least one for want of a connection.
    public static func offline(_ failures: [Error?]) -> Bool {
        !failures.isEmpty && failures.allSatisfy { $0 != nil } && failures.contains { e in
            guard let a = e as? ApiError else { return true }
            return a.isNetwork
        }
    }
}

/// The last pass of the proactive messages. status: done | nothing_new | skipped | failed
public struct ProactivePass: Decodable, Equatable, Sendable {
    public var at: Double?
    public var status = "", reason = ""
    public var sent = 0
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        at = j.optDouble("at"); status = j.str("status"); reason = j.str("reason"); sent = j.int("sent")
    }
}

/// GET /api/proactive: messages the agent may send by itself (off by default).
public struct ProactiveSettings: Decodable, Equatable, Sendable {
    public var enabled = false
    public var wakeStart = "07:00", wakeEnd = "22:00"
    public var everyMinutes = 120, dailyRegular = 2, dailyTotal = 3, dailyNow = 3
    public var today: [String: Int] = [:]
    public var lastPass: ProactivePass?
    public var awake = false
    /// Follow the hours the owner really uses Kelo without asking (off by default).
    public var autoHours = false
    /// default | manual | learned: "learned" when the hours came from a suggestion (마이 → 기록) or from autoHours.
    public var hoursSource = "default"
    /// The same answer's message hours: weekday and weekend rows, suggestions, learned windows (`ContactHours`).
    public var hours = ContactHours()

    public init() {}

    public init(from decoder: Decoder) throws {
        hours = try ContactHours(from: decoder)
        let j = try Lenient(decoder)
        enabled = j.bool("enabled"); wakeStart = j.str("wake_start", "07:00"); wakeEnd = j.str("wake_end", "22:00")
        everyMinutes = j.int("every_minutes", 120); dailyRegular = j.int("daily_regular", 2); dailyTotal = j.int("daily_total", 3)
        dailyNow = j.int("daily_now", 3)
        today = (j["today"]?.objectValue ?? [:]).compactMapValues { $0.intValue }
        lastPass = j.decode("last_pass"); awake = j.bool("awake")
        autoHours = j.bool("auto_hours"); hoursSource = j.str("hours_source", "default")
    }
}

/// GET /api/checklist: the things to keep an eye on.
public struct ChecklistSettings: Decodable, Equatable, Sendable {
    public var text = ""
    public var items = 0
    public var list: [String] = []
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        text = j.str("text"); items = j.int("items"); list = j.strings("list")
    }
}

/// One nightly look-back note (GET /api/reflection notes).
public struct LookbackNote: Decodable, Equatable, Sendable {
    public var date = "", text = ""
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        date = j.str("date"); text = j.str("text")
    }
}

/// GET /api/reflection with the fields the records page reads (`available`, `notes`).
public struct MyLookback: Decodable, Equatable, Sendable {
    public var available = true
    public var enabled = false
    public var rules: [LearnedRule] = []
    public var lastRun: LookbackRun?
    public var notes: [LookbackNote] = []
    /// The last memory pass that changed something (nil from an older server, or before the first one).
    public var memoryPass: MemoryPass?

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        available = j.bool("available", true); enabled = j.bool("enabled"); rules = j.list("rules")
        lastRun = j.decode("last_run"); notes = j.list("notes")
        memoryPass = j.decode("memory", as: MemoryUpkeep.self)?.lastPass
    }
}

/// The last memory pass that changed something: lines it added and tidied (removed, folded or rewritten), whether it was undone and
/// can still be. `files` are the server's own file names: never shown, only grouped by MemoryArea. Android agent/AgentModels.kt.
public struct MemoryPass: Decodable, Equatable, Sendable {
    public var at: Double?
    public var status = "", code = "", trigger = ""
    public var added = 0, tidied = 0
    public var files: [String] = []
    public var undone = false, canUndo = false

    public init(status: String = "", added: Int = 0, tidied: Int = 0, undone: Bool = false, canUndo: Bool = false) {
        self.status = status; self.added = added; self.tidied = tidied; self.undone = undone; self.canUndo = canUndo
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        at = j.optDouble("at"); status = j.str("status"); code = j.str("code"); trigger = j.str("trigger")
        added = j.int("added"); tidied = j.int("tidied"); files = j.strings("files")
        undone = j.bool("undone"); canUndo = j.bool("can_undo")
    }
}

public struct MemoryUpkeep: Decodable, Equatable, Sendable {
    public var lastPass: MemoryPass?
    public init(from decoder: Decoder) throws { lastPass = try Lenient(decoder).decode("last_pass") }
}

/// The lines one file gained and lost in the last pass (GET /api/reflection/memory).
public struct MemoryChange: Decodable, Equatable, Sendable {
    public var file = ""
    public var added: [String] = [], removed: [String] = []
    public init(file: String, added: [String] = [], removed: [String] = []) { self.file = file; self.added = added; self.removed = removed }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        file = j.str("file"); added = j.strings("added"); removed = j.strings("removed")
    }
}

public struct MemoryChanges: Decodable, Equatable, Sendable {
    public var lastPass: MemoryPass?
    public var changes: [MemoryChange] = []
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        lastPass = j.decode("last_pass"); changes = j.list("changes")
    }
}

/// What a memory file is about, in the user's words (the app never names the files themselves).
public enum MemoryArea: CaseIterable, Sendable { case aboutYou, toRemember, people, workingNotes }

public struct MemoryAreaChange: Equatable, Sendable {
    public var area: MemoryArea
    public var added: [String], removed: [String]
}

/// The memory pass's small rules (Android agent/AgentModels.kt MemoryPassRules).
public enum MemoryPassRules {
    /// The pass line on the Records page: a pass that changed something, or one the user undid.
    public static func shown(_ pass: MemoryPass?) -> Bool {
        guard let p = pass, p.status == "done" else { return false }
        return p.undone || p.added > 0 || p.tidied > 0
    }

    public static func area(_ file: String) -> MemoryArea {
        if file == "user.md" { return .aboutYou }
        if file == "AGENTS.md" { return .workingNotes }
        if file.hasPrefix("memory/people/") { return .people }
        return .toRemember
    }

    /// The changes grouped by area, in the order the sheet shows them; areas without a changed line are left out.
    public static func group(_ changes: [MemoryChange]) -> [MemoryAreaChange] {
        MemoryArea.allCases.compactMap { a in
            let mine = changes.filter { area($0.file) == a }
            let added = mine.flatMap(\.added).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            let removed = mine.flatMap(\.removed).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            return added.isEmpty && removed.isEmpty ? nil : MemoryAreaChange(area: a, added: added, removed: removed)
        }
    }
}

/// GET /api/operator/firebase: the operator's push key, never the key itself.
public struct FirebaseConfig: Decodable, Equatable, Sendable {
    public var enabled = false
    public var projectId = "", clientEmail = ""
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        enabled = j.bool("enabled"); projectId = j.str("project_id"); clientEmail = j.str("client_email")
    }
}

/// GET /api/operator/cloudflare (the API token is only "stored or not").
public struct CloudflareConfig: Decodable, Equatable, Sendable {
    public var accountId = "", zoneId = "", zoneName = ""
    public var hasToken = false
    public var maxNames = 0, used = 0
    public var enabled = false
    public var idleDays = 30
    public init() {}
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        accountId = j.str("account_id"); zoneId = j.str("zone_id"); zoneName = j.str("zone_name"); hasToken = j.bool("has_token")
        maxNames = j.int("max_names"); used = j.int("used"); enabled = j.bool("enabled"); idleDays = j.int("idle_days", 30)
    }
}

public struct Reclaimed: Decodable, Sendable {
    public var released: [String] = []
    public init(from decoder: Decoder) throws { released = try Lenient(decoder).strings("released") }
}

/// One report a copy of Kelo sent (GET /api/operator/reports).
public struct ReceivedReport: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64 = 0
    public var createdAt: Double = 0
    public var reason = "", comment = "", reply = "", asked = "", provider = "", model = "", appVersion = ""
    public var handled = false
    public init(provider: String = "", model: String = "") { self.provider = provider; self.model = model }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.int64("id"); createdAt = j.double("created_at"); reason = j.str("reason"); comment = j.str("comment")
        reply = j.str("reply"); asked = j.str("asked"); provider = j.str("provider"); model = j.str("model")
        appVersion = j.str("app_version"); handled = j.bool("handled")
    }
}

public struct ReceivedReports: Decodable, Equatable, Sendable {
    public var reports: [ReceivedReport] = []
    public var open = 0
    public var keepDays = 365
    public var receiving = false
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        reports = j.list("reports"); open = j.int("open"); keepDays = j.int("keep_days", 365); receiving = j.bool("receiving")
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

    public init(session: String = "", kind: String = "", state: String = "pending") {
        self.session = session; self.kind = kind; self.state = state
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        session = j.str("session"); kind = j.str("kind"); url = j.str("url"); userCode = j.optStr("user_code")
        expiresIn = j.int64("expires_in"); state = j.str("state", "pending"); message = j.optStr("message"); account = j.optStr("account")
    }
}

/// DELETE /api/providers/{name}/login answer.
public struct SignOutResult: Decodable, Equatable, Sendable {
    public var ok = false, removed = false, stillSignedIn = false, fallbackCleared = false
    public init(stillSignedIn: Bool = false, fallbackCleared: Bool = false) {
        self.stillSignedIn = stillSignedIn; self.fallbackCleared = fallbackCleared
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        ok = j.bool("ok"); removed = j.bool("removed"); stillSignedIn = j.bool("still_signed_in"); fallbackCleared = j.bool("fallback_cleared")
    }
}

/// One request to try Google sign-in, as GET /api/operator/beta lists it (Android my/OperatorBeta.kt BetaRequestRow).
public struct BetaRequestRow: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64 = 0
    public var createdAt: Double = 0
    public var email = ""
    public var products: [String] = []
    /// requested | approved | declined
    public var status = "requested"
    /// the email to support when it came in: "sent", "no_mailbox", "failed" or "" (still going)
    public var notice = ""
    public init(id: Int64 = 0, email: String = "", products: [String] = [], status: String = "requested") {
        self.id = id; self.email = email; self.products = products; self.status = status
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.int64("id"); createdAt = j.double("created_at"); email = j.str("email"); products = j.strings("products")
        status = j.str("status", "requested"); notice = j.str("notice")
    }
}

public struct BetaRequests: Decodable, Equatable, Sendable {
    public var requests: [BetaRequestRow] = []
    public var open = 0
    public var mailFrom = ""
    public var receiving = false
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        requests = j.list("requests"); open = j.int("open"); mailFrom = j.str("mail_from"); receiving = j.bool("receiving")
    }
}

public struct BetaAnswer: Decodable, Equatable, Sendable {
    public var mailed = false
    public var mailError = ""
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        mailed = j.bool("mailed"); mailError = j.str("mail_error")
    }
}

public enum OperatorBetaRules {
    /// "Gmail, Google Calendar" (product names stay as they are in every language).
    public static func products(_ ids: [String]) -> String {
        ids.compactMap { ["gmail": "Gmail", "calendar": "Google Calendar"][$0] }.joined(separator: ", ")
    }
    /// Approved / Decline show only while a request waits; an approved one can still be taken back with Decline.
    public static func canApprove(_ r: BetaRequestRow) -> Bool { r.status == "requested" }
    public static func canDecline(_ r: BetaRequestRow) -> Bool { r.status != "declined" }
}
