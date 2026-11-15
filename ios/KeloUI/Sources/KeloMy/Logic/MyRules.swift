import Foundation
import KeloCore
import KeloShared

// The My pages' small rules without UI, so they can be tested (ports of the Android objects of the same names).

/// What a file is, by its name (Android MediaKind).
public enum MediaKind {
    private static let image: Set<String> = ["jpg", "jpeg", "png", "webp", "heic", "heif"]
    private static let video: Set<String> = ["mp4", "m4v", "mov", "webm", "3gp", "mkv"]
    private static let audio: Set<String> = ["mp3", "m4a", "aac", "wav", "ogg", "oga", "opus", "flac", "amr"]

    public static func of(_ name: String) -> String {
        guard let dot = name.lastIndex(of: ".") else { return "document" }
        let ext = name[name.index(after: dot)...].lowercased()
        if image.contains(ext) { return "image" }
        if video.contains(ext) { return "video" }
        if audio.contains(ext) { return "audio" }
        return "document"
    }

    /// A file name for a pick that has none with a suffix ("IMG_0042" + video/mp4 -> "IMG_0042.mp4").
    public static func withSuffix(_ name: String, mime: String?) -> String {
        if let dot = name.lastIndex(of: "."), name.index(after: dot) < name.endIndex { return name }
        let ext: String
        switch (mime ?? "").lowercased() {
        case "image/jpeg": ext = "jpg"
        case "image/png": ext = "png"
        case "image/webp": ext = "webp"
        case "image/heic": ext = "heic"
        case "image/heif": ext = "heif"
        case "video/mp4": ext = "mp4"
        case "video/quicktime": ext = "mov"
        case "video/webm": ext = "webm"
        case "audio/mpeg": ext = "mp3"
        case "audio/mp4", "audio/x-m4a", "audio/m4a": ext = "m4a"
        case "audio/aac": ext = "aac"
        case "audio/wav", "audio/x-wav": ext = "wav"
        case "application/pdf": ext = "pdf"
        default: return name
        }
        return "\(name).\(ext)"
    }
}

/// The operator page's checks and request bodies (Android OperatorRules). Nothing here keeps a secret.
public struct CloudflareForm: Equatable, Sendable {
    public var token = "", accountId = "", zoneId = "", zoneName = "", maxNames = ""
    public init(token: String = "", accountId: String = "", zoneId: String = "", zoneName: String = "", maxNames: String = "") {
        self.token = token; self.accountId = accountId; self.zoneId = zoneId; self.zoneName = zoneName; self.maxNames = maxNames
    }
}

public enum OperatorRules {
    public static let maxKeyFile = 20_000
    public enum Problem: Equatable, Sendable { case token, account, zoneId, zone, max }

    private static func matches(_ s: String, _ pattern: String) -> Bool { s.range(of: pattern, options: .regularExpression) != nil }
    public static func idOk(_ s: String) -> Bool { s.isEmpty || matches(s.trimmingCharacters(in: .whitespaces).lowercased(), "^[0-9a-f]{32}$") }
    public static func zoneOk(_ s: String) -> Bool { s.isEmpty || matches(s.trimmingCharacters(in: .whitespaces).lowercased(), #"^([a-z0-9-]+\.)+[a-z]{2,63}$"#) }
    public static func tokenOk(_ s: String) -> Bool { s.isEmpty || matches(s.trimmingCharacters(in: .whitespaces), "^[A-Za-z0-9_-]{20,200}$") }
    public static func maxOk(_ s: String) -> Bool {
        let t = s.trimmingCharacters(in: .whitespaces)
        return t.isEmpty || (1...100_000).contains(Int(t) ?? 0)
    }

    public static func problem(_ f: CloudflareForm) -> Problem? {
        if !tokenOk(f.token) { return .token }
        if !idOk(f.accountId) { return .account }
        if !idOk(f.zoneId) { return .zoneId }
        if !zoneOk(f.zoneName) { return .zone }
        if !maxOk(f.maxNames) { return .max }
        return nil
    }

    /// PUT /api/operator/cloudflare: only what is filled in; an empty token keeps the stored one.
    public static func cloudflareBody(_ f: CloudflareForm) -> JSON {
        var o: [String: JSON] = [:]
        func t(_ s: String) -> String { s.trimmingCharacters(in: .whitespaces) }
        if !t(f.token).isEmpty { o["api_token"] = .string(t(f.token)) }
        if !t(f.accountId).isEmpty { o["account_id"] = .string(t(f.accountId).lowercased()) }
        if !t(f.zoneId).isEmpty { o["zone_id"] = .string(t(f.zoneId).lowercased()) }
        if !t(f.zoneName).isEmpty { o["zone_name"] = .string(t(f.zoneName).lowercased()) }
        if let n = Int(t(f.maxNames)) { o["max_names"] = .number(Double(n)) }
        return .object(o)
    }

    public static func cloudflareOff() -> JSON { ["api_token": ""] }

    public static func formOf(_ c: CloudflareConfig) -> CloudflareForm {
        CloudflareForm(accountId: c.accountId, zoneId: c.zoneId, zoneName: c.zoneName, maxNames: c.maxNames > 0 ? String(c.maxNames) : "")
    }

    /// A picked file that can be a service-account key: small, and a JSON object. Its content is never shown.
    public static func keyFileOk(_ bytes: Data) -> Bool {
        guard !bytes.isEmpty, bytes.count <= maxKeyFile, let v = try? JSON.parse(bytes) else { return false }
        return v.objectValue != nil
    }

    /// An operator GET answered (nil status) = the owner's own token; 403 = a paired phone; anything else unknown.
    public static func ownerFrom(_ status: Int?) -> Bool? {
        switch status { case nil: true; case 403: false; default: nil }
    }

    /// GET /api/operator/status answered (nil status: its `operator`), 403 = not the owner, 404 = an older server without it
    /// (then `fallback`: the old /api/operator/firebase check); anything else unknown (nil keeps what was shown).
    public static func operatorFrom(_ status: Int?, _ isOperator: Bool?, fallback: () -> Bool? = { nil }) -> Bool? {
        switch status {
        case nil: isOperator == true
        case 403: false
        case 404: fallback()
        default: nil
        }
    }

    public static func reportsPath(showHandled: Bool) -> String { showHandled ? "/api/operator/reports" : "/api/operator/reports?handled=false" }

    public static func modelName(_ r: ReceivedReport) -> String? {
        let s = [r.provider, r.model].filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }.joined(separator: " / ")
        return s.isEmpty ? nil : s
    }
}

/// The notification settings' small rules (Android NotifySettingsRules).
public enum NotifySettingsRules {
    public static let maxItems = 30, maxItem = 300, maxRegular = 10, maxTotal = 20, maxNow = 10

    public static func parseTime(_ text: String) -> (hour: Int, minute: Int)? { ContactHoursRules.minutes(text).map { ($0 / 60, $0 % 60) } }

    public static func formatTime(_ hour: Int, _ minute: Int) -> String { String(format: "%02d:%02d", hour, minute) }

    /// How often the 새 소식 알림 pass looks, as (in hours, the number): whole hours as hours, else minutes.
    public static func every(_ minutes: Int) -> (hours: Bool, n: Int) {
        minutes >= 60 && minutes % 60 == 0 ? (true, minutes / 60) : (false, max(1, minutes))
    }

    /// The one "at most N a day" the page shows, as the server's three caps (daily_regular, daily_total, daily_now).
    public static func perDay(_ n: Int, regular: Int, now: Int) -> (regular: Int, total: Int, now: Int) {
        let t = min(max(n, 1), maxTotal)
        return (min(max(regular, 0), min(t, maxRegular)), t, min(max(now, 0), min(t, maxNow)))
    }

    /// The list after adding `item` (trimmed, one line; nothing when blank, already there or the list is full).
    public static func added(_ list: [String], _ item: String) -> [String] {
        let clean = String(item.split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(maxItem))
        if clean.isEmpty || list.contains(clean) || list.count >= maxItems { return list }
        return list + [clean]
    }
}

/// The follow-up delay pickers (Android FollowupRules FIRST/SECOND_CHOICES).
public enum FollowupDelayRules {
    public static let firstChoices = [5, 15, 30, 60, 120, 240]
    public static let secondChoices = [30, 60, 120, 180, 360, 720, 1440]
    public static func delays(_ first: Int, _ second: Int) -> (Int, Int) { let f = max(first, 1); return (f, max(second, f)) }
    public static func choices(_ usual: [Int], current: Int) -> [Int] { Array(Set((usual + [current]).filter { $0 > 0 })).sorted() }
}

/// The resources page's ceilings: the limit that was set, else what the server machine has (never just "no limit").
public enum ResourceRules {
    public static func ramCeiling(_ r: Resources) -> Int64? { r.memory.limit ?? r.host.memory }
    public static func cpuCeiling(_ r: Resources) -> Double? { r.cpus ?? r.host.cpus }
    public static func diskCeiling(_ r: Resources) -> Int64? { r.disk.limit ?? r.host.diskFree.map { r.disk.used + $0 } }
    public static func diskFull(_ r: Resources) -> Bool { r.disk.limit.map { Double(r.disk.used) >= Double($0) * 0.9 } ?? false }
    public static func fraction(_ used: Int64, of: Int64?) -> Double? {
        guard let of, of > 0 else { return nil }
        return min(max(Double(used) / Double(of), 0), 1)
    }
}

/// What the resources page shows (Android ResourcesLoad): the last numbers read, or before any arrived, that it is still loading or
/// that the read failed (then an error with "try again", never a blank page). A refresh that fails after a success keeps the numbers.
public struct ResourcesLoad: Equatable, Sendable {
    public var data: Resources?
    public var failed: Bool

    public init(data: Resources? = nil, failed: Bool = false) {
        self.data = data
        self.failed = failed
    }

    public var showError: Bool { data == nil && failed }

    public func loaded(_ r: Resources) -> ResourcesLoad { ResourcesLoad(data: r, failed: false) }
    public func readFailed() -> ResourcesLoad { data != nil ? self : ResourcesLoad(data: nil, failed: true) }
    public func retrying() -> ResourcesLoad { ResourcesLoad(data: data, failed: false) }
}

/// Weeks run Monday to Sunday; the journal is fetched by month, so a week may need two (Android DigestRules).
public enum DigestRules {
    public static func startOf(_ day: LocalDay) -> LocalDay {
        let d = date(day)
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let wd = cal.component(.weekday, from: d)   // 1 = Sunday
        let back = (wd + 5) % 7                     // days since Monday
        return day.adding(days: -back)
    }

    public static func months(_ start: LocalDay) -> [String] {
        let end = start.adding(days: 6)
        let a = String(format: "%04d-%02d", start.year, start.month)
        let b = String(format: "%04d-%02d", end.year, end.month)
        return a == b ? [a] : [a, b]
    }

    public static func canGoNext(_ start: LocalDay, today: LocalDay) -> Bool { start < startOf(today) }

    /// The entries inside the week, newest first, without repeats.
    public static func entries(_ start: LocalDay, _ all: [JournalEntry]) -> [JournalEntry] {
        let from = start.description, to = start.adding(days: 6).description
        var seen = Set<Int64>()
        return all.filter { e in
            let d = String(e.date.prefix(10))
            return d >= from && d <= to && seen.insert(e.id).inserted
        }.sorted { $0.date > $1.date }
    }

    public static func gaps(_ start: LocalDay, _ all: [JournalGap]) -> [JournalGap] {
        let from = start.description, to = start.adding(days: 6).description
        var seen = Set<String>()
        return all.filter { g in
            let d = String(g.date.prefix(10))
            return d >= from && d <= to && seen.insert(d).inserted
        }
    }

    /// How many entries each day of the week has, Monday first.
    public static func perDay(_ start: LocalDay, _ entries: [JournalEntry]) -> [Int] {
        (0...6).map { i in let d = start.adding(days: i).description; return entries.filter { $0.date.hasPrefix(d) }.count }
    }

    private static func date(_ d: LocalDay) -> Date {
        var c = DateComponents(); c.year = d.year; c.month = d.month; c.day = d.day; c.hour = 12
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        return cal.date(from: c) ?? Date()
    }
}

/// The My page's sub page stack and how a request from elsewhere maps to it.
public enum MyRoute: Hashable, Sendable {
    case profile, agent, permissions, records, notify, model, usage, devices, vault(noLock: Bool), resources, documents, privacy, files
    /// 마이 → 모델 showing one CLI's box first (a newer-CLI notice; the provider id)
    case modelCli(String)
    case language, wallet, `operator`
    /// 마이 → 서버 업데이트 (owner only)
    case serverUpdate
    /// 마이 → Kelo 서버 자원 → 정리하기 (what can safely be removed; owner only)
    case cleanup
    case vaultItem(String), vaultAi(String), vaultAddLogin, vaultLog
    case document(String)
    /// 마이 → 보안 → Keyguard (`code`: opened at the recovery code)
    case keyguard(code: Bool)

    public static func of(_ r: MyPageRequest) -> MyRoute? {
        switch r {
        case .wallet, .walletAdd: .wallet
        case .agent: .agent
        case .model: .model
        case .notifications: .notify
        case .vault: nil   // the vault always goes through the phone's own check first
        case .devices: .devices
        case .permissions: .permissions
        case .documents: .documents
        case .usage: .usage
        case .language: .language
        case .operator: .operator
        case .serverUpdate: .serverUpdate
        case .resources: .resources
        case .cleanup: .cleanup
        case .keyguard: .keyguard(code: false)
        case .keyguardCode: .keyguard(code: true)
        @unknown default: nil
        }
    }
}

/// GET /api/operator/status (owner only): {"owner": true, "operator": bool}; the Operator section shows only when `operator`.
public struct OperatorStatus: Decodable, Equatable, Sendable {
    public var owner = false
    public var isOperator = false
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        owner = j.bool("owner"); isOperator = j.bool("operator")
    }
}
