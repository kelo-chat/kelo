import Foundation
import KeloCore
import KeloShared

// The My page's own answers (Android my/MyModels.kt and the pages' @Serializable classes), where KeloCore's generated models
// lack fields the latest server sends. All decode leniently: a missing or mistyped field takes its default.

/// GET /api/uploads items: a file the user uploaded. `kind` is document | image | video | audio.
public struct MyUpload: Decodable, Equatable, Hashable, Sendable, Identifiable {
    public var path: String
    public var bytes: Int64 = 0
    public var modified: Double = 0
    public var kind = "document"
    public var type = ""
    public var duration: Double?

    public var id: String { path }
    public var name: String { path.replacingOccurrences(of: "uploads/", with: "") }

    /// GET /api/uploads as a list; an entry without a path is left out instead of failing the whole page.
    public static func list(_ json: JSON) -> [MyUpload] { (json.arrayValue ?? []).compactMap { try? JSON.decode(MyUpload.self, from: $0) } }
    public var isMedia: Bool { kind == "image" || kind == "video" }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        path = try j.require("path", j.optStr("path"))
        bytes = j.int64("bytes"); modified = j.double("modified"); kind = j.str("kind", MediaKind.of(path)); type = j.str("type")
        duration = j.optDouble("duration")
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

/// GET /api/proactive: messages the agent may send by itself (off by default).
public struct ProactiveSettings: Decodable, Equatable, Sendable {
    public var enabled = false
    public var wakeStart = "07:00", wakeEnd = "22:00"
    public var everyMinutes = 120, dailyRegular = 2, dailyTotal = 3, dailyNow = 3
    public var mailCheck = true
    public var today: [String: Int] = [:]
    public var awake = false
    /// Follow the hours the owner really uses Kelo without asking (off by default).
    public var autoHours = false
    /// default | manual | learned: "learned" when the hours came from a suggestion (마이 → 기록) or from autoHours.
    public var hoursSource = "default"
    /// The same answer's message hours: weekday and weekend rows, suggestions, learned windows (`ContactHours`).
    public var hours = ContactHours()
    /// Messages Kelo starts a day (0...10; 0 = only what cannot wait), how many went out today, and how many items it is holding:
    /// nil from an older server (the rows are then hidden).
    public var dailyDeliveries: Int?
    public var todayDeliveries: Int?
    public var held: Int?
    /// The hourly situation look is on ("research"): the "What Kelo looked at" row needs it.
    public var research = false

    public init() {}

    public init(from decoder: Decoder) throws {
        hours = try ContactHours(from: decoder)
        let j = try Lenient(decoder)
        enabled = j.bool("enabled"); wakeStart = j.str("wake_start", "07:00"); wakeEnd = j.str("wake_end", "22:00")
        everyMinutes = j.int("every_minutes", 120); dailyRegular = j.int("daily_regular", 2); dailyTotal = j.int("daily_total", 3)
        dailyNow = j.int("daily_now", 3)
        mailCheck = j.bool("mail_check", true)
        today = (j["today"]?.objectValue ?? [:]).compactMapValues { $0.intValue }
        awake = j.bool("awake")
        autoHours = j.bool("auto_hours"); hoursSource = j.str("hours_source", "default")
        dailyDeliveries = j.optInt("daily_deliveries"); todayDeliveries = j.optInt("today_deliveries"); held = j.optInt("held")
        research = j.bool("research")
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
    public var status = "", code = "", trigger = ""
    public var added = 0, tidied = 0
    public var undone = false, canUndo = false

    public init(status: String = "", added: Int = 0, tidied: Int = 0, undone: Bool = false, canUndo: Bool = false) {
        self.status = status; self.added = added; self.tidied = tidied; self.undone = undone; self.canUndo = canUndo
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        status = j.str("status"); code = j.str("code"); trigger = j.str("trigger")
        added = j.int("added"); tidied = j.int("tidied")
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

/// One request to try Google sign-in, as GET /api/operator/beta lists it (Android my/OperatorBeta.kt BetaRequestRow).
public struct BetaRequestRow: Decodable, Equatable, Sendable, Identifiable {
    public var id: Int64 = 0
    public var createdAt: Double = 0
    public var email = ""
    public var products: [String] = []
    /// "google" (a request to try Google sign-in) or "play_tester" (a kelo.chat/tester sign-up for the Play closed test)
    public var kind = "google"
    /// the phone model, for a Play tester
    public var appVersion = ""
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
        kind = j.str("kind", "google"); appVersion = j.str("app_version")
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
        ids.compactMap { ["gmail": "Gmail", "calendar": "Google Calendar", "play_test": "Google Play 비공개 테스트"][$0] }.joined(separator: ", ")
    }
    /// Approved / Decline show only while a request waits; an approved one can still be taken back with Decline.
    public static func canApprove(_ r: BetaRequestRow) -> Bool { r.status == "requested" }
    public static func canDecline(_ r: BetaRequestRow) -> Bool { r.status != "declined" }
}
