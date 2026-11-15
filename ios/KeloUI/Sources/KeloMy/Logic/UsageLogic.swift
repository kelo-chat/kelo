import Foundation
import KeloCore

/// One block of limits for the models it covers. `name` is already put into words.
public struct UsageGroup: Equatable, Sendable {
    public let group: String?
    public let name: String
    public let active: Bool
    public let windows: [UsageWindow]
}

/// When a limit resets: the server's own text, or a time and how far off it is.
public enum ResetInfo: Equatable, Sendable {
    case text(String)
    case at(epochSeconds: Double)
}

/// The usage rules without the UI (Android my/UsageLogic.kt); wording comes in from the strings.
public enum UsageLogic {
    /// The English labels the CLIs report, in the order they are replaced; the UI pairs each with its translation.
    public static let labelSources = ["Current session", "Current week", "all models", "Weekly Limit Remaining", "Five Hour Limit Remaining"]

    public static func label(_ s: String, _ replacements: [(String, String)]) -> String {
        replacements.reduce(s) { acc, pair in
            guard let r = acc.range(of: pair.0) else { return acc }
            return acc.replacingCharacters(in: r, with: pair.1)
        }
    }

    /// "Gemini Models" -> "Gemini 모델", "Claude and GPT models" -> "Claude · GPT 모델".
    public static func groupName(_ g: String, models: (String) -> String, joiner: String) -> String {
        var named = g
        if let r = g.range(of: #"\s+models?$"#, options: [.regularExpression, .caseInsensitive]) {
            named = models(String(g[..<r.lowerBound]))
        }
        return named.replacingOccurrences(of: #"\s+and\s+"#, with: joiner, options: [.regularExpression, .caseInsensitive])
    }

    private static let stop: Set<String> = ["model", "models", "and", "the", "of"]

    /// The limit group that covers the model in use, or nil when that cannot be told.
    public static func activeGroup(_ groups: [String], model: String) -> String? {
        let m = model.lowercased()
        let hits = groups.filter { g in
            g.lowercased().split(whereSeparator: { !($0.isLetter || $0.isNumber) || !$0.isASCII }).map(String.init)
                .contains { w in w.count > 1 && !stop.contains(w) && m.contains(w) }
        }
        return hits.count == 1 ? hits[0] : nil
    }

    /// The limit windows split by the models they cover; the group of the model in use comes first.
    public static func groups(_ u: MyUsage?, model: String, name: (String) -> String) -> [UsageGroup] {
        guard let sub = u?.subscription, sub.error == nil else { return [] }
        var names: [String?] = []
        for w in sub.windows where !names.contains(where: { $0 == w.group }) { names.append(w.group) }
        let active = activeGroup(names.compactMap { $0 }.filter { !$0.isEmpty }, model: model)
        let all = names.map { g in
            UsageGroup(group: g, name: (g?.isEmpty == false) ? name(g!) : "", active: g != nil && g == active,
                       windows: sub.windows.filter { $0.group == g })
        }
        return all.filter(\.active) + all.filter { !$0.active }
    }

    public static func oneLine(_ groups: [UsageGroup], _ g: UsageGroup, full: Bool) -> Bool {
        !full && !g.active && groups.contains(where: \.active)
    }

    /// The parsed reset time (ISO first, else the CLI's words), or the server's text when nothing can be read.
    public static func reset(_ w: UsageWindow, now: Double, zone: TimeZone = .current) -> ResetInfo? {
        if let iso = w.resetsAt, let t = parseTime(iso, zone: zone) { return .at(epochSeconds: t) }
        if let text = w.resetsText, let t = parseResetText(text, now: now) { return .at(epochSeconds: t) }
        return w.resetsText.map { .text($0) }
    }

    public static let nearMinutes = 12 * 60

    public enum Phrase: Equatable, Sendable {
        case soon, inMinutes(Int), inHours(Int), on(Double), raw(String)
    }

    public static func phrase(_ r: ResetInfo, now: Double) -> Phrase {
        switch r {
        case .text(let t): return .raw(t)
        case .at(let at):
            let mins = Int(((at - now) / 60).rounded())
            if mins <= 1 { return .soon }
            if mins < 60 { return .inMinutes(mins) }
            if mins < nearMinutes { return .inHours(Int((Double(mins) / 60).rounded())) }
            return .on(at)
        }
    }

    private static let months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

    private static func firstMatch(_ pattern: String, in s: String, options: NSRegularExpression.Options = []) -> [String]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: options),
              let m = re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) else { return nil }
        return (0..<m.numberOfRanges).map { i in
            let r = m.range(at: i)
            return r.location == NSNotFound ? "" : (Range(r, in: s).map { String(s[$0]) } ?? "")
        }
    }

    /// A reset time from a CLI's own words, in the zone it names (UTC otherwise): "Nov 7, 11:40am (UTC)", "11:40am", "in 3 hours".
    public static func parseResetText(_ text: String, now: Double) -> Double? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let m = firstMatch(#"\bin\s+(\d+)\s*(minute|min|hour|hr|h|day|d)s?\b"#, in: t, options: .caseInsensitive), let n = Double(m[1]) {
            let u = m[2].lowercased()
            let mins = u.hasPrefix("m") ? n : (u.hasPrefix("h") ? n * 60 : n * 1440)
            return now + mins * 60
        }
        var zone = TimeZone(identifier: "UTC")!
        var body = t
        if let m = firstMatch(#"\(([^)]+)\)\s*$"#, in: t) {
            let z = m[1].trimmingCharacters(in: .whitespaces)
            if z.uppercased() == "UTC" || z.uppercased() == "GMT" { zone = TimeZone(identifier: "UTC")! }
            else if let tz = TimeZone(identifier: z) { zone = tz }
            if let r = t.range(of: #"\(([^)]+)\)\s*$"#, options: .regularExpression) { body = String(t[..<r.lowerBound]).trimmingCharacters(in: .whitespaces) }
        }
        var hm: (Int, Int)?
        if let m = firstMatch(#"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\.?\b"#, in: body, options: .caseInsensitive), let h = Int(m[1]) {
            hm = (h % 12 + (m[3].lowercased() == "p" ? 12 : 0), Int(m[2]) ?? 0)
        } else if let m = firstMatch(#"\b(\d{1,2}):(\d{2})\b"#, in: body), let h = Int(m[1]), let mi = Int(m[2]) {
            hm = (h, mi)
        }
        guard let (h, mi) = hm, (0...23).contains(h), (0...59).contains(mi) else { return nil }
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let nowDate = Date(timeIntervalSince1970: now)
        let today = cal.dateComponents([.year, .month, .day], from: nowDate)
        var day = today
        var hasDate = false
        if let m = firstMatch(#"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?"#, in: body),
           let mon = months.firstIndex(of: m[1].lowercased()), let d = Int(m[2]) {
            var c = DateComponents(); c.year = Int(m[3]) ?? today.year; c.month = mon + 1; c.day = d
            if let date = cal.date(from: c), cal.component(.day, from: date) == d {
                hasDate = true
                day = c
                if m[3].isEmpty, let twoBack = cal.date(byAdding: .day, value: -2, to: cal.startOfDay(for: nowDate)), date < twoBack {
                    day.year = (day.year ?? 0) + 1
                }
            }
        }
        day.hour = h; day.minute = mi; day.second = 0
        guard var at = cal.date(from: day) else { return nil }
        if !hasDate && at < nowDate { at = cal.date(byAdding: .day, value: 1, to: at) ?? at }
        return at.timeIntervalSince1970
    }

    /// ISO 8601 with an offset or "Z"; a time without one is read in `zone`.
    public static func parseTime(_ s: String, zone: TimeZone) -> Double? {
        let t = s.trimmingCharacters(in: .whitespaces)
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: t) { return d.timeIntervalSince1970 }
        f.formatOptions = [.withInternetDateTime]
        if let d = f.date(from: t) { return d.timeIntervalSince1970 }
        let local = DateFormatter()
        local.locale = Locale(identifier: "en_US_POSIX")
        local.timeZone = zone
        for p in ["yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd'T'HH:mm"] {
            local.dateFormat = p
            if let d = local.date(from: t) { return d.timeIntervalSince1970 }
        }
        return nil
    }

    /// The one number for a settings row: the most used window of the group in use (else of the first group), or nil.
    public static func summaryPercent(_ groups: [UsageGroup]) -> Int? {
        guard let g = groups.first(where: \.active) ?? groups.first, let m = g.windows.map(\.usedPercent).max() else { return nil }
        return min(max(m, 0), 100)
    }

    public static func barFraction(_ used: Int) -> Double { Double(min(max(used, 0), 100)) / 100 }
    public static func isHot(_ used: Int) -> Bool { used >= 90 }
    public static func left(_ used: Int) -> Int { max(0, 100 - used) }

    /// The kinds of work the server counts tokens for, in the order a tie is listed; anything else is "other".
    public static let jobKinds = ["chat", "schedule", "checklist", "task", "worker", "reflection", "memory", "journal", "proactive",
                                  "compaction", "followup", "probe", "other"]

    public static func jobKind(_ kind: String) -> String { jobKinds.contains(kind) ? kind : "other" }

    /// The cached share of the input in whole percent (at least 1 when any was read), or nil when none was reported (no "0%").
    public static func cachePercent(_ cacheRead: Int64, _ input: Int64) -> Int? {
        guard cacheRead > 0, input > 0 else { return nil }
        return min(max(Int((Double(cacheRead) * 100 / Double(input)).rounded()), 1), 100)
    }

    /// The rows of one period: each kind of work that used tokens, the largest first (unknown kinds summed into "other").
    public static func jobRows(_ part: UsagePart?) -> [JobRow] {
        guard let part else { return [] }
        var order: [String] = []
        var sums: [String: (Int64, Int64, Int64)] = [:]
        for k in part.kinds {
            let kind = jobKind(k.kind)
            if sums[kind] == nil { order.append(kind) }
            let s = sums[kind] ?? (0, 0, 0)
            sums[kind] = (s.0 + k.inputTokens, s.1 + k.outputTokens, s.2 + k.cacheReadTokens)
        }
        return order.compactMap { kind -> JobRow? in
            let s = sums[kind]!
            return s.0 + s.1 > 0 ? JobRow(kind: kind, tokens: s.0 + s.1, cachePercent: cachePercent(s.2, s.0)) : nil
        }.sorted { a, b in
            a.tokens != b.tokens ? a.tokens > b.tokens : (jobKinds.firstIndex(of: a.kind) ?? 99) < (jobKinds.firstIndex(of: b.kind) ?? 99)
        }
    }

    public static func partTokens(_ part: UsagePart?) -> Int64 { part.map { $0.inputTokens + $0.outputTokens } ?? 0 }

    /// The background model's last failure, shown only while that model is still the one chosen on the page.
    public static func fallbackShown(_ state: BackgroundModelState?, provider: String, model: String) -> BackgroundFallback? {
        guard let state, !provider.isEmpty, state.provider == provider, state.model == model else { return nil }
        return state.lastFallback
    }
}

/// One kind of work in the usage page: its tokens (input + output) and the cached share of its input, when reported.
public struct JobRow: Equatable, Sendable {
    public let kind: String
    public let tokens: Int64
    public let cachePercent: Int?

    public init(kind: String, tokens: Int64, cachePercent: Int?) { self.kind = kind; self.tokens = tokens; self.cachePercent = cachePercent }
}
