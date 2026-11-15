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
    /// The periods the usage card offers, in days; `defaultDays` is chosen first.
    public static let periods = [1, 7, 30]
    public static let defaultDays = 1

    /// Tokens read fresh: the whole prompt minus what the provider read from its cache (never below 0).
    public static func fresh(_ input: Int64, _ cacheRead: Int64) -> Int64 { max(input - cacheRead, 0) }

    /// The big number: fresh input and the output.
    public static func used(_ input: Int64, _ output: Int64, _ cacheRead: Int64) -> Int64 { fresh(input, cacheRead) + output }

    /// One legend row: a model (or, with `others` > 0, the rest together) with its tokens and its share of the total in %.
    public struct ModelShare: Equatable, Sendable {
        public let provider: String
        public let model: String
        public let tokens: Int64
        public let percent: Int
        public let others: Int

        public init(provider: String, model: String, tokens: Int64, percent: Int, others: Int = 0) {
            self.provider = provider; self.model = model; self.tokens = tokens; self.percent = percent; self.others = others
        }
    }

    /// The legend: by tokens, largest first; with 6 or more models the top `top` and one "N others" row. Empty when nothing was used.
    public static func modelShares(_ models: [ModelUsage], top: Int = 4) -> [ModelShare] {
        let rows = models.map { ModelShare(provider: $0.provider, model: $0.model, tokens: used($0.inputTokens, $0.outputTokens, $0.cacheReadTokens), percent: 0) }
            .filter { $0.tokens > 0 }.sorted { $0.tokens > $1.tokens }
        let total = rows.reduce(0) { $0 + $1.tokens }
        if total <= 0 { return [] }
        let shown: [ModelShare]
        if rows.count > top + 1 {
            let topRows = Array(rows.prefix(top))
            let othersTokens = rows.dropFirst(top).reduce(0) { $0 + $1.tokens }
            shown = topRows + [ModelShare(provider: "", model: "", tokens: othersTokens, percent: 0, others: rows.count - top)]
        } else {
            shown = rows
        }
        return shown.map {
            ModelShare(provider: $0.provider, model: $0.model, tokens: $0.tokens, percent: Int(((Double($0.tokens) * 100.0) / Double(total)).rounded()), others: $0.others)
        }
    }

    /**
     * A token count in a few characters: Korean/Japanese/Chinese count by 만/万 (10^4) and 억/億 (10^8), others by K / M / B; one decimal
     * below 10 of the unit ("2.9M", "299만", "1.2억"), whole numbers under 10,000 as they are ("8,512").
     */
    public static func compactTokens(_ n: Int64, _ language: String) -> String {
        func one(_ v: Double) -> String {
            if v < 10 {
                let s = String(format: "%.1f", locale: Locale(identifier: "en_US_POSIX"), v)
                return s.hasSuffix(".0") ? String(s.dropLast(2)) : s
            } else {
                return String(Int64(v.rounded()))
            }
        }
        if n < 10_000 {
            let nf = NumberFormatter()
            nf.locale = Locale(identifier: "en_US_POSIX")
            nf.numberStyle = .decimal
            nf.usesGroupingSeparator = true
            nf.groupingSeparator = ","
            return nf.string(from: NSNumber(value: n)) ?? String(n)
        }
        switch language {
        case "ko":
            return n >= 100_000_000 ? one(Double(n) / 1e8) + "억" : one(Double(n) / 1e4) + "만"
        case "ja", "zh":
            return n >= 100_000_000 ? one(Double(n) / 1e8) + "億" : one(Double(n) / 1e4) + "万"
        default:
            if n >= 1_000_000_000 {
                return one(Double(n) / 1e9) + "B"
            } else if n >= 1_000_000 {
                return one(Double(n) / 1e6) + "M"
            } else {
                return one(Double(n) / 1e3) + "K"
            }
        }
    }

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

    /// The background model's last failure, shown only while that model is still the one chosen on the page.
    public static func fallbackShown(_ state: BackgroundModelState?, provider: String, model: String) -> BackgroundFallback? {
        guard let state, !provider.isEmpty, state.provider == provider, state.model == model else { return nil }
        return state.lastFallback
    }

    /// The limits section: the current model's service first (its subscription, else its account), then the others that report something.
    public struct LimitParts: Equatable, Sendable {
        public let current: Subscription?
        public let currentAccount: ProviderAccount?
        public let others: [Subscription]
        public let otherAccounts: [ProviderAccount]

        public var otherCount: Int { others.count + otherAccounts.count }

        public init(current: Subscription?, currentAccount: ProviderAccount?, others: [Subscription], otherAccounts: [ProviderAccount]) {
            self.current = current; self.currentAccount = currentAccount; self.others = others; self.otherAccounts = otherAccounts
        }
    }

    public static func limitParts(_ u: MyUsage?) -> LimitParts {
        guard let u else { return LimitParts(current: nil, currentAccount: nil, others: [], otherAccounts: []) }
        let rawSubs: [Subscription]
        if u.subscriptions.isEmpty, let s = u.subscription {
            var mainSub = s
            mainSub.role = "main"
            rawSubs = [mainSub]
        } else {
            rawSubs = u.subscriptions
        }
        let subs = rawSubs.filter { $0.error != nil || !$0.windows.isEmpty }
        let accounts = u.accounts.filter { $0.error == nil }
        let currentIdx = subs.firstIndex(where: { $0.role == "main" })
        let current = currentIdx.map { subs[$0] }
        let currentAcctIdx = (current == nil) ? accounts.firstIndex(where: { $0.role == "main" || $0.role.isEmpty }) : nil
        let currentAccount = currentAcctIdx.map { accounts[$0] }
        let others = subs.enumerated().filter { $0.offset != currentIdx }.map(\.element)
        let otherAccounts = accounts.enumerated().filter { $0.offset != currentAcctIdx }.map(\.element)
        return LimitParts(current: current, currentAccount: currentAccount, others: others, otherAccounts: otherAccounts)
    }
}
