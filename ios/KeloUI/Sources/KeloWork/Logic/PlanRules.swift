import Foundation
import KeloCore

// The schedule editor's rules (Android agent/AgentRules.kt: Repeat, CronBuilder, Freq, SchedulePlan, PlanRules, ScheduleDays).
// KeloCore's Repeat / CronBuilder predate the yearly repeat, so the editor keeps its own full copy here (PlanRepeat / PlanCron).
// A cron expression is only ever sent to the server or read from it; the screen always shows words.

extension LocalDay {
    private static let utc: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()

    var dateUTC: Date {
        var c = DateComponents(); c.year = year; c.month = month; c.day = day
        return LocalDay.utc.date(from: c)!
    }

    /// 0 = Sunday ... 6 = Saturday (as in cron).
    var cronWeekday: Int { LocalDay.utc.component(.weekday, from: dateUTC) - 1 }

    func adding(months: Int) -> LocalDay {
        let d = LocalDay.utc.date(byAdding: .month, value: months, to: dateUTC)!
        let r = LocalDay.utc.dateComponents([.year, .month, .day], from: d)
        return LocalDay(year: r.year!, month: r.month!, day: r.day!)
    }

    /// The day as a local calendar date at midnight in `zone` (for date pickers).
    func date(in zone: TimeZone = .current) -> Date {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        var c = DateComponents(); c.year = year; c.month = month; c.day = day
        return cal.date(from: c) ?? Date()
    }

    init(date: Date, zone: TimeZone = .current) { self.init(seconds: date.timeIntervalSince1970, zone: zone) }

    static func today(zone: TimeZone = .current) -> LocalDay { LocalDay(seconds: Date().timeIntervalSince1970, zone: zone) }
}

/// A moment on the local clock, to the minute (plus seconds for "is it still ahead").
struct LocalMoment: Equatable, Sendable {
    var day: LocalDay
    var hour: Int
    var minute: Int
    var second: Int = 0

    static func now(zone: TimeZone = .current) -> LocalMoment {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let c = cal.dateComponents([.year, .month, .day, .hour, .minute, .second], from: Date())
        return LocalMoment(day: LocalDay(year: c.year!, month: c.month!, day: c.day!), hour: c.hour!, minute: c.minute!, second: c.second!)
    }

    /// `day` at `hour:minute` comes strictly after this moment.
    func isBefore(_ day: LocalDay, _ hour: Int, _ minute: Int) -> Bool {
        if day != self.day { return self.day < day }
        let a = hour * 3600 + minute * 60
        let b = self.hour * 3600 + self.minute * 60 + second
        return a > b
    }
}

/// How often a schedule runs, as the picker shows it. Days of the week are 0 = Sunday ... 6 = Saturday.
enum PlanRepeat: Equatable, Sendable {
    case daily(hour: Int, minute: Int)
    case weekdays(hour: Int, minute: Int)
    case weekly(days: Set<Int>, hour: Int, minute: Int)
    case monthly(day: Int, hour: Int, minute: Int)
    case yearly(month: Int, day: Int, hour: Int, minute: Int)
    case everyHours(hours: Int, minute: Int)
    case once(date: LocalDay, hour: Int, minute: Int)
    case interval(minutes: Int)
    case custom(cron: String)
}

/// Android CronBuilder (with the yearly repeat).
enum PlanCron {
    static let pickerDays = [1, 2, 3, 4, 5, 6, 0]
    static let macros = ["@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
                         "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"]
    static let dayNames = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]

    static func fromSchedule(_ s: Schedule) -> PlanRepeat {
        let noCron = (s.cron ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if s.kind == "interval" || (noCron && s.everyMinutes != nil) { return .interval(minutes: s.everyMinutes ?? 60) }
        if s.kind == "once" || (noCron && s.runAt != nil) { return parseRunAt(s.runAt) ?? .custom(cron: s.cron ?? "") }
        return parse(s.cron)
    }

    /// "2026-11-08T09:00:00+09:00" (or without the offset), in the time written there.
    static func parseRunAt(_ text: String?) -> PlanRepeat? {
        let t = (text ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.count >= 16, let day = LocalDay(parsing: t) else { return nil }
        let chars = Array(t)
        guard chars[10] == "T" || chars[10] == " ", chars[13] == ":",
              let h = Int(String(chars[11...12])), let m = Int(String(chars[14...15])), (0...23).contains(h), (0...59).contains(m) else { return nil }
        return .once(date: day, hour: h, minute: m)
    }

    /// The hour and minute of an ISO time ("…T09:30…"), as written.
    static func timeOf(_ text: String?) -> (Int, Int)? {
        if case .once(_, let h, let m)? = parseRunAt(text) { return (h, m) }
        return nil
    }

    static func parse(_ cron: String?) -> PlanRepeat {
        let raw = (cron ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let expr = macros[raw.lowercased()] ?? raw
        let f = expr.split(whereSeparator: { $0 == " " || $0 == "\t" }).map(String.init)
        guard f.count == 5 else { return .custom(cron: raw) }
        let (mi, ho, dom, mon, dow) = (f[0], f[1], f[2], f[3], f[4])
        guard let minute = Int(mi), (0...59).contains(minute) else { return .custom(cron: raw) }
        if mon != "*" {
            guard let month = Int(mon), (1...12).contains(month), let d = Int(dom), (1...31).contains(d),
                  let h = Int(ho), (0...23).contains(h) else { return .custom(cron: raw) }
            return dow == "*" ? .yearly(month: month, day: d, hour: h, minute: minute) : .custom(cron: raw)
        }
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

    /// Whether a text is (or carries) a cron expression rather than words. Such text is never shown.
    static func looksLikeCron(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("@") || t.contains("*") { return true }
        return t.range(of: #"(^|\s)[\d,/-]+(\s+[\d*,/-]+){4}(\s|$)"#, options: .regularExpression) != nil
    }
}

/// How often a schedule repeats, as a phone calendar asks it.
enum Freq: String, CaseIterable, Sendable { case none, daily, weekdays, weekends, weekly, monthly, yearly }

/// A schedule as the simple form shows it (Android SchedulePlan).
struct SchedulePlan: Equatable, Sendable {
    var date: LocalDay
    var hour: Int
    var minute: Int
    var freq: Freq = .none
    var days: Set<Int> = []
    var until: LocalDay? = nil
}

/// A schedule's first and last day ("2026-11-08").
enum ScheduleDays {
    static func parse(_ text: String?) -> LocalDay? {
        guard let t = text, !t.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return LocalDay(parsing: t)
    }
}

/// Android PlanRules: the simple form <-> what POST/PATCH /api/schedules takes (cron | run_at, starts_on, ends_on).
enum PlanRules {
    enum Problem: Equatable, Sendable { case noDay, past, endBeforeStart }

    static func cron(_ p: SchedulePlan) -> String? {
        let hm = "\(p.minute) \(p.hour)"
        switch p.freq {
        case .none: return nil
        case .daily: return "\(hm) * * *"
        case .weekdays: return "\(hm) * * 1-5"
        case .weekends: return "\(hm) * * 0,6"
        case .weekly: return "\(hm) * * " + Set(p.days.map { $0 % 7 }).sorted().map(String.init).joined(separator: ",")
        case .monthly: return "\(hm) \(p.date.day) * *"
        case .yearly: return "\(hm) \(p.date.day) \(p.date.month) *"
        }
    }

    static func runAt(_ p: SchedulePlan) -> String { "\(p.date)T" + String(format: "%02d:%02d", p.hour, p.minute) }

    static func runsOn(_ p: SchedulePlan, _ d: LocalDay) -> Bool {
        let dow = d.cronWeekday
        switch p.freq {
        case .none: return d == p.date
        case .daily: return true
        case .weekdays: return (1...5).contains(dow)
        case .weekends: return dow == 0 || dow == 6
        case .weekly: return p.days.map { $0 % 7 }.contains(dow)
        case .monthly: return d.day == p.date.day
        case .yearly: return d.day == p.date.day && d.month == p.date.month
        }
    }

    static func firstDay(_ p: SchedulePlan, from: LocalDay, now: LocalMoment) -> LocalDay? {
        var d = from
        for _ in 0...(366 * 4) {
            if runsOn(p, d) && now.isBefore(d, p.hour, p.minute) { return d }
            d = d.adding(days: 1)
        }
        return nil
    }

    static func startsOn(_ p: SchedulePlan, now: LocalMoment) -> String {
        guard p.freq != .none, let natural = firstDay(p, from: now.day, now: now) else { return "" }
        return p.date > natural ? p.date.description : ""
    }

    static func endsOn(_ p: SchedulePlan) -> String { p.freq != .none ? (p.until?.description ?? "") : "" }

    static func problem(_ p: SchedulePlan, now: LocalMoment) -> Problem? {
        if p.freq == .weekly && p.days.isEmpty { return .noDay }
        if p.freq == .none && !now.isBefore(p.date, p.hour, p.minute) { return .past }
        if p.freq != .none, let u = p.until, u < max(p.date, now.day) { return .endBeforeStart }
        return nil
    }

    private static func base(_ s: Schedule, startsOn: String?, today: LocalDay) -> LocalDay {
        if let st = ScheduleDays.parse(startsOn), st > today { return st }
        if let next = s.nextRun, let d = LocalDay(parsing: next) { return d }
        return today
    }

    private static func nextWith(_ from: LocalDay, day: Int, month: Int? = nil) -> LocalDay {
        var d = from
        for _ in 0...(366 * 4) {
            if d.day == day && (month == nil || d.month == month) { return d }
            d = d.adding(days: 1)
        }
        return from
    }

    /// A saved schedule in the simple form, or nil when it does not fit it (every few hours, every N minutes, an unusual cron).
    static func fromSchedule(_ s: Schedule, startsOn: String?, endsOn: String?, today: LocalDay) -> SchedulePlan? {
        let until = ScheduleDays.parse(endsOn)
        let b = base(s, startsOn: startsOn, today: today)
        switch PlanCron.fromSchedule(s) {
        case .once(let d, let h, let m): return SchedulePlan(date: d, hour: h, minute: m, freq: .none)
        case .daily(let h, let m): return SchedulePlan(date: b, hour: h, minute: m, freq: .daily, until: until)
        case .weekdays(let h, let m): return SchedulePlan(date: b, hour: h, minute: m, freq: .weekdays, until: until)
        case .weekly(let days, let h, let m):
            if days == [0, 6] { return SchedulePlan(date: b, hour: h, minute: m, freq: .weekends, until: until) }
            return SchedulePlan(date: b, hour: h, minute: m, freq: .weekly, days: days, until: until)
        case .monthly(let d, let h, let m): return SchedulePlan(date: nextWith(b, day: d), hour: h, minute: m, freq: .monthly, until: until)
        case .yearly(let mo, let d, let h, let m):
            return SchedulePlan(date: nextWith(b, day: d, month: mo), hour: h, minute: m, freq: .yearly, until: until)
        default: return nil
        }
    }

    /// What "replace it" starts from for a schedule the form cannot show: every day at its next run's time (9:00 without one).
    static func replacing(_ s: Schedule, endsOn: String?, today: LocalDay) -> SchedulePlan {
        let t = PlanCron.timeOf(s.nextRun)
        return SchedulePlan(date: today, hour: t?.0 ?? 9, minute: t?.1 ?? 0, freq: .daily, until: ScheduleDays.parse(endsOn))
    }

    /// The weekdays in the order the week is shown, from `first` (0 = Sunday).
    static func weekOrder(first: Int) -> [Int] { (0..<7).map { (first + $0) % 7 } }

    static func withFreq(_ p: SchedulePlan, _ f: Freq) -> SchedulePlan {
        var q = p
        q.freq = f
        if f == .weekly && p.days.isEmpty { q.days = [p.date.cronWeekday] }
        return q
    }

    static func toggleDay(_ days: Set<Int>, _ d: Int) -> Set<Int> {
        if days.contains(d) {
            let rest = days.subtracting([d])
            return rest.isEmpty ? days : rest
        }
        return days.union([d])
    }

    static func defaultUntil(start: LocalDay, today: LocalDay) -> LocalDay { max(start, today).adding(months: 1) }

    /// The label in a small weekday circle: the short name without its dot when at most 3 letters, else the narrow one.
    static func circleLabel(short: String, narrow: String) -> String {
        var s = short
        while s.hasSuffix(".") { s.removeLast() }
        return (1...3).contains(s.count) ? s : narrow
    }

    static func asRepeat(_ p: SchedulePlan) -> PlanRepeat {
        switch p.freq {
        case .none: return .once(date: p.date, hour: p.hour, minute: p.minute)
        case .daily: return .daily(hour: p.hour, minute: p.minute)
        case .weekdays: return .weekdays(hour: p.hour, minute: p.minute)
        case .weekends: return .weekly(days: [0, 6], hour: p.hour, minute: p.minute)
        case .weekly: return .weekly(days: p.days, hour: p.hour, minute: p.minute)
        case .monthly: return .monthly(day: p.date.day, hour: p.hour, minute: p.minute)
        case .yearly: return .yearly(month: p.date.month, day: p.date.day, hour: p.hour, minute: p.minute)
        }
    }

    /// The body of POST (id nil) / PATCH /api/schedules. `plan` nil keeps a saved schedule's time (only the name and what to do change).
    /// A repeat goes as cron (+ starts_on / ends_on, window 0); a one-time run as run_at; the other kind is cleared on a change.
    static func body(id: Int64?, name: String, prompt: String, plan: SchedulePlan?, timezone: String?, now: LocalMoment) -> JSON {
        var o: [String: JSON] = [
            "name": .string(name.trimmingCharacters(in: .whitespacesAndNewlines)),
            "prompt": .string(prompt.trimmingCharacters(in: .whitespacesAndNewlines)),
        ]
        if let plan {
            if let c = cron(plan) {
                o["cron"] = .string(c)
                o["window_minutes"] = .number(0)
                o["starts_on"] = .string(startsOn(plan, now: now))
                o["ends_on"] = .string(endsOn(plan))
                if id != nil { o["run_at"] = .null; o["every_minutes"] = .null }
            } else {
                o["run_at"] = .string(runAt(plan))
                if id != nil { o["cron"] = .null; o["every_minutes"] = .null }
            }
        }
        if let tz = timezone, !tz.isEmpty { o["timezone"] = .string(tz) }
        return .object(o)
    }
}
