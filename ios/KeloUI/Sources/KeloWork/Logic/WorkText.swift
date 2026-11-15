import Foundation
import KeloCore
import KeloShared

/// Dates, times and schedules in the app's language (Android agent/AgentFormat.kt): the words come from the strings, the shapes
/// from the locale. A schedule is always said in words, never as its cron expression.
@MainActor
enum WorkText {
    static var locale: Locale { Strings.shared.locale }

    private static func formatter(_ template: String, zone: TimeZone = .current) -> DateFormatter {
        let f = DateFormatter()
        f.locale = locale
        f.timeZone = zone
        f.dateFormat = DateFormatter.dateFormat(fromTemplate: template, options: 0, locale: locale) ?? template
        return f
    }

    private static func date(_ seconds: Double) -> Date { Date(timeIntervalSince1970: seconds) }

    /// "오후 3:05"
    static func clock(_ seconds: Double) -> String {
        let f = DateFormatter()
        f.locale = locale
        f.timeStyle = .short
        f.dateStyle = .none
        return f.string(from: date(seconds))
    }

    static func clock(hour: Int, minute: Int) -> String {
        let f = DateFormatter()
        f.locale = locale
        f.timeZone = TimeZone(identifier: "UTC")
        f.timeStyle = .short
        f.dateStyle = .none
        return f.string(from: Date(timeIntervalSince1970: Double(hour * 3600 + minute * 60)))
    }

    /// "11월 8일 (일)"
    static func day(_ d: LocalDay) -> String { formatter("MMMdEEE", zone: TimeZone(identifier: "UTC")!).string(from: d.dateUTC) }

    /// "2026년 11월 8일 (일)"
    static func fullDay(_ d: LocalDay) -> String { formatter("yMMMdEEE", zone: TimeZone(identifier: "UTC")!).string(from: d.dateUTC) }

    static func dayOrFull(_ d: LocalDay, today: LocalDay) -> String { d.year == today.year ? day(d) : fullDay(d) }

    /// "11월 8일 (일) 오전 9:00"
    static func dayClock(_ seconds: Double) -> String { L(R.string.agent_day_time, day(LocalDay(seconds: seconds)), clock(seconds)) }

    /// "11월 8일"
    static func monthDay(month: Int, day: Int) -> String {
        var c = DateComponents(); c.year = 2024; c.month = month; c.day = max(1, min(day, 31))
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let d = cal.date(from: c) ?? Date()
        return formatter("MMMd", zone: TimeZone(identifier: "UTC")!).string(from: d)
    }

    static func dayLabel(_ label: DayGroups.Label, today: LocalDay = .today()) -> String {
        switch label {
        case .today: return L(R.string.agent_today)
        case .yesterday: return L(R.string.agent_yesterday)
        case .date(let d): return d.year == today.year ? day(d) : fullDay(d)
        case .unknown: return L(R.string.agent_day_unknown)
        }
    }

    /// "8시간 뒤" / "3분 전" / "곧" / "방금"
    static func rel(_ seconds: Double, now: Double = Date().timeIntervalSince1970) -> String {
        let r = RelTime.of(seconds, now: now)
        let n = Int(r.amount)
        switch r.unit {
        case .now: return L(r.future ? R.string.agent_rel_soon : R.string.agent_rel_now)
        case .minutes: return P(r.future ? R.plurals.agent_rel_minutes_future : R.plurals.agent_rel_minutes_past, n, n)
        case .hours: return P(r.future ? R.plurals.agent_rel_hours_future : R.plurals.agent_rel_hours_past, n, n)
        case .days: return P(r.future ? R.plurals.agent_rel_days_future : R.plurals.agent_rel_days_past, n, n)
        }
    }

    /// A weekday's short name ("월"), 0 = Sunday.
    static func weekdayShort(_ cronDay: Int) -> String {
        let f = DateFormatter(); f.locale = locale
        let names = f.shortWeekdaySymbols ?? []
        return names.indices.contains(cronDay % 7) ? names[cronDay % 7] : ""
    }

    static func weekdayFull(_ cronDay: Int) -> String {
        let f = DateFormatter(); f.locale = locale
        let names = f.weekdaySymbols ?? []
        return names.indices.contains(cronDay % 7) ? names[cronDay % 7] : ""
    }

    static func weekdayNarrow(_ cronDay: Int) -> String {
        let f = DateFormatter(); f.locale = locale
        let names = f.veryShortWeekdaySymbols ?? []
        return names.indices.contains(cronDay % 7) ? names[cronDay % 7] : ""
    }

    /// The first day of the week where the user is (0 = Sunday).
    static var firstWeekday: Int {
        var cal = Calendar(identifier: .gregorian)
        let region = Locale.current.region?.identifier
        let id = locale.region == nil && region != nil ? locale.identifier + "_" + region! : locale.identifier
        cal.locale = Locale(identifier: id)
        return cal.firstWeekday - 1
    }

    /// "30분마다" / "2시간마다" / "하루마다"
    static func interval(_ minutes: Int) -> String {
        if minutes % 1440 == 0 { return P(R.plurals.work_every_days, minutes / 1440, minutes / 1440) }
        if minutes % 60 == 0 { return P(R.plurals.agent_repeat_every_hours, minutes / 60, minutes / 60) }
        return P(R.plurals.work_every_minutes, minutes, minutes)
    }

    /// What a repeat means, in words; a rule the app has no words for uses the server's sentence (never the expression).
    static func repeatText(_ r: PlanRepeat, serverWords: String = "") -> String {
        let sep = L(R.string.agent_list_separator)
        switch r {
        case .daily(let h, let m): return L(R.string.agent_repeat_daily_at, clock(hour: h, minute: m))
        case .weekdays(let h, let m): return L(R.string.agent_repeat_weekdays_at, clock(hour: h, minute: m))
        case .weekly(let days, let h, let m):
            if Set(days.map { $0 % 7 }) == [0, 6] { return L(R.string.agent_repeat_weekends_at, clock(hour: h, minute: m)) }
            let names = PlanCron.pickerDays.filter { days.contains($0) }.map(weekdayShort).joined(separator: sep)
            return L(R.string.agent_repeat_weekly_at, names, clock(hour: h, minute: m))
        case .monthly(let d, let h, let m): return L(R.string.agent_repeat_monthly_at, d, clock(hour: h, minute: m))
        case .yearly(let mo, let d, let h, let m): return L(R.string.agent_repeat_yearly_at, monthDay(month: mo, day: d), clock(hour: h, minute: m))
        case .everyHours(let n, let m):
            if n <= 1 { return L(R.string.agent_repeat_hourly_at, m) }
            if m == 0 { return P(R.plurals.agent_repeat_every_hours, n, n) }
            return L(R.string.agent_repeat_every_hours_at, P(R.plurals.agent_repeat_every_hours, n, n), m)
        case .once(let date, let h, let m): return L(R.string.agent_repeat_once_at, fullDay(date), clock(hour: h, minute: m))
        case .interval(let minutes): return interval(minutes)
        case .custom:
            let w = serverWords.trimmingCharacters(in: .whitespacesAndNewlines)
            return !w.isEmpty && !PlanCron.looksLikeCron(w) ? w : L(R.string.sched_text_rule)
        }
    }

    /// A repeat's words with its first and last day ("매일 오전 9:00, 12월 31일까지").
    static func withDays(_ words: String, startsOn: LocalDay?, endsOn: LocalDay?, today: LocalDay = .today()) -> String {
        var out = words
        if let s = startsOn, s > today { out = L(R.string.sched_text_from, out, dayOrFull(s, today: today)) }
        if let e = endsOn { out = L(R.string.sched_text_until, out, dayOrFull(e, today: today)) }
        return out
    }

    /// How a saved schedule runs, in words (the same in the agenda and on its page).
    static func schedule(_ s: Schedule, days: ScheduleSpan? = nil) -> String {
        let r = PlanCron.fromSchedule(s)
        let words = repeatText(r, serverWords: s.description)
        switch r {
        case .custom, .once: return words
        default: return withDays(words, startsOn: ScheduleDays.parse(days?.startsOn), endsOn: ScheduleDays.parse(days?.endsOn))
        }
    }

    /// The simple form's choice in words, as the agenda will show it once saved.
    static func plan(_ p: SchedulePlan, now: LocalMoment = .now()) -> String {
        let words = repeatText(PlanRules.asRepeat(p))
        if p.freq == .none { return words }
        return withDays(words, startsOn: ScheduleDays.parse(PlanRules.startsOn(p, now: now)), endsOn: p.until, today: now.day)
    }

    /// The repeat row's value: "안 함", "매일", "매주 월, 수", "매달 9일", "매년 11월 9일".
    static func repeatValue(_ p: SchedulePlan) -> String {
        switch p.freq {
        case .none: return L(R.string.sched_freq_none)
        case .daily: return L(R.string.agent_repeat_daily)
        case .weekdays: return L(R.string.agent_repeat_weekdays)
        case .weekends: return L(R.string.sched_freq_weekends)
        case .weekly:
            let names = PlanRules.weekOrder(first: firstWeekday).filter { p.days.contains($0) }.map(weekdayShort)
            return L(R.string.sched_value_weekly, names.joined(separator: L(R.string.agent_list_separator)))
        case .monthly: return L(R.string.sched_value_monthly, p.date.day)
        case .yearly: return L(R.string.sched_value_yearly, monthDay(month: p.date.month, day: p.date.day))
        }
    }

    static func freqOption(_ f: Freq, date: LocalDay) -> String {
        switch f {
        case .none: return L(R.string.sched_freq_none)
        case .daily: return L(R.string.agent_repeat_daily)
        case .weekdays: return L(R.string.agent_repeat_weekdays)
        case .weekends: return L(R.string.sched_freq_weekends)
        case .weekly: return L(R.string.sched_freq_weekly)
        case .monthly: return L(R.string.sched_opt_monthly, date.day)
        case .yearly: return L(R.string.sched_opt_yearly, monthDay(month: date.month, day: date.day))
        }
    }

    // ---- states in words

    static func taskStatus(_ status: String) -> String {
        switch status {
        case AgentTask.statusRunning: return L(R.string.agent_status_running)
        case AgentTask.statusWaiting: return L(R.string.agent_status_waiting)
        case AgentTask.statusFailed: return L(R.string.agent_status_failed)
        case AgentTask.statusStopped: return L(R.string.agent_status_stopped)
        case "interrupted": return L(R.string.work_job_interrupted)
        case "waiting_model": return L(R.string.agent_status_waiting_model)
        default: return L(R.string.agent_status_done)
        }
    }

    static func source(_ source: String) -> String? {
        if source.hasPrefix("schedule") { return L(R.string.agent_source_schedule) }
        switch source {
        case "background": return L(R.string.agent_source_background)
        case "worker": return L(R.string.work_source_job)
        case "handoff": return L(R.string.work_source_handoff)
        default: return nil
        }
    }

    static func jobStatus(_ status: String) -> String {
        switch status {
        case Worker.statusRunning: return L(R.string.agent_status_running)
        case Worker.statusNeedsInput: return L(R.string.work_job_needs_input)
        case Worker.statusBlocked: return L(R.string.work_job_blocked)
        case Worker.statusFailed: return L(R.string.agent_status_failed)
        case Worker.statusStopped: return L(R.string.agent_status_stopped)
        case Worker.statusInterrupted: return L(R.string.work_job_interrupted)
        default: return L(R.string.agent_status_done)
        }
    }

    static func jobKind(_ kind: String) -> String {
        switch kind {
        case "browser": return L(R.string.work_kind_browser)
        case "research": return L(R.string.work_kind_research)
        default: return L(R.string.work_kind_general)
        }
    }

    /// What a secure entry waits for, in words ("텔레그램 봇 토큰 입력을 기다려요").
    static func entry(_ r: SecretRequest) -> String {
        let name = !r.label.isEmpty ? r.label : (r.hosts.first ?? r.name)
        if r.isCard { return L(R.string.work_entry_card) }
        if r.isPin { return L(R.string.work_entry_pin, r.hosts.first ?? name) }
        if r.isLogin { return L(R.string.work_entry_login, name) }
        return L(R.string.work_entry_key, name)
    }

    static func waiting(_ w: Worker) -> String {
        if w.status == Worker.statusInterrupted { return L(R.string.work_wait_restart) }
        if w.waitingFor == "login" { return L(R.string.work_wait_login) }
        if w.waitingFor == "control" { return L(R.string.work_wait_control) }
        if w.status == Worker.statusBlocked { return L(R.string.work_wait_blocked) }
        return L(R.string.work_wait_answer)
    }

    static func run(_ status: String) -> String {
        switch status {
        case "running": return L(R.string.agent_status_running)
        case "quiet": return L(R.string.work_run_quiet)
        case "failed": return L(R.string.agent_status_failed)
        case "stopped": return L(R.string.agent_status_stopped)
        case "skipped", "missed": return L(R.string.agent_status_missed)
        case "paused": return L(R.string.work_run_paused)
        default: return L(R.string.agent_status_done)
        }
    }

    static func watch(_ state: String) -> String {
        switch state {
        case "watching": return L(R.string.work_watch_on)
        case "error": return L(R.string.work_watch_error)
        case "done": return L(R.string.work_watch_done)
        default: return L(R.string.work_watch_off)
        }
    }

    static func watchEvent(_ kind: String) -> String {
        switch kind {
        case "fired": return L(R.string.work_watch_ev_fired)
        case "error": return L(R.string.work_watch_ev_error)
        case "enabled": return L(R.string.work_watch_ev_on)
        case "disabled": return L(R.string.work_watch_ev_off)
        default: return L(R.string.work_watch_ev_check)
        }
    }

    static func decision(_ d: String) -> String {
        switch d {
        case "allowed_once": return L(R.string.agent_decision_once)
        case "allowed_always": return L(R.string.agent_decision_always)
        case "allowed_by_rule": return L(R.string.agent_decision_rule)
        case "denied": return L(R.string.agent_decision_denied)
        case "expired": return L(R.string.agent_decision_expired)
        case "cancelled": return L(R.string.agent_decision_cancelled)
        default: return L(R.string.agent_decision_pending)
        }
    }

    static func tabKind(_ t: BrowserTab) -> String {
        switch t.kind {
        case "owner": return L(R.string.agent_tab_kind_owner)
        case "schedule": return L(R.string.agent_tab_kind_schedule)
        case "task": return L(R.string.agent_tab_kind_task)
        default: return t.label
        }
    }

    static func followupKind(_ kind: String) -> String {
        switch kind {
        case "login": return L(R.string.followup_kind_login)
        case "worker": return L(R.string.followup_kind_worker)
        case "approval": return L(R.string.followup_kind_approval)
        case "approval_expired": return L(R.string.followup_kind_approval_expired)
        default: return L(R.string.followup_kind_reply)
        }
    }

    /// "30분 뒤" / "2시간 뒤" / "1일 뒤"
    static func later(_ minutes: Int) -> String {
        if minutes >= 1440 && minutes % 1440 == 0 { return P(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440) }
        if minutes >= 60 && minutes % 60 == 0 { return P(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60) }
        return P(R.plurals.agent_rel_minutes_future, minutes, minutes)
    }

    static func followupAction(_ step: FollowupStep) -> String {
        switch step {
        case .openLogin: return L(R.string.followup_action_login)
        case .answer(_, let approve): return L(approve ? R.string.followup_action_approve : R.string.followup_action_deny)
        case .resumeWorker: return L(R.string.followup_action_resume)
        case .drop: return L(R.string.followup_action_drop)
        case .snooze(_, let minutes): return L(R.string.followup_action_snooze, later(minutes))
        case .retry: return L(R.string.followup_action_retry)
        }
    }

    /// The approval card's question ("사이트 열기를 할까요?"): an English "-ing" label reads lower-case inside the sentence, and
    /// Korean gets its object particle.
    static func approvalAsk(_ word: String) -> String {
        L(R.string.chat_approval_ask, askWord(word, language: Strings.shared.code), objectParticle(word))
    }

    nonisolated static func askWord(_ word: String, language: String) -> String {
        guard language == "en" else { return word }
        let first = word.split(separator: " ").first.map(String.init) ?? word
        guard first.count >= 4, first.hasSuffix("ing"), let c0 = first.first, c0.isUppercase,
              !first.dropFirst().contains(where: { $0.isUppercase }) else { return word }
        return word.prefix(1).lowercased() + word.dropFirst()
    }

    nonisolated static func objectParticle(_ word: String) -> String {
        guard let last = word.trimmingCharacters(in: .whitespaces).last(where: { $0.isLetter || $0.isNumber }) else { return "를" }
        if let v = last.unicodeScalars.first?.value, (0xAC00...0xD7A3).contains(v) { return (v - 0xAC00) % 28 != 0 ? "을" : "를" }
        if last.isNumber { return "013678".contains(last) ? "을" : "를" }
        return "aeiouy".contains(last.lowercased()) ? "를" : "을"
    }
}

/// A schedule's first and last day (the server's starts_on / ends_on, which KeloCore's Schedule does not carry).
struct ScheduleSpan: Equatable, Sendable {
    var startsOn: String?
    var endsOn: String?
}
