import Foundation

/// What a followed reply is doing: writing (or between steps), in the browser, reading, or using another tool.
public enum TurnKind: Sendable, Equatable { case thinking, browser, reading, tool }

/// The rules behind the header's name pill and the character (Android chat/AgentActivity.kt AgentActivityRules, same order and
/// constants). While the chat follows a reply, the reply's own events say what the agent does; otherwise GET /api/agent/status,
/// polled every few seconds in the foreground, covers work the chat is not following (scheduled or background runs).
public enum AgentActivityRules {
    public static let pollMs: UInt64 = 3_000

    /// The line to show: the followed reply's own, else the polled one.
    public static func shown(following: Bool, turnLine: String?, polled: String?) -> String? {
        nonBlank((following ? turnLine : polled)?.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    /// The status endpoint's line: only while busy; its live status, else the task's title.
    public static func line(_ s: AgentStatus) -> String? {
        guard s.busy else { return nil }
        return nonBlankKeep(s.status) ?? nonBlankKeep(s.title)
    }

    /// The followed reply's line after `ev`: a visible tool's label, the browser's status, an approval's label; writing the answer
    /// clears it; anything else keeps it.
    public static func next(_ current: String?, _ ev: TurnEvent) -> String? {
        switch ev {
        case .toolStarted(let t): return t.hidden ? current : (t.label.isEmpty ? current : t.label)
        case .browser(let b): return b.status.isEmpty ? current : b.status
        case .approvalRequest(let a): return a.label.isEmpty ? current : a.label
        case .text, .done, .error: return nil
        default: return current
        }
    }

    /// Tools that read (files, documents, mail, memory, search): the character reads a book meanwhile.
    static let readingTools: Set<String> = [
        "read_file", "list_files", "search_files", "extract_document", "email_list", "email_read", "journal_search", "search_web",
        "web_search", "view_image", "workroom_files", "search_skills", "connector_get", "list_product_images",
    ]

    /// The kind of a tool by its name.
    public static func toolKind(_ name: String) -> TurnKind {
        if name.hasPrefix("browser_") { return .browser }
        if readingTools.contains(name) || name.hasPrefix("memory") { return .reading }
        return .tool
    }

    /// What the followed reply is doing after `ev`: a browser picture says browser; a visible tool starting says its kind; writing
    /// the answer (or the end) says thinking; anything else keeps the last one, so back-to-back browser steps do not flicker.
    public static func turnKind(_ current: TurnKind, _ ev: TurnEvent) -> TurnKind {
        switch ev {
        case .browser: return .browser
        case .toolStarted(let t): return t.hidden ? current : toolKind(t.name)
        case .text, .done, .error: return .thinking
        default: return current
        }
    }

    /// How recent a tab's last action must be (seconds) to count as the agent using the browser now.
    public static let browserRecentS = 30.0

    /// Whether a run the chat is not following is using the browser: a "working" tab whose last action is recent (against `nowS`,
    /// the phone's clock) or moved since the previous poll (`before`, by tab id). Only while `s` says the agent is busy.
    public static func polledBrowsing(_ s: AgentStatus?, tabs: [BrowserTabBrief], before: [String: Double], nowS: Double) -> Bool {
        guard let s, s.busy else { return false }
        return tabs.contains { t in
            guard t.status == "working", let at = t.lastActivity else { return false }
            return nowS - at <= browserRecentS || (before[t.id] ?? at) < at
        }
    }

    /// Network failures in a row before the character shows the server as unreachable (one blip is not enough).
    public static let offlineAfter = 2
    /// How long the character cheers after work finished (two of its 0.8 s jumps).
    public static let doneMs: Int64 = 1_600
    /// A job waiting for the user counts this long after its last change (older ones stay in the work list, not on the character).
    public static let stuckForS = 12 * 3600.0

    /// A job is stuck on the user: the newest task failed and nobody has looked at it, or a background job has been waiting for the
    /// user since less than `stuckForS` ago.
    public static func needsUser(newest: TaskBrief?, waiting: [WorkerBrief], nowS: Double) -> Bool {
        if let n = newest, n.status == "failed", !n.seen { return true }
        return waiting.contains { $0.status == "needs_input" && ($0.updatedAt ?? 0) >= nowS - stuckForS }
    }

    /// Work just finished (busy at the previous poll, not now) and the newest task went well.
    public static func finished(before: AgentStatus?, now: AgentStatus, newest: TaskBrief?) -> Bool {
        guard let before, let newest else { return false }
        return before.busy && !now.busy && newest.status == "done"
    }

    /// How long nothing must happen (no message typed, sent or come in, no reply, no work, approval or listening) before the
    /// character may fall asleep at night; until then it rests awake.
    public static let sleepAfterIdleMs: Int64 = 5 * 60_000

    /// The late night (23:00-07:00) used when the owner's contact hours are not known.
    public static func isNight(hour: Int) -> Bool { hour >= 23 || hour < 7 }

    /// "07:30" -> minutes of the day, or nil when it is not a time.
    public static func minutesOf(_ text: String?) -> Int? {
        let parts = (text ?? "").trimmingCharacters(in: .whitespaces).split(separator: ":", omittingEmptySubsequences: false)
        guard parts.count == 2, (1...2).contains(parts[0].count), parts[1].count == 2,
              (parts[0] + parts[1]).allSatisfy({ $0.isASCII && $0.isNumber }),
              let h = Int(parts[0]), let m = Int(parts[1]), (0...23).contains(h), (0...59).contains(m) else { return nil }
        return h * 60 + m
    }

    /// The owner's waking window as (start, end) minutes of the day, when the server knows it: hours the owner set or Kelo learned
    /// ("manual" / "learned"). The server's untouched default (07:00-22:00, "default") is not the owner's, so it gives nil.
    public static func wakeWindow(_ h: SleepHours?) -> (start: Int, end: Int)? {
        guard let h, h.hoursSource == "manual" || h.hoursSource == "learned",
              let a = minutesOf(h.wakeStart), let b = minutesOf(h.wakeEnd) else { return nil }
        return (a, b)
    }

    /// Whether `minuteOfDay` is night: outside the owner's waking window when known (`wakeWindow`; its end not included, a window
    /// past midnight such as 10:00-02:00 works, equal ends mean awake all day, as the server counts it), else 23:00-07:00.
    public static func isNight(minuteOfDay: Int, hours: SleepHours?) -> Bool {
        guard let w = wakeWindow(hours) else { return isNight(hour: minuteOfDay / 60) }
        let (a, b) = (w.start, w.end)
        if a == b { return false }
        let awake = a < b ? (minuteOfDay >= a && minuteOfDay < b) : (minuteOfDay >= a || minuteOfDay < b)
        return !awake
    }

    /// A late night belongs to the evening before: a moment counts for the day it was this many hours earlier (as the server does).
    public static let dayCutHours = 4

    /// weekday | weekend of the day "YYYY-MM-DD": the server's `SleepHours.dayTypes` (holidays included), else Saturday and Sunday.
    public static func dayType(_ hours: SleepHours?, day: String, weekday: Int) -> String {
        if let t = hours?.dayTypes[day] { return t }
        return weekday == 1 || weekday == 7 ? "weekend" : "weekday"   // Calendar weekday: 1 = Sunday, 7 = Saturday
    }

    /// The hours that apply on a day: the weekend window on a weekend or holiday when it has its own, else the weekday one.
    public static func hoursFor(_ hours: SleepHours?, dayType: String) -> SleepHours? {
        guard var h = hours, dayType == "weekend", h.weekendSource == "manual" || h.weekendSource == "learned",
              minutesOf(h.weekendStart) != nil, minutesOf(h.weekendEnd) != nil else { return hours }
        h.wakeStart = h.weekendStart; h.wakeEnd = h.weekendEnd; h.hoursSource = h.weekendSource
        return h
    }

    /// Night at `date`: the owner's window for that day's type (`hoursFor`; the day of a moment is the one `dayCutHours` earlier, so
    /// 01:00 on a Saturday is still Friday night) read in their time zone (`SleepHours.timezone`); anything else in `local`.
    public static func nightAt(_ date: Date, local: TimeZone = .current, hours: SleepHours?) -> Bool {
        let known = wakeWindow(hours) != nil || (hours.map { $0.weekendSource == "manual" || $0.weekendSource == "learned" } ?? false)
        let zone = !known ? local : (hours.flatMap { TimeZone(identifier: $0.timezone) } ?? local)
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let c = cal.dateComponents([.hour, .minute], from: date)
        let dayDate = date.addingTimeInterval(-Double(dayCutHours) * 3600)
        let d = cal.dateComponents([.year, .month, .day, .weekday], from: dayDate)
        let day = String(format: "%04d-%02d-%02d", d.year ?? 2000, d.month ?? 1, d.day ?? 1)
        let type = dayType(hours, day: day, weekday: d.weekday ?? 2)
        return isNight(minuteOfDay: (c.hour ?? 0) * 60 + (c.minute ?? 0), hours: hoursFor(hours, dayType: type))
    }

    /// ms since the last activity (`activeAt`, monotonic seconds; nil = none) at `now` on the same clock; nil = nothing since launch.
    public static func idleMs(now: Double, activeAt: Double?) -> Int64? {
        guard let activeAt else { return nil }
        return max(0, Int64((now - activeAt) * 1000))
    }

    /// Everything the character's mood is decided from.
    public struct MoodInput: Sendable {
        public var offline = false
        public var status: AgentStatus? = nil
        public var stuck = false
        public var listening = false
        public var following = false
        public var turnKind: TurnKind = .thinking
        public var polledBrowsing = false
        /// ms since work finished (nil = not recently)
        public var sinceDoneMs: Int64? = nil
        public var night = false
        /// ms since the owner or the agent last did something (`idleMs`; nil = nothing since launch)
        public var idleMs: Int64? = nil

        public init(offline: Bool = false, status: AgentStatus? = nil, stuck: Bool = false, listening: Bool = false, following: Bool = false,
                    turnKind: TurnKind = .thinking, polledBrowsing: Bool = false, sinceDoneMs: Int64? = nil, night: Bool = false,
                    idleMs: Int64? = nil) {
            self.offline = offline; self.status = status; self.stuck = stuck; self.listening = listening; self.following = following
            self.turnKind = turnKind; self.polledBrowsing = polledBrowsing; self.sinceDoneMs = sinceDoneMs; self.night = night
            self.idleMs = idleMs
        }
    }

    /// The character's mood, the first that applies: unreachable server, an approval waiting, a job stuck on the user, the
    /// microphone listening, then the work itself (the followed reply's kind; else a polled run in the browser or at work), the cheer
    /// after finishing, sleep at night once nothing has happened for `sleepAfterIdleMs`, else rest.
    public static func mood(_ m: MoodInput) -> KeloMood {
        let busy = m.following || m.status?.busy == true
        if m.offline { return .offline }
        if (m.status?.pendingApprovals ?? 0) > 0 { return .approval }
        if m.stuck { return .error }
        if m.listening { return .listening }
        if m.following {
            switch m.turnKind {
            case .browser: return .browser
            case .reading: return .reading
            case .tool: return .working
            case .thinking: return .thinking
            }
        }
        if busy { return m.polledBrowsing ? .browser : .working }
        if let since = m.sinceDoneMs, since < doneMs { return .done }
        if m.night && (m.idleMs ?? sleepAfterIdleMs) >= sleepAfterIdleMs { return .sleeping }
        return .idle
    }

    private static func nonBlank(_ s: String?) -> String? { (s?.isEmpty ?? true) ? nil : s }
    /// Kotlin `ifBlank { null }`: blank becomes nil, anything else is kept as it is (untrimmed).
    private static func nonBlankKeep(_ s: String?) -> String? {
        guard let s, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return s
    }
}

/// The contact hours of GET /api/proactive, only what the character's night needs: the owner's waking window (in `timezone`) and
/// where it came from (default | manual | learned) (Android chat/AgentActivity.kt SleepHours).
public struct SleepHours: Decodable, Equatable, Sendable {
    public var wakeStart = "", wakeEnd = ""
    public var hoursSource = "default"
    public var timezone = ""
    /// The weekend and holiday window ("default" source = it follows the weekdays) and the day type of the next days
    /// ("YYYY-MM-DD" -> weekday | weekend, holidays already counted by the server; an older server sends none).
    public var weekendStart = "", weekendEnd = ""
    public var weekendSource = "default"
    public var dayTypes: [String: String] = [:]

    public init(wakeStart: String = "", wakeEnd: String = "", hoursSource: String = "default", timezone: String = "",
                weekendStart: String = "", weekendEnd: String = "", weekendSource: String = "default", dayTypes: [String: String] = [:]) {
        self.wakeStart = wakeStart; self.wakeEnd = wakeEnd; self.hoursSource = hoursSource; self.timezone = timezone
        self.weekendStart = weekendStart; self.weekendEnd = weekendEnd; self.weekendSource = weekendSource; self.dayTypes = dayTypes
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        wakeStart = j.str("wake_start"); wakeEnd = j.str("wake_end"); hoursSource = j.str("hours_source", "default")
        timezone = j.str("timezone")
        weekendStart = j.str("weekend_start"); weekendEnd = j.str("weekend_end"); weekendSource = j.str("weekend_source", "default")
        dayTypes = j.decode("day_types", as: [String: String].self) ?? [:]
    }
}
