import Foundation
import Testing
@testable import KeloCore

/// Port of android chat/AgentActivityTest.kt.
@Suite struct AgentActivityTests {
    private func shot(_ id: String, status: String) -> TurnEvent {
        .browser(.init(id: id, title: "", url: "", host: "", status: status, image: nil, width: 0, height: 0, at: 0))
    }

    @Test func theFollowedReplyWinsOverThePolledStatus() {
        #expect(AgentActivityRules.shown(following: true, turnLine: "사이트 여는 중", polled: "다른 일") == "사이트 여는 중")
        #expect(AgentActivityRules.shown(following: true, turnLine: nil, polled: "다른 일") == nil)
        #expect(AgentActivityRules.shown(following: false, turnLine: "사이트 여는 중", polled: "다른 일") == "다른 일")
        #expect(AgentActivityRules.shown(following: false, turnLine: nil, polled: "  ") == nil)
    }

    @Test func statusLineOnlyWhileBusy() {
        #expect(AgentActivityRules.line(AgentStatus(busy: false, status: "x")) == nil)
        #expect(AgentActivityRules.line(AgentStatus(busy: true, status: "입력하는 중", title: "t")) == "입력하는 중")
        #expect(AgentActivityRules.line(AgentStatus(busy: true, status: nil, title: "매일 뉴스 요약")) == "매일 뉴스 요약")
        #expect(AgentActivityRules.line(AgentStatus(busy: true)) == nil)
    }

    @Test func turnEventsMoveTheLine() {
        var line: String? = nil
        line = AgentActivityRules.next(line, .toolStarted(.init(id: "1", name: "read_skill", arguments: [:], label: "안내서 확인", hidden: true)))
        #expect(line == nil)
        line = AgentActivityRules.next(line, .toolStarted(.init(id: "2", name: "browser_open", arguments: [:], label: "사이트 여는 중")))
        #expect(line == "사이트 여는 중")
        line = AgentActivityRules.next(line, .toolStarted(.init(id: "3", name: "x", arguments: [:])))   // no label: keep
        #expect(line == "사이트 여는 중")
        line = AgentActivityRules.next(line, shot("2", status: "구매 진행 중"))
        #expect(line == "구매 진행 중")
        line = AgentActivityRules.next(line, .thinking)
        #expect(line == "구매 진행 중")
        line = AgentActivityRules.next(line, .text("다 됐어요"))
        #expect(line == nil)
    }

    @Test func followedReplyKindFollowsItsTools() {
        var k = TurnKind.thinking
        k = AgentActivityRules.turnKind(k, .toolStarted(.init(id: "1", name: "read_file", arguments: [:], label: "파일 읽는 중")))
        #expect(k == .reading)
        k = AgentActivityRules.turnKind(k, .toolStarted(.init(id: "2", name: "browser_open", arguments: [:], label: "사이트 여는 중")))
        #expect(k == .browser)
        k = AgentActivityRules.turnKind(k, .thinking)
        #expect(k == .browser)
        k = AgentActivityRules.turnKind(k, .toolStarted(.init(id: "3", name: "read_skill", arguments: [:], label: "", hidden: true)))   // hidden: keep
        #expect(k == .browser)
        k = AgentActivityRules.turnKind(k, .toolStarted(.init(id: "4", name: "schedule_create", arguments: [:], label: "일정 만드는 중")))
        #expect(k == .tool)
        k = AgentActivityRules.turnKind(k, shot("5", status: "사이트 보는 중"))
        #expect(k == .browser)
        k = AgentActivityRules.turnKind(k, .text("다 됐어요"))
        #expect(k == .thinking)
        #expect(AgentActivityRules.toolKind("memory_search") == .reading)
        #expect(AgentActivityRules.toolKind("email_read") == .reading)
    }

    @Test func polledRunBrowsesOnlyInARecentOrMovingWorkingTab() {
        let busy = AgentStatus(busy: true)
        let now = 1_000_000.0
        let recent = BrowserTabBrief(id: "task:a", status: "working", lastActivity: now - 5)
        let stale = BrowserTabBrief(id: "task:a", status: "working", lastActivity: now - 600)
        #expect(AgentActivityRules.polledBrowsing(busy, tabs: [recent], before: [:], nowS: now) == true)
        #expect(AgentActivityRules.polledBrowsing(busy, tabs: [stale], before: [:], nowS: now) == false)
        // the phone's clock is off, but the tab's last action moved since the last poll
        #expect(AgentActivityRules.polledBrowsing(busy, tabs: [stale], before: ["task:a": now - 700], nowS: now) == true)
        #expect(AgentActivityRules.polledBrowsing(busy, tabs: [stale], before: ["task:a": now - 600], nowS: now) == false)
        // an idle tab, or nothing running
        var idleTab = recent
        idleTab.status = "idle"
        #expect(AgentActivityRules.polledBrowsing(busy, tabs: [idleTab], before: [:], nowS: now) == false)
        #expect(AgentActivityRules.polledBrowsing(AgentStatus(busy: false), tabs: [recent], before: [:], nowS: now) == false)
        #expect(AgentActivityRules.polledBrowsing(nil, tabs: [recent], before: [:], nowS: now) == false)
    }

    private func mood(_ m: AgentActivityRules.MoodInput) -> KeloMood { AgentActivityRules.mood(m) }
    private func copy(_ m: AgentActivityRules.MoodInput, _ change: (inout AgentActivityRules.MoodInput) -> Void) -> AgentActivityRules.MoodInput {
        var c = m
        change(&c)
        return c
    }
    private let busy = AgentStatus(busy: true)
    private let asking = AgentStatus(busy: true, pendingApprovals: 1)

    @Test func moodPriority() {
        let all = AgentActivityRules.MoodInput(
            offline: true, status: asking, stuck: true, listening: true, following: true, turnKind: .browser,
            polledBrowsing: true, sinceDoneMs: 100, night: true)
        // each step down the list once the ones above it are gone
        #expect(mood(all) == .offline)
        #expect(mood(copy(all) { $0.offline = false }) == .approval)
        let noAsk = copy(all) { $0.offline = false; $0.status = busy }
        #expect(mood(noAsk) == .error)
        #expect(mood(copy(noAsk) { $0.stuck = false }) == .listening)
        let work = copy(noAsk) { $0.stuck = false; $0.listening = false }
        #expect(mood(work) == .browser)
        #expect(mood(copy(work) { $0.turnKind = .reading }) == .reading)
        #expect(mood(copy(work) { $0.turnKind = .tool }) == .working)
        #expect(mood(copy(work) { $0.turnKind = .thinking }) == .thinking)
        // not following: the poll says
        #expect(mood(copy(work) { $0.following = false }) == .browser)
        #expect(mood(copy(work) { $0.following = false; $0.polledBrowsing = false }) == .working)
        let rest = copy(work) { $0.following = false; $0.status = AgentStatus(busy: false) }
        #expect(mood(rest) == .done)
        #expect(mood(copy(rest) { $0.sinceDoneMs = nil }) == .sleeping)
        #expect(mood(copy(rest) { $0.sinceDoneMs = AgentActivityRules.doneMs }) == .sleeping)   // the cheer is over
        #expect(mood(copy(rest) { $0.sinceDoneMs = nil; $0.night = false }) == .idle)
        #expect(mood(AgentActivityRules.MoodInput()) == .idle)
    }

    @Test func stuckOnTheUser() {
        let now = 2_000_000.0
        #expect(AgentActivityRules.needsUser(newest: TaskBrief(id: 1, status: "failed", seen: false), waiting: [], nowS: now) == true)
        #expect(AgentActivityRules.needsUser(newest: TaskBrief(id: 1, status: "failed", seen: true), waiting: [], nowS: now) == false)   // looked at
        #expect(AgentActivityRules.needsUser(newest: TaskBrief(id: 1, status: "done", seen: false), waiting: [], nowS: now) == false)
        #expect(AgentActivityRules.needsUser(newest: nil, waiting: [WorkerBrief(id: "w", status: "needs_input", updatedAt: now - 600)], nowS: now) == true)
        #expect(AgentActivityRules.needsUser(newest: nil, waiting: [WorkerBrief(id: "w", status: "needs_input", updatedAt: now - 13 * 3600)], nowS: now) == false)   // old
        #expect(AgentActivityRules.needsUser(newest: nil, waiting: [WorkerBrief(id: "w", status: "running", updatedAt: now)], nowS: now) == false)
    }

    @Test func finishedOnlyWhenARunEndsWell() {
        let idle = AgentStatus(busy: false)
        #expect(AgentActivityRules.finished(before: busy, now: idle, newest: TaskBrief(id: 1, status: "done")) == true)
        #expect(AgentActivityRules.finished(before: nil, now: idle, newest: TaskBrief(id: 1, status: "done")) == false)     // app start
        #expect(AgentActivityRules.finished(before: idle, now: idle, newest: TaskBrief(id: 1, status: "done")) == false)
        #expect(AgentActivityRules.finished(before: busy, now: idle, newest: TaskBrief(id: 1, status: "failed")) == false)
        #expect(AgentActivityRules.finished(before: busy, now: busy, newest: TaskBrief(id: 1, status: "done")) == false)
    }

    @Test func lateNight() {
        #expect(AgentActivityRules.isNight(hour: 23) == true)
        #expect(AgentActivityRules.isNight(hour: 3) == true)
        #expect(AgentActivityRules.isNight(hour: 7) == false)
        #expect(AgentActivityRules.isNight(hour: 14) == false)
    }
    // ---- sleep only after a quiet while (the owner saw it fall asleep right after each reply at night)
    private let quietNight = AgentActivityRules.MoodInput(status: AgentStatus(busy: false), night: true)
    private let sleepAfter = AgentActivityRules.sleepAfterIdleMs

    @Test func atNightAFinishedReplyCheersThenRestsAwake() {
        #expect(mood(copy(quietNight) { $0.sinceDoneMs = 0; $0.idleMs = 0 }) == .done)
        #expect(mood(copy(quietNight) { $0.idleMs = AgentActivityRules.doneMs }) == .idle)
        #expect(mood(copy(quietNight) { $0.idleMs = 60_000 }) == .idle)
    }

    @Test func sleepsOnlyAfterFiveQuietMinutes() {
        #expect(sleepAfter == 5 * 60_000)
        #expect(mood(copy(quietNight) { $0.idleMs = 4 * 60_000 + 59_000 }) == .idle)   // 4:59
        #expect(mood(copy(quietNight) { $0.idleMs = sleepAfter }) == .sleeping)        // 5:00
        #expect(mood(copy(quietNight) { $0.idleMs = 3 * 3_600_000 }) == .sleeping)
        #expect(mood(copy(quietNight) { $0.idleMs = nil }) == .sleeping)              // nothing since launch
    }

    @Test func typingOrANewMessageWakesIt() {
        let started = 1_000.0
        let later = started + Double(sleepAfter) / 1000 + 30
        #expect(mood(copy(quietNight) { $0.idleMs = AgentActivityRules.idleMs(now: later, activeAt: started) }) == .sleeping)
        // a key typed, or a message sent or come in, moves the last activity to now
        #expect(mood(copy(quietNight) { $0.idleMs = AgentActivityRules.idleMs(now: later, activeAt: later) }) == .idle)
        #expect(AgentActivityRules.idleMs(now: later, activeAt: nil) == nil)
        #expect(AgentActivityRules.idleMs(now: 5, activeAt: 10) == 0)   // a clock step back is not negative
    }

    @Test func daytimeIsNeverSleeping() {
        let day = copy(quietNight) { $0.night = false }
        #expect(mood(copy(day) { $0.idleMs = nil }) == .idle)
        #expect(mood(copy(day) { $0.idleMs = 10 * 3_600_000 }) == .idle)
        let utc = TimeZone(identifier: "UTC")!
        let noon = Date(timeIntervalSince1970: 1_793_620_800)   // 2026-11-02 12:00 UTC
        #expect(AgentActivityRules.nightAt(noon, local: utc, hours: nil) == false)
        #expect(AgentActivityRules.nightAt(noon.addingTimeInterval(11 * 3600), local: utc, hours: nil) == true)   // 23:00
    }

    @Test func theOwnersContactHoursAreTheNightWhenKnown() throws {
        let h = SleepHours(wakeStart: "09:00", wakeEnd: "01:30", hoursSource: "learned", timezone: "Asia/Seoul")
        #expect(AgentActivityRules.wakeWindow(h).map { [$0.start, $0.end] } == [9 * 60, 90])
        #expect(AgentActivityRules.isNight(minuteOfDay: 23 * 60 + 30, hours: h) == false)   // the fallback would sleep at 23:30
        #expect(AgentActivityRules.isNight(minuteOfDay: 60, hours: h) == false)
        #expect(AgentActivityRules.isNight(minuteOfDay: 90, hours: h) == true)              // the end is not included
        #expect(AgentActivityRules.isNight(minuteOfDay: 8 * 60, hours: h) == true)          // the fallback is awake at 08:00
        #expect(AgentActivityRules.isNight(minuteOfDay: 9 * 60, hours: h) == false)
        var manual = h
        manual.wakeStart = "08:00"; manual.wakeEnd = "21:00"; manual.hoursSource = "manual"
        #expect(AgentActivityRules.isNight(minuteOfDay: 21 * 60 + 5, hours: manual) == true)
        #expect(AgentActivityRules.isNight(minuteOfDay: 12 * 60, hours: manual) == false)
        var allDay = manual
        allDay.wakeEnd = "08:00"
        #expect(AgentActivityRules.isNight(minuteOfDay: 3 * 60, hours: allDay) == false)   // equal ends: awake all day
        // the server's untouched default, a missing answer or a bad time: 23:00-07:00
        let untouched = SleepHours(wakeStart: "07:00", wakeEnd: "22:00", hoursSource: "default", timezone: "Asia/Seoul")
        #expect(AgentActivityRules.wakeWindow(untouched) == nil)
        #expect(AgentActivityRules.isNight(minuteOfDay: 22 * 60 + 30, hours: untouched) == false)
        #expect(AgentActivityRules.isNight(minuteOfDay: 23 * 60, hours: nil) == true)
        var bad = h
        bad.wakeEnd = "25:00"
        #expect(AgentActivityRules.wakeWindow(bad) == nil)
        #expect(AgentActivityRules.minutesOf("+1:30") == nil)
        #expect(AgentActivityRules.minutesOf(" 7:05 ") == 7 * 60 + 5)
        // read in the owner's time zone: 15:00 UTC is 00:00 in Seoul, inside 09:00-01:30
        let utc = TimeZone(identifier: "UTC")!
        let t = Date(timeIntervalSince1970: 1_793_631_600)   // 2026-11-02 15:00 UTC
        #expect(AgentActivityRules.nightAt(t, local: utc, hours: h) == false)
        #expect(AgentActivityRules.nightAt(t.addingTimeInterval(2 * 3600), local: utc, hours: h) == true)   // 02:00 in Seoul
        // the JSON of GET /api/proactive
        let json = #"{"enabled":true,"wake_start":"09:00","wake_end":"01:30","hours_source":"learned","timezone":"Asia/Seoul","awake":true}"#
        #expect(try JSONDecoder().decode(SleepHours.self, from: Data(json.utf8)) == h)
    }
}

/// Android AgentActivityTest.theNightFollowsTheDayTypeWeekendsAndHolidays.
@Suite struct NightByDayTypeTests {
    let h = SleepHours(wakeStart: "07:00", wakeEnd: "23:00", hoursSource: "manual", timezone: "Asia/Seoul",
                       weekendStart: "10:00", weekendEnd: "01:00", weekendSource: "learned", dayTypes: ["2026-11-11": "weekend"])

    func at(_ local: String) -> Date {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "Asia/Seoul")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm"
        return f.date(from: local)!
    }

    @Test func theNightFollowsTheDayTypeWeekendsAndHolidays() throws {
        let utc = TimeZone(identifier: "UTC")!
        #expect(!AgentActivityRules.nightAt(at("2026-11-09T08:00"), local: utc, hours: h))   // Monday: the weekday window
        #expect(AgentActivityRules.nightAt(at("2026-11-14T08:00"), local: utc, hours: h))    // Saturday 08:00: before the weekend start
        #expect(!AgentActivityRules.nightAt(at("2026-11-14T10:30"), local: utc, hours: h))
        #expect(AgentActivityRules.nightAt(at("2026-11-11T08:00"), local: utc, hours: h))    // a holiday the server named
        #expect(!AgentActivityRules.nightAt(at("2026-11-15T00:30"), local: utc, hours: h))   // Saturday night past midnight
        #expect(AgentActivityRules.nightAt(at("2026-11-14T00:30"), local: utc, hours: h))    // Friday night past 23:00
        var follows = h
        follows.weekendSource = "default"; follows.dayTypes = [:]
        #expect(!AgentActivityRules.nightAt(at("2026-11-14T08:00"), local: utc, hours: follows))
        #expect(AgentActivityRules.dayType(nil, day: "2026-11-15", weekday: 1) == "weekend")
        #expect(AgentActivityRules.dayType(h, day: "2026-11-12", weekday: 5) == "weekday")
        let parsed = try JSON.decode(SleepHours.self, from: try JSON.parse("""
            {"wake_start":"07:00","wake_end":"23:00","hours_source":"manual","timezone":"Asia/Seoul","weekend_start":"10:00",
            "weekend_end":"01:00","weekend_source":"learned","day_types":{"2026-11-11":"weekend"},"day_type":"weekday"}
        """))
        #expect(parsed == h)
    }
}
