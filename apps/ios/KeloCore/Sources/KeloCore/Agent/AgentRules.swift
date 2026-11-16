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
