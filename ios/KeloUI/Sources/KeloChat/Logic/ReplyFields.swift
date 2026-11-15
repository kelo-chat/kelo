import Foundation
import KeloCore

/// The typed answer fields of a quick-reply question (Android chat/ReplyFields.kt, the same rules; the server checks the same plain
/// forms, goals.py check_answer).
///
/// A question's `kind`: choice (buttons; the default), text, number, date, time, datetime, weekdays (one or more days of the week) or
/// weekdays_time. Every kind but choice opens the system's own picker for that kind of value, whatever the question is about. An
/// answer is kept in its plain form (`check`): number "3", date "2026-11-10", time "14:30", datetime "2026-11-10 14:30", weekdays
/// "mon,wed", weekdays_time "mon,wed 14:30". The chat shows (and sends) it in the user's words (`display`); the plain forms go along
/// as "answers".
public enum ReplyFields {
    public static let choice = "choice", text = "text", number = "number", date = "date", time = "time", datetime = "datetime",
                      weekdays = "weekdays", weekdaysTime = "weekdays_time"
    static let fields: Set<String> = [text, number, date, time, datetime, weekdays, weekdaysTime]
    /// The plain day codes, Monday first (ISO), as the server writes them. Index + 2 is the Calendar weekday (1 = Sunday) mod 7.
    public static let dayCodes = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    static let answerMax = 300

    /// The question's kind; one this app does not know yet (a newer server) is a choice when it has buttons, else a text box.
    public static func kind(_ q: ReplyQuestion) -> String {
        let k = q.kind.trimmingCharacters(in: .whitespaces).lowercased()
        if k.isEmpty || k == choice { return choice }
        if fields.contains(k) { return k }
        return q.options.count >= 2 ? choice : text
    }

    public static func isField(_ q: ReplyQuestion) -> Bool { kind(q) != choice }

    /// The answer `value` in its plain form, or nil when it does not fit the question (the server's check, mirrored).
    public static func check(_ q: ReplyQuestion, _ value: String?) -> String? {
        let v = String((value ?? "").split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(answerMax))
        if v.isEmpty { return nil }
        switch kind(q) {
        case choice, text: return v
        case number:
            guard let n = Double(v.replacingOccurrences(of: ",", with: "")), n.isFinite else { return nil }
            if let lo = q.min, n < lo { return nil }
            if let hi = q.max, n > hi { return nil }
            return numberText(n)
        case date: return parseDate(v).map(datePlain)
        case time: return parseTime(v).map(timePlain)
        case datetime:
            let parts = v.replacingOccurrences(of: "T", with: " ").split(separator: " ", maxSplits: 1).map(String.init)
            guard let d = parseDate(parts[0]), parts.count == 2, let t = parseTime(parts[1]) else { return nil }
            return "\(datePlain(d)) \(timePlain(t))"
        case weekdays: return parseDays(v).map(daysPlain)
        case weekdaysTime:
            guard let at = v.lastIndex(of: " "), let d = parseDays(String(v[..<at])), let t = parseTime(String(v[v.index(after: at)...])) else { return nil }
            return "\(daysPlain(d)) \(timePlain(t))"
        default: return nil
        }
    }

    public struct YMD: Equatable, Sendable { public var year: Int, month: Int, day: Int }
    public struct HM: Equatable, Sendable { public var hour: Int, minute: Int }

    static var gregorian: Calendar { var c = Calendar(identifier: .gregorian); c.timeZone = TimeZone(identifier: "UTC")!; return c }

    public static func parseDate(_ v: String) -> YMD? {
        let p = v.trimmingCharacters(in: .whitespaces).split(separator: "-").compactMap { Int($0) }
        guard p.count == 3, v.trimmingCharacters(in: .whitespaces).count == 10 else { return nil }
        let comps = DateComponents(year: p[0], month: p[1], day: p[2])
        guard comps.isValidDate(in: gregorian) else { return nil }
        return YMD(year: p[0], month: p[1], day: p[2])
    }

    public static func parseTime(_ v: String) -> HM? {
        let s = v.trimmingCharacters(in: .whitespaces)
        guard s.range(of: #"^([01]?\d|2[0-3]):([0-5]\d)$"#, options: .regularExpression) != nil else { return nil }
        let p = s.split(separator: ":").compactMap { Int($0) }
        return HM(hour: p[0], minute: p[1])
    }

    public static func datePlain(_ d: YMD) -> String { String(format: "%04d-%02d-%02d", d.year, d.month, d.day) }
    public static func timePlain(_ t: HM) -> String { String(format: "%02d:%02d", t.hour, t.minute) }

    /// Days as indexes into `dayCodes` (0 = Monday).
    public static func parseDays(_ v: String) -> Set<Int>? {
        let words = v.split(whereSeparator: { $0 == "," || $0.isWhitespace }).map { String($0.lowercased().prefix(3)) }
        if words.isEmpty { return nil }
        var out = Set<Int>()
        for w in words { guard let i = dayCodes.firstIndex(of: w) else { return nil }; out.insert(i) }
        return out
    }

    public static func daysPlain(_ days: Set<Int>) -> String { days.sorted().map { dayCodes[$0] }.joined(separator: ",") }

    /// The seven days (indexes into `dayCodes`) in the order to show them, starting on `firstWeekday` (Calendar's: 1 = Sunday).
    public static func weekOrder(firstWeekday: Int) -> [Int] {
        let first = (firstWeekday + 5) % 7   // Calendar 1 (Sun) -> 6, 2 (Mon) -> 0
        return (0..<7).map { (first + $0) % 7 }
    }

    public static func toggle(_ days: Set<Int>, _ d: Int) -> Set<Int> { days.contains(d) ? days.subtracting([d]) : days.union([d]) }

    /// A unit written right after the number: Korean, Chinese and Japanese counters ("3개", "3個"), and "%".
    public static func closeUnit(_ unit: String) -> Bool {
        guard let c = unit.unicodeScalars.first else { return false }
        if c == "%" { return true }
        let v = c.value
        return (0xAC00...0xD7AF).contains(v) || (0x1100...0x11FF).contains(v) || (0x3130...0x318F).contains(v)   // Hangul
            || (0x4E00...0x9FFF).contains(v) || (0x3040...0x30FF).contains(v)                                     // Han, kana
    }

    /// Where a picker starts: the question's default, else nothing picked yet.
    public static func start(_ q: ReplyQuestion) -> String? { check(q, q.defaultValue) }

    /// The number range for the hint under a number box ("1", "5"), or nil without both ends.
    public static func range(_ q: ReplyQuestion) -> (String, String)? {
        guard let lo = q.min, let hi = q.max else { return nil }
        return (numberText(lo), numberText(hi))
    }

    static func numberText(_ n: Double) -> String {
        n == n.rounded() && abs(n) < 1e15 ? String(Int64(n)) : String(n)
    }

    /// The app's phrases `display` needs (agent_repeat_daily, sched_value_weekly, agent_repeat_daily_at, agent_repeat_weekly_at,
    /// reply_date_at, agent_list_separator).
    public struct Words: Sendable {
        public var daily = "Every day", weekly = "Weekly on %1$s", dailyAt = "Every day at %1$s", weeklyAt = "Every %1$s at %2$s",
                   dateAt = "%1$s at %2$s", separator = ", "
        public init(daily: String = "Every day", weekly: String = "Weekly on %1$s", dailyAt: String = "Every day at %1$s",
                    weeklyAt: String = "Every %1$s at %2$s", dateAt: String = "%1$s at %2$s", separator: String = ", ") {
            self.daily = daily; self.weekly = weekly; self.dailyAt = dailyAt; self.weeklyAt = weeklyAt; self.dateAt = dateAt; self.separator = separator
        }
    }

    /// The words an answer is shown and sent in: dates with their weekday and times as `locale` writes them, days by their short
    /// names from `firstWeekday` on.
    public static func display(_ q: ReplyQuestion, _ plain: String, locale: Locale, words: Words, firstWeekday: Int) -> String {
        func fill(_ template: String, _ args: String...) -> String {
            var out = template
            for (i, a) in args.enumerated() { out = out.replacingOccurrences(of: "%\(i + 1)$s", with: a) }
            return out.replacingOccurrences(of: "%s", with: args.first ?? "")
        }
        func dayList(_ d: Set<Int>) -> String {
            var cal = Calendar(identifier: .gregorian); cal.locale = locale
            let names = cal.shortWeekdaySymbols   // Sunday first
            return weekOrder(firstWeekday: firstWeekday).filter(d.contains).map { names[($0 + 1) % 7] }.joined(separator: words.separator)
        }
        switch kind(q) {
        case number:
            let u = q.unit.trimmingCharacters(in: .whitespaces)
            return u.isEmpty ? plain : closeUnit(u) ? "\(plain)\(u)" : "\(plain) \(u)"
        case date: return parseDate(plain).map { dateText($0, locale) } ?? plain
        case time: return parseTime(plain).map { timeText($0, locale) } ?? plain
        case datetime:
            let parts = plain.split(separator: " ", maxSplits: 1).map(String.init)
            guard parts.count == 2, let d = parseDate(parts[0]), let t = parseTime(parts[1]) else { return plain }
            return fill(words.dateAt, dateText(d, locale), timeText(t, locale))
        case weekdays:
            guard let d = parseDays(plain) else { return plain }
            return d.count == 7 ? words.daily : fill(words.weekly, dayList(d))
        case weekdaysTime:
            guard let at = plain.lastIndex(of: " "), let d = parseDays(String(plain[..<at])), let t = parseTime(String(plain[plain.index(after: at)...])) else { return plain }
            return d.count == 7 ? fill(words.dailyAt, timeText(t, locale)) : fill(words.weeklyAt, dayList(d), timeText(t, locale))
        default: return plain
        }
    }

    public static func dateValue(_ d: YMD) -> Date {
        gregorian.date(from: DateComponents(year: d.year, month: d.month, day: d.day, hour: 12)) ?? Date()
    }

    static func dateText(_ d: YMD, _ locale: Locale) -> String {
        let f = DateFormatter(); f.locale = locale; f.timeZone = TimeZone(identifier: "UTC")
        f.setLocalizedDateFormatFromTemplate("yMMMdEEE")
        return f.string(from: dateValue(d))
    }

    static func timeText(_ t: HM, _ locale: Locale) -> String {
        let f = DateFormatter(); f.locale = locale; f.timeZone = TimeZone(identifier: "UTC")
        f.dateStyle = .none; f.timeStyle = .short
        return f.string(from: gregorian.date(from: DateComponents(year: 2026, month: 1, day: 1, hour: t.hour, minute: t.minute)) ?? Date())
    }
}
