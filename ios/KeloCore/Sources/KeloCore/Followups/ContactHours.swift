import Foundation

// Message hours (server active_hours.py, GET /api/proactive), as Android my/ContactHours.kt: the weekday hours and the weekend and
// holiday hours, where each came from, a learned suggestion per day type, and the learned windows that differ.

/// A learned contact-hours suggestion (GET /api/proactive "hours_suggestion" / "weekend_suggestion", the briefing card's "hours").
public struct HoursSuggestion: Decodable, Equatable, Sendable {
    public var id = ""
    public var wakeStart = "", wakeEnd = ""
    public var currentStart = "", currentEnd = ""
    public var activeDays = 0
    public var createdAt: Double?
    /// weekday | weekend (weekends and public holidays); an older server sends none: weekday.
    public var dayType = ContactHoursRules.weekday

    public init(id: String = "", wakeStart: String = "", wakeEnd: String = "", currentStart: String = "", currentEnd: String = "",
                activeDays: Int = 0, createdAt: Double? = nil, dayType: String = ContactHoursRules.weekday) {
        self.id = id; self.wakeStart = wakeStart; self.wakeEnd = wakeEnd; self.currentStart = currentStart; self.currentEnd = currentEnd
        self.activeDays = activeDays; self.createdAt = createdAt; self.dayType = dayType
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        id = j.str("id"); wakeStart = j.str("wake_start"); wakeEnd = j.str("wake_end")
        currentStart = j.str("current_start"); currentEnd = j.str("current_end"); activeDays = j.int("active_days")
        createdAt = j.optDouble("created_at")
        dayType = j.str("day_type", ContactHoursRules.weekday)
    }
}

/// A window learned from the owner's use that differs from the hours in effect (GET /api/proactive "learned").
public struct LearnedWindow: Decodable, Equatable, Sendable {
    public var wakeStart = "", wakeEnd = ""
    public var activeDays = 0

    public init(wakeStart: String = "", wakeEnd: String = "", activeDays: Int = 0) {
        self.wakeStart = wakeStart; self.wakeEnd = wakeEnd; self.activeDays = activeDays
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        wakeStart = j.str("wake_start"); wakeEnd = j.str("wake_end"); activeDays = j.int("active_days")
    }
}

/// The message-hours part of GET /api/proactive: the weekday hours (`wakeStart`..`wakeEnd`, what older servers send alone) and the
/// weekend and holiday hours (`weekendStart`..`weekendEnd` in effect; `weekendSource` "default" = they follow the weekdays), a
/// suggestion per day type, and the learned windows that differ.
public struct ContactHours: Decodable, Equatable, Sendable {
    public var wakeStart = "07:00", wakeEnd = "22:00"
    public var autoHours = false
    /// default | manual | learned: where wake_start..wake_end came from.
    public var hoursSource = "default"
    public var suggestion: HoursSuggestion?
    public var weekendStart = "", weekendEnd = ""
    public var weekendSource = "default"
    public var weekendSuggestion: HoursSuggestion?
    public var learned: [String: LearnedWindow] = [:]
    /// Today's day type (the server's; holidays included).
    public var dayType = ContactHoursRules.weekday
    /// The server knows weekend hours (an older server does not: the page shows one row).
    public var holidayCountry: String?

    public init() {}

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        wakeStart = j.str("wake_start", "07:00"); wakeEnd = j.str("wake_end", "22:00"); autoHours = j.bool("auto_hours")
        hoursSource = j.str("hours_source", "default")
        suggestion = j.decode("hours_suggestion", as: HoursSuggestion.self).flatMap { $0.id.isEmpty ? nil : $0 }
        weekendStart = j.str("weekend_start"); weekendEnd = j.str("weekend_end"); weekendSource = j.str("weekend_source", "default")
        weekendSuggestion = j.decode("weekend_suggestion", as: HoursSuggestion.self).flatMap { $0.id.isEmpty ? nil : $0 }
        var l: [String: LearnedWindow] = [:]
        for (k, v) in j["learned"]?.objectValue ?? [:] where !v.isNull {
            if let w = try? JSON.decode(LearnedWindow.self, from: v) { l[k] = w }
        }
        learned = l
        dayType = j.str("day_type", ContactHoursRules.weekday)
        holidayCountry = j.optStr("holiday_country")
    }

    /// The server knows weekend hours: the notifications page shows a row per day type.
    public var hasWeekend: Bool { holidayCountry != nil || !weekendStart.isEmpty || weekendSuggestion != nil || learned[ContactHoursRules.weekend] != nil }
}

/// Small rules of the message hours, apart from the UI (Android ContactHoursRules).
public enum ContactHoursRules {
    public static let learnedSource = "learned"
    public static let weekday = "weekday", weekend = "weekend"
    /// How long the row says what was chosen before it goes.
    public static let doneSeconds: Double = 2.5

    public static func learned(_ source: String) -> Bool { source == learnedSource }

    /// "07:30" -> minutes of the day, or nil when it is not a time.
    public static func minutes(_ text: String) -> Int? {
        let parts = text.trimmingCharacters(in: .whitespaces).split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, (1...2).contains(parts[0].count), parts[1].count == 2,
              let h = Int(parts[0]), let m = Int(parts[1]), (0...23).contains(h), (0...59).contains(m) else { return nil }
        return h * 60 + m
    }

    /// A window whose end is before its start runs past midnight (10:00–01:00). Equal ends mean the whole day.
    public static func pastMidnight(_ start: String, _ end: String) -> Bool {
        guard let a = minutes(start), let b = minutes(end) else { return false }
        return b < a
    }

    /// A suggestion the page can show: an id, two real times, and different from the hours set now.
    public static func shown(_ s: HoursSuggestion?) -> Bool {
        guard let s, !s.id.trimmingCharacters(in: .whitespaces).isEmpty, minutes(s.wakeStart) != nil, minutes(s.wakeEnd) != nil else { return false }
        return s.wakeStart != s.currentStart || s.wakeEnd != s.currentEnd
    }

    /// The open suggestion to show first: the weekday one, else the weekend one (each only when `shown`).
    public static func open(_ h: ContactHours?) -> HoursSuggestion? {
        if let s = h?.suggestion, shown(s) { return s }
        if var s = h?.weekendSuggestion, shown(s) { s.dayType = weekend; return s }
        return nil
    }

    /// (start, end, source) of one day type's hours. Weekends without hours of their own show the weekday ones (source "default").
    public static func window(_ h: ContactHours, _ kind: String) -> (start: String, end: String, source: String) {
        if kind == weekend && minutes(h.weekendStart) != nil && minutes(h.weekendEnd) != nil { return (h.weekendStart, h.weekendEnd, h.weekendSource) }
        if kind == weekend { return (h.wakeStart, h.wakeEnd, "default") }
        return (h.wakeStart, h.wakeEnd, h.hoursSource)
    }

    /// The learned window of `kind` worth an Apply button: real times, different from the hours in effect.
    public static func learnedFor(_ h: ContactHours, _ kind: String) -> LearnedWindow? {
        guard let l = h.learned[kind], minutes(l.wakeStart) != nil, minutes(l.wakeEnd) != nil else { return nil }
        let w = window(h, kind)
        return l.wakeStart != w.start || l.wakeEnd != w.end ? l : nil
    }

    /// The PUT /api/proactive field of one day type's start or end.
    public static func field(_ kind: String, start: Bool) -> String {
        switch (kind == weekend, start) {
        case (true, true): "weekend_start"
        case (true, false): "weekend_end"
        case (false, true): "wake_start"
        case (false, false): "wake_end"
        }
    }

    /// The "from" and "until" buttons share a row only when each one's text fits on one line in half of it; otherwise both go full
    /// width, one under the other, so neither label wraps (320pt, German, "다음 날 오전 1:00까지"). Android `timesSideBySide`.
    public static func timesSideBySide(_ first: Double, _ second: Double, available: Double, gap: Double) -> Bool {
        available > 0 && 2 * max(first, second) + gap <= available
    }

    public static func applyPath(_ kind: String) -> String { "/api/proactive/hours/learned/\(KeloApi.segment(kind))/apply" }
    public static func acceptPath(_ id: String) -> String { "/api/proactive/hours/\(KeloApi.segment(id))/accept" }
    public static func keepPath(_ id: String) -> String { "/api/proactive/hours/\(KeloApi.segment(id))/keep" }
}
