import Foundation
import KeloCore
import KeloShared

// The My pages' small rules without UI, so they can be tested (ports of the Android objects of the same names).

/// A model as provider + id; "" + "" is none (the server's default, or no fallback).
public struct ModelChoice: Equatable, Hashable, Sendable {
    public var provider: String
    public var model: String
    public init(_ provider: String, _ model: String) { self.provider = provider; self.model = model }
    public static let none = ModelChoice("", "")
}

/// Android chat/ModelSheet.kt ModelRules: the main model and the fallback never share a provider.
public enum ModelRules {
    /// A fallback on the main model's own provider (from before that rule) is shown as empty.
    public static func initialFallback(_ p: Profile) -> ModelChoice {
        if !p.fallbackProvider.isEmpty && p.fallbackProvider == p.provider { return .none }
        return ModelChoice(p.fallbackProvider, p.fallbackModel)
    }

    /// Moving the main model onto the fallback's provider empties the fallback. Returns the new fallback and whether it was cleared.
    public static func afterMainChange(_ main: ModelChoice, _ fallback: ModelChoice) -> (ModelChoice, Bool) {
        if !main.provider.isEmpty && main.provider == fallback.provider { return (.none, true) }
        return (fallback, false)
    }

    /// A chosen provider needs a model name.
    public static func complete(_ c: ModelChoice) -> Bool { c.provider.isEmpty || !c.model.trimmingCharacters(in: .whitespaces).isEmpty }

    /// The providers the fallback may come from: any but the main one.
    public static func fallbackProviders(_ all: [MyProvider], main: String) -> [MyProvider] { all.filter { $0.name != main } }
}

/// Checks the address of an OpenAI-compatible server before it is sent (Android EndpointRules).
public enum EndpointRules {
    public enum Problem: Equatable, Sendable { case empty, spaces, scheme, host }

    public static func check(_ url: String) -> Problem? {
        let t = url.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return .empty }
        if t.contains(where: \.isWhitespace) { return .spaces }
        guard let sep = t.range(of: "://") else { return .scheme }
        let scheme = t[..<sep.lowerBound].lowercased()
        if scheme != "http" && scheme != "https" { return .scheme }
        var rest = String(t[sep.upperBound...])
        for c in ["/", "?", "#"] { if let r = rest.range(of: c) { rest = String(rest[..<r.lowerBound]) } }
        if let at = rest.range(of: "@", options: .backwards) { rest = String(rest[at.upperBound...]) }
        let host = rest.hasPrefix("[") ? rest : (rest.split(separator: ":").first.map(String.init) ?? "")
        return host.trimmingCharacters(in: .whitespaces).isEmpty ? .host : nil
    }
}

/// Which API key box a provider gets (Android KeyRules).
public enum KeyRules {
    public enum State: Equatable, Sendable { case inApp, env, none }
    public static func state(_ keySource: String?) -> State {
        switch keySource { case "app": .inApp; case "env": .env; default: .none }
    }
    /// A pasted key must be at least this long before 저장 turns on.
    public static func keyReady(_ key: String) -> Bool { key.trimmingCharacters(in: .whitespacesAndNewlines).count >= 8 }
}

/// Unofficial providers need the user's agreement once per consent version (Android ProviderConsentRules).
public enum ProviderConsentRules {
    public enum Service: Equatable, Sendable { case claudeSubscription, chatgptSubscription, antigravity, qwen, minimax, other }

    public static func needs(_ p: MyProvider) -> Bool { p.unofficial && (p.consent == nil || (p.consent?.version ?? 0) < p.consentVersion) }

    public static func group(_ p: MyProvider) -> Int { p.unofficial ? 1 : 0 }

    public static func service(_ name: String) -> Service {
        if name.hasPrefix("claude-subscription") { return .claudeSubscription }
        if name.hasPrefix("antigravity") { return .antigravity }
        if name.hasPrefix("openai-codex") { return .chatgptSubscription }
        if name.hasPrefix("qwen") { return .qwen }
        if name.hasPrefix("minimax") { return .minimax }
        return .other
    }

    private static let known: [String: String] = ["anthropic": "이", "google": "이", "openai": "가", "alibaba": "가", "minimax": "가"]

    /// The Korean subject particle after a vendor's name, as read aloud.
    public static func subjectParticle(_ vendor: String) -> String {
        let v = vendor.trimmingCharacters(in: .whitespaces)
        if let k = known[v.lowercased()] { return k }
        guard let last = v.unicodeScalars.last else { return "가" }
        if (0xAC00...0xD7A3).contains(last.value) { return (last.value - 0xAC00) % 28 != 0 ? "이" : "가" }
        return "aeiouy".contains(Character(String(last).lowercased())) ? "가" : "이"
    }

    /// The providers among the chosen main and fallback that still need the user's agreement, main first.
    public static func pending(main: String, fallback: String, providers: [MyProvider]) -> [MyProvider] {
        var seen = Set<String>()
        return [main, fallback].filter { !$0.isEmpty && seen.insert($0).inserted }
            .compactMap { n in providers.first { $0.name == n } }.filter(needs)
    }

    /// "consent_required:<provider>" in a 428 from PUT /api/profile, or nil.
    public static func requiredBy(_ e: ApiError) -> String? {
        guard e.status == 428, let r = e.detail.range(of: "consent_required:") else { return nil }
        let n = e.detail[r.upperBound...].trimmingCharacters(in: .whitespaces)
        return n.isEmpty ? nil : n
    }

    public static func askedKey(_ p: MyProvider) -> String { "consent.asked.\(p.name).\(p.consentVersion)" }
}

/// When the phone sign-in shows and what the server's answers mean (Android SignInRules). The box is offered only when the server
/// says the app may sign this provider in (`signin`), never otherwise.
public enum SignInRules {
    public enum Problem: Equatable, Sendable { case consent, already, off, failed }

    public static func offered(signin: Bool, signedIn: Bool?) -> Bool { signin && signedIn != true }
    public static func isDeviceCode(_ s: SignInSession) -> Bool { s.kind == "device_code" }
    public static func pasteReady(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return !t.isEmpty && !t.contains(where: \.isWhitespace) && t.count <= 4096
    }
    public static func finished(_ s: SignInSession) -> Bool { s.state == "done" }
    public static func ended(_ s: SignInSession) -> Bool { s.state == "failed" || s.state == "expired" }

    public static func problem(_ e: ApiError) -> Problem {
        if e.status == 428 { return .consent }
        if e.status == 409 && e.detail.hasPrefix("already_signed_in") { return .already }
        if e.status == 503 { return .off }
        return .failed
    }

    public static func readable(_ e: ApiError) -> String {
        if e.isNetwork { return "" }
        if let r = e.detail.range(of: "already_signed_in: ") { return String(e.detail[r.upperBound...]).trimmingCharacters(in: .whitespaces) }
        return e.detail.trimmingCharacters(in: .whitespaces)
    }

    /// "4:05" from seconds left.
    public static func mmss(_ seconds: Int) -> String { String(format: "%d:%02d", max(0, seconds) / 60, max(0, seconds) % 60) }
}

/// Android SignOutRules.
public enum SignOutRules {
    public enum Outcome: Equatable, Sendable { case done, doneFallbackCleared, doneEnvLeft, mainModel, failed }

    public static func offered(_ signedIn: Bool?) -> Bool { signedIn == true }
    public static func isMain(_ name: String, mainProvider: String) -> Bool { !mainProvider.isEmpty && mainProvider == name }
    public static func done(_ o: Outcome) -> Bool { o != .failed && o != .mainModel }

    public static func outcome(_ result: SignOutResult?, _ error: ApiError?) -> Outcome {
        if let e = error {
            if e.status == 409 { return .mainModel }
            if e.status == 404 { return .done }
            return .failed
        }
        if result?.stillSignedIn == true { return .doneEnvLeft }
        if result?.fallbackCleared == true { return .doneFallbackCleared }
        return .done
    }
}

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

    /// The server's per-file limit ("up to 100 MB", under the tunnel's 100 MB body limit); checked before anything is sent.
    public static let maxUpload: Int64 = 99_000_000
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

    public static func parseTime(_ text: String) -> (hour: Int, minute: Int)? {
        let parts = text.trimmingCharacters(in: .whitespaces).split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, (1...2).contains(parts[0].count), parts[1].count == 2,
              let h = Int(parts[0]), let m = Int(parts[1]), (0...23).contains(h), (0...59).contains(m) else { return nil }
        return (h, m)
    }

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

    public var loading: Bool { data == nil && !failed }
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
    case profile, agent, permissions, records, notify, model, usage, devices, vault(noLock: Bool), resources, documents, privacy
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
    public init(owner: Bool = false, isOperator: Bool = false) { self.owner = owner; self.isOperator = isOperator }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        owner = j.bool("owner"); isOperator = j.bool("operator")
    }
}
