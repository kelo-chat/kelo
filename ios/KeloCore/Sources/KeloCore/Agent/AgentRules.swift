import Foundation

/// A calendar day without a time or zone ("2026-11-07"), as the server's `day` fields and the agenda use it.
public struct LocalDay: Hashable, Comparable, Sendable, CustomStringConvertible {
    public let year: Int, month: Int, day: Int

    public init(year: Int, month: Int, day: Int) { self.year = year; self.month = month; self.day = day }

    /// "2026-11-07" (only the first 10 characters are read); nil when it is not a real date.
    public init?(parsing text: String?) {
        guard let t = text?.trimmingCharacters(in: .whitespacesAndNewlines), t.count >= 10 else { return nil }
        let p = t.prefix(10).split(separator: "-", omittingEmptySubsequences: false)
        guard p.count == 3, p[0].count == 4, p[1].count == 2, p[2].count == 2,
              let y = Int(p[0]), let m = Int(p[1]), let d = Int(p[2]) else { return nil }
        var c = DateComponents(); c.year = y; c.month = m; c.day = d
        guard (1...12).contains(m), let date = LocalDay.gregorian.date(from: c),
              LocalDay.gregorian.component(.day, from: date) == d else { return nil }
        self.init(year: y, month: m, day: d)
    }

    /// The day of a moment in `zone`.
    public init(seconds: Double, zone: TimeZone = .current) {
        var cal = LocalDay.gregorian
        cal.timeZone = zone
        let c = cal.dateComponents([.year, .month, .day], from: Date(timeIntervalSince1970: seconds))
        self.init(year: c.year ?? 1970, month: c.month ?? 1, day: c.day ?? 1)
    }

    public func adding(days: Int) -> LocalDay {
        var c = DateComponents(); c.year = year; c.month = month; c.day = day
        var cal = LocalDay.gregorian
        cal.timeZone = TimeZone(identifier: "UTC")!
        let date = cal.date(byAdding: .day, value: days, to: cal.date(from: c)!)!
        let r = cal.dateComponents([.year, .month, .day], from: date)
        return LocalDay(year: r.year!, month: r.month!, day: r.day!)
    }

    public static func < (a: LocalDay, b: LocalDay) -> Bool { (a.year, a.month, a.day) < (b.year, b.month, b.day) }
    public var description: String { String(format: "%04d-%02d-%02d", year, month, day) }

    static let gregorian: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()
}

/// How a list is split into days, newest first: today, yesterday, then dates (Android agent/AgentRules.kt DayGroups).
public enum DayGroups {
    public enum Label: Hashable, Sendable { case today, yesterday, date(LocalDay), unknown }

    public struct Group<T> {
        public let label: Label
        public let items: [T]
    }

    public static func label(_ day: LocalDay?, today: LocalDay) -> Label {
        guard let day else { return .unknown }
        if day == today { return .today }
        if day == today.adding(days: -1) { return .yesterday }
        return .date(day)
    }

    /// Keeps the items' order; a day's items stay together under the first place that day appears.
    public static func group<T>(_ items: [T], today: LocalDay, dayOf: (T) -> LocalDay?) -> [Group<T>] {
        var order: [Label] = []
        var buckets: [Label: [T]] = [:]
        for item in items {
            let l = label(dayOf(item), today: today)
            if buckets[l] == nil { order.append(l) }
            buckets[l, default: []].append(item)
        }
        return order.map { Group(label: $0, items: buckets[$0]!) }
    }

    public static func parseDay(_ text: String?) -> LocalDay? { LocalDay(parsing: text) }

    /// A task's day: the server's "day", else the local date of when it started.
    public static func dayOf(_ task: AgentTask, zone: TimeZone = .current) -> LocalDay? {
        parseDay(task.day) ?? (task.startedAt > 0 ? LocalDay(seconds: task.startedAt, zone: zone) : nil)
    }
}

/// "in 8 hours" / "3 minutes ago": how far a moment is from now, in the largest unit that fits. Plurals are picked by the screen.
public enum RelTime {
    public enum Unit: Sendable { case now, minutes, hours, days }
    public struct Rel: Equatable, Sendable { public let unit: Unit; public let amount: Int64; public let future: Bool }

    public static func of(_ targetSeconds: Double, now nowSeconds: Double) -> Rel {
        let d = targetSeconds - nowSeconds
        let future = d > 0
        let s = abs(d)
        if s < 60 { return Rel(unit: .now, amount: 0, future: future) }
        let minutes = Int64((s / 60).rounded())
        if minutes < 60 { return Rel(unit: .minutes, amount: minutes, future: future) }
        let hours = Int64((s / 3600).rounded())
        if hours < 24 { return Rel(unit: .hours, amount: hours, future: future) }
        return Rel(unit: .days, amount: max(Int64((s / 86400).rounded()), 1), future: future)
    }
}

/// How often a schedule runs, as the picker shows it. Days of the week are 0 = Sunday ... 6 = Saturday (as in cron).
public enum Repeat: Equatable, Sendable {
    case daily(hour: Int, minute: Int)
    case weekdays(hour: Int, minute: Int)
    case weekly(days: Set<Int>, hour: Int, minute: Int)
    case monthly(day: Int, hour: Int, minute: Int)
    case everyHours(hours: Int, minute: Int)
    case once(date: LocalDay, hour: Int, minute: Int)
    /// Every `minutes` minutes from the first run (the server's "interval" kind).
    case interval(minutes: Int)
    /// A cron expression typed by hand (or one the picker has no words for).
    case custom(cron: String)
}

/// Turns picker choices into what POST/PATCH /api/schedules takes, and a saved schedule back into picker choices.
public enum CronBuilder {
    public static let hourSteps = [1, 2, 3, 4, 6, 8, 12]
    public static let intervalSteps = [15, 30, 45, 60, 90, 120, 180, 240, 360, 720, 1440]
    public static let minInterval = 5
    public static let maxWindow = 120
    public static let windowSteps = [0, 5, 10, 15, 30, 60]
    /// The days in the order the picker shows them: Monday first.
    public static let pickerDays = [1, 2, 3, 4, 5, 6, 0]

    public enum Problem: Sendable { case noDay, badCron, tooOften }

    /// The cron expression, or nil for a one-time run (sent as `runAt`) or an interval.
    public static func cron(_ r: Repeat) -> String? {
        switch r {
        case .daily(let h, let m): return "\(m) \(h) * * *"
        case .weekdays(let h, let m): return "\(m) \(h) * * 1-5"
        case .weekly(let days, let h, let m): return "\(m) \(h) * * " + Set(days.map { $0 % 7 }).sorted().map(String.init).joined(separator: ",")
        case .monthly(let d, let h, let m): return "\(m) \(h) \(d) * *"
        case .everyHours(let n, let m): return n <= 1 ? "\(m) * * * *" : "\(m) */\(n) * * *"
        case .custom(let c): return c.trimmingCharacters(in: .whitespacesAndNewlines)
        case .once, .interval: return nil
        }
    }

    /// A one-time run as local date-time ("2026-11-08T09:00"); the server reads it in the schedule's zone.
    public static func runAt(date: LocalDay, hour: Int, minute: Int) -> String {
        "\(date)T" + String(format: "%02d:%02d", hour, minute)
    }

    /// What is missing before the choice can be saved, or nil when it is complete.
    public static func problem(_ r: Repeat) -> Problem? {
        switch r {
        case .weekly(let days, _, _): return days.isEmpty ? .noDay : nil
        case .custom(let c):
            let t = c.trimmingCharacters(in: .whitespacesAndNewlines)
            return t.split(whereSeparator: { $0 == " " || $0 == "\t" }).count != 5 && !t.hasPrefix("@") ? .badCron : nil
        case .interval(let m): return m < minInterval ? .tooOften : nil
        default: return nil
        }
    }

    public static func fromSchedule(_ s: Schedule) -> Repeat {
        let noCron = (s.cron ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if s.kind == "interval" || (noCron && s.everyMinutes != nil) { return .interval(minutes: s.everyMinutes ?? 60) }
        if s.kind == "once" || (noCron && s.runAt != nil) { return parseRunAt(s.runAt) ?? .custom(cron: s.cron ?? "") }
        return parse(s.cron)
    }

    /// "2026-11-08T09:00:00+09:00" (or without the offset) as picker choices, in the time written there.
    public static func parseRunAt(_ text: String?) -> Repeat? {
        let t = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.count >= 16, let day = LocalDay(parsing: t) else { return nil }
        let chars = Array(t)
        guard chars[10] == "T" || chars[10] == " ", chars[13] == ":",
              let h = Int(String(chars[11...12])), let m = Int(String(chars[14...15])), (0...23).contains(h), (0...59).contains(m) else { return nil }
        return .once(date: day, hour: h, minute: m)
    }

    static let macros = ["@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"]
    static let dayNames = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

    /// The picker's choice for `cron`; anything it cannot show stays `.custom`.
    public static func parse(_ cron: String?) -> Repeat {
        let raw = (cron ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let expr = macros[raw.lowercased()] ?? raw
        let f = expr.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
        guard f.count == 5 else { return .custom(cron: raw) }
        let (mi, ho, dom, mon, dow) = (f[0], f[1], f[2], f[3], f[4])
        guard let minute = Int(mi), (0...59).contains(minute), mon == "*" else { return .custom(cron: raw) }
        if dom == "*" && dow == "*" {
            if ho == "*" { return .everyHours(hours: 1, minute: minute) }
            if ho.hasPrefix("*/") {
                guard let n = Int(ho.dropFirst(2)), (1...23).contains(n) else { return .custom(cron: raw) }
                return .everyHours(hours: n, minute: minute)
            }
        }
        guard let hour = Int(ho), (0...23).contains(hour) else { return .custom(cron: raw) }
        if dom == "*" && dow == "*" { return .daily(hour: hour, minute: minute) }
        if dow == "*" {
            guard let d = Int(dom), (1...31).contains(d) else { return .custom(cron: raw) }
            return .monthly(day: d, hour: hour, minute: minute)
        }
        if dom != "*" { return .custom(cron: raw) }
        guard let days = weekdays(dow) else { return .custom(cron: raw) }
        if days == [1, 2, 3, 4, 5] { return .weekdays(hour: hour, minute: minute) }
        if days.count == 7 { return .daily(hour: hour, minute: minute) }
        return .weekly(days: days, hour: hour, minute: minute)
    }

    /// "1-5", "mon,wed", "0,6", "7" as a set of 0...6, or nil when it is something else.
    static func weekdays(_ text: String) -> Set<Int>? {
        var out = Set<Int>()
        for part in text.lowercased().split(separator: ",", omittingEmptySubsequences: false) {
            if part.contains("/") { return nil }
            var nums: [Int] = []
            for e in part.split(separator: "-", omittingEmptySubsequences: false) {
                if let n = Int(e) { nums.append(n) } else if let i = dayNames.firstIndex(of: String(e)) { nums.append(i) } else { return nil }
            }
            if nums.contains(where: { !(0...7).contains($0) }) { return nil }
            switch nums.count {
            case 1: out.insert(nums[0] % 7)
            case 2:
                guard nums[0] <= nums[1] else { return nil }
                for d in nums[0]...nums[1] { out.insert(d % 7) }
            default: return nil
            }
        }
        return out.isEmpty ? nil : out
    }
}

/// A schedule's next run time as unix seconds: the server's own number, else its ISO time.
public enum ScheduleTimes {
    public static func nextRunSeconds(_ s: Schedule) -> Double? {
        if let ts = s.nextRunTs { return ts }
        guard let text = s.nextRun else { return nil }
        return isoSeconds(text)
    }

    /// An ISO 8601 time with an offset ("2026-11-08T09:00:00+09:00", fractions allowed); nil without an offset.
    public static func isoSeconds(_ text: String) -> Double? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        if let d = f.date(from: text) { return d.timeIntervalSince1970 }
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.date(from: text)?.timeIntervalSince1970
    }
}
