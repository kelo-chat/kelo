import Foundation

/// The small time line above a message that starts a new stretch of the conversation, as messaging apps show it (Android TimeLabels).
///
/// A message gets one when it is the first message shown, the first of a new calendar day (device time zone), or comes `gapMinutes`
/// or more after the message before it. Nothing in a running back-and-forth.
///
/// The words: today "오늘 오후 10:00", yesterday "어제 오전 9:31", earlier this year "11월 1일 오후 9:11", any earlier year (last year
/// too) "2024년 10월 19일 오전 9:22". The patterns come from the locale (`Patterns.of`); only the choice of pattern and the
/// "Today" / "Yesterday" words are decided here.
public enum TimeLabels {
    /// A message this long after the one before it starts a new stretch (and gets a time line).
    public static let gapMinutes: Int64 = 30

    public enum Day: Equatable, Sendable { case today, yesterday, thisYear, earlier }

    /// Locale patterns: `time` alone ("a h:mm" in Korean), `monthDayTime` ("MMM d일 a h:mm"), `fullDateTime` ("y년 MMM d일 a h:mm").
    public struct Patterns: Equatable, Sendable {
        public var time: String, monthDayTime: String, fullDateTime: String
        public init(time: String, monthDayTime: String, fullDateTime: String) {
            self.time = time; self.monthDayTime = monthDayTime; self.fullDateTime = fullDateTime
        }

        /// The patterns the locale's formatter gives for the templates jmm / MMMdjmm / yMMMdjmm. Apple joins date and time with a
        /// word ("Nov 1 at 9:11 PM"); like Android (and a chat's time line) they are joined with the plain ", " instead.
        public static func of(_ locale: Locale) -> Patterns {
            func p(_ t: String) -> String { DateFormatter.dateFormat(fromTemplate: t, options: 0, locale: locale) ?? t }
            let time = p("jmm")
            return Patterns(time: time, monthDayTime: plainJoin(p("MMMdjmm"), time: time), fullDateTime: plainJoin(p("yMMMdjmm"), time: time))
        }

        /// "MMM d 'at' h:mm a" -> "MMM d, h:mm a": a quoted word right before the time part becomes ", ".
        static func plainJoin(_ pattern: String, time: String) -> String {
            guard pattern.hasSuffix(time), pattern.count > time.count else { return pattern }
            let head = String(pattern.dropLast(time.count))
            guard let r = head.range(of: #"\s*'[^']*'\s*$"#, options: .regularExpression), r.lowerBound > head.startIndex else { return pattern }
            return String(head[..<r.lowerBound]) + ", " + time
        }
    }

    static func calendar(_ zone: TimeZone) -> Calendar {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = zone
        return c
    }

    static func date(_ ms: Int64) -> Date { Date(timeIntervalSince1970: Double(ms) / 1000) }

    /// Which calendar day `atMs` is, seen from `nowMs` in `zone`. A time a little in the future (clocks differ) counts as today.
    public static func day(_ atMs: Int64, now nowMs: Int64, zone: TimeZone) -> Day {
        let cal = calendar(zone)
        let at = cal.startOfDay(for: date(atMs)), today = cal.startOfDay(for: date(nowMs))
        let days = cal.dateComponents([.day], from: today, to: at).day ?? 0
        if days == 0 || (days == 1 && atMs - nowMs < 60 * 60_000) { return .today }
        if days == -1 { return .yesterday }
        return cal.component(.year, from: at) == cal.component(.year, from: today) ? .thisYear : .earlier
    }

    /// Whether a message at `atMs` starts a new stretch after one at `prevMs` (nil: it is the first message shown).
    public static func startsStretch(prev prevMs: Int64?, at atMs: Int64, zone: TimeZone) -> Bool {
        startsStretch(prev: prevMs, at: atMs, cal: calendar(zone))
    }

    private static func startsStretch(prev prevMs: Int64?, at atMs: Int64, cal: Calendar) -> Bool {
        guard let prevMs, prevMs > 0 else { return true }
        return !cal.isDate(date(prevMs), inSameDayAs: date(atMs)) || atMs - prevMs >= gapMinutes * 60_000
    }

    /// The keys of the messages in `items` (oldest first) that get a time line. Messages without a time (`at` 0) get none and do not
    /// count; rows that are not messages are skipped.
    public static func labelled(_ items: [ChatItem], zone: TimeZone) -> Set<String> {
        var out = Set<String>()
        var prev: Int64? = nil
        let cal = calendar(zone)
        for item in items {
            guard let m = item.asMsg, m.at > 0 else { continue }
            if startsStretch(prev: prev, at: m.at, cal: cal) { out.insert(m.key) }
            prev = m.at
        }
        return out
    }

    /// The words of the time line for `atMs`; `format` renders one of `patterns` for that moment.
    public static func label(_ atMs: Int64, now nowMs: Int64, zone: TimeZone, patterns: Patterns, today: String, yesterday: String,
                             format: (_ pattern: String, _ atMs: Int64) -> String) -> String {
        switch day(atMs, now: nowMs, zone: zone) {
        case .today: return "\(today) \(format(patterns.time, atMs))"
        case .yesterday: return "\(yesterday) \(format(patterns.time, atMs))"
        case .thisYear: return format(patterns.monthDayTime, atMs)
        case .earlier: return format(patterns.fullDateTime, atMs)
        }
    }

    /// `label` with the locale's own formatter. The patterns and formatters are made once per locale and zone (a chat redraws
    /// often while a reply streams; building date formatters each time would keep the main thread busy).
    public static func label(_ atMs: Int64, now nowMs: Int64 = Int64(Date().timeIntervalSince1970 * 1000), zone: TimeZone = .current,
                             locale: Locale, today: String, yesterday: String) -> String {
        Formatters.shared(locale, zone).label(atMs, now: nowMs, today: today, yesterday: yesterday)
    }

    private final class Formatters {
        nonisolated(unsafe) private static var cache: [String: Formatters] = [:]
        private static let lock = NSLock()

        static func shared(_ locale: Locale, _ zone: TimeZone) -> Formatters {
            lock.lock(); defer { lock.unlock() }
            let key = locale.identifier + "|" + zone.identifier
            if let f = cache[key] { return f }
            let f = Formatters(locale, zone)
            cache[key] = f
            return f
        }

        private let zone: TimeZone
        private let patterns: Patterns
        private var formats: [String: DateFormatter] = [:]
        private let locale: Locale
        private let lock = NSLock()

        private init(_ locale: Locale, _ zone: TimeZone) {
            self.locale = locale
            self.zone = zone
            patterns = .of(locale)
        }

        func label(_ atMs: Int64, now nowMs: Int64, today: String, yesterday: String) -> String {
            lock.lock(); defer { lock.unlock() }
            return TimeLabels.label(atMs, now: nowMs, zone: zone, patterns: patterns, today: today, yesterday: yesterday) { pattern, at in
                let f: DateFormatter
                if let made = formats[pattern] {
                    f = made
                } else {
                    f = DateFormatter()
                    f.locale = locale
                    f.timeZone = zone
                    f.calendar = TimeLabels.calendar(zone)
                    f.dateFormat = pattern
                    formats[pattern] = f
                }
                return f.string(from: TimeLabels.date(at))
            }
        }
    }
}
