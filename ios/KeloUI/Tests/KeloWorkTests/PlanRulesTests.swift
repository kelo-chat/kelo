import Testing
import Foundation
import KeloCore
@testable import KeloWork

/// Ports of the Android app's PlanRulesTest (agent/AgentRulesTest.kt) plus the editor's own rules.
@Suite struct PlanRulesTests {
    // Saturday 2026-11-07 08:00
    let now = LocalMoment(day: LocalDay(year: 2026, month: 11, day: 7), hour: 8, minute: 0)
    var today: LocalDay { now.day }
    func day(_ y: Int, _ m: Int, _ d: Int) -> LocalDay { LocalDay(year: y, month: m, day: d) }
    func plan(_ d: LocalDay, _ h: Int, _ m: Int, _ f: Freq = .none, days: Set<Int> = [], until: LocalDay? = nil) -> SchedulePlan {
        SchedulePlan(date: d, hour: h, minute: m, freq: f, days: days, until: until)
    }

    @Test func cronAndRunAt() {
        let d = day(2026, 11, 8)
        #expect(PlanRules.cron(plan(d, 9, 0)) == nil)
        #expect(PlanRules.runAt(plan(d, 9, 0)) == "2026-11-08T09:00")
        #expect(PlanRules.cron(plan(d, 9, 0, .daily)) == "0 9 * * *")
        #expect(PlanRules.cron(plan(d, 7, 30, .weekdays)) == "30 7 * * 1-5")
        #expect(PlanRules.cron(plan(d, 10, 0, .weekends)) == "0 10 * * 0,6")
        #expect(PlanRules.cron(plan(d, 18, 15, .weekly, days: [3, 1])) == "15 18 * * 1,3")
        #expect(PlanRules.cron(plan(d, 9, 0, .monthly)) == "0 9 8 * *")
        #expect(PlanRules.cron(plan(d, 9, 0, .yearly)) == "0 9 8 11 *")
    }

    @Test func firstAndLastDays() {
        #expect(PlanRules.startsOn(plan(today, 9, 0, .daily), now: now) == "")
        #expect(PlanRules.startsOn(plan(today.adding(days: 1), 7, 0, .daily), now: now) == "")
        #expect(PlanRules.startsOn(plan(day(2026, 11, 20), 9, 0, .daily), now: now) == "2026-11-20")
        #expect(PlanRules.startsOn(plan(day(2026, 11, 20), 9, 0), now: now) == "")
        #expect(PlanRules.endsOn(plan(today, 9, 0, .daily, until: day(2026, 12, 31))) == "2026-12-31")
        #expect(PlanRules.endsOn(plan(today, 9, 0, .none, until: day(2026, 12, 31))) == "")
    }

    @Test func problems() {
        #expect(PlanRules.problem(plan(today, 9, 0, .weekly), now: now) == .noDay)
        #expect(PlanRules.problem(plan(today, 7, 0), now: now) == .past)
        #expect(PlanRules.problem(plan(today, 8, 0), now: now) == .past)
        #expect(PlanRules.problem(plan(today, 9, 0), now: now) == nil)
        #expect(PlanRules.problem(plan(day(2026, 12, 1), 9, 0, .daily, until: day(2026, 11, 30)), now: now) == .endBeforeStart)
    }

    @Test func savedSchedulesInTheSimpleForm() {
        let daily = Schedule(id: 1, kind: "cron", cron: "0 9 * * *", nextRun: "2026-11-08T09:00+09:00")
        #expect(PlanRules.fromSchedule(daily, startsOn: nil, endsOn: "2026-12-31", today: today)
                == plan(day(2026, 11, 8), 9, 0, .daily, until: day(2026, 12, 31)))
        #expect(PlanRules.fromSchedule(Schedule(id: 2, cron: "0 10 * * 6,0"), startsOn: nil, endsOn: nil, today: today)?.freq == .weekends)
        let monthly = PlanRules.fromSchedule(Schedule(id: 3, cron: "0 9 15 * *"), startsOn: nil, endsOn: nil, today: today)!
        #expect(monthly.freq == .monthly && monthly.date == day(2026, 11, 15))
        let yearly = PlanRules.fromSchedule(Schedule(id: 4, cron: "0 8 25 12 *"), startsOn: nil, endsOn: nil, today: today)!
        #expect(yearly.freq == .yearly && yearly.date == day(2026, 12, 25))
        let once = PlanRules.fromSchedule(Schedule(id: 5, kind: "once", runAt: "2026-11-09T07:30:00+09:00"), startsOn: nil, endsOn: nil, today: today)
        #expect(once == plan(day(2026, 11, 9), 7, 30))
        #expect(PlanRules.fromSchedule(Schedule(id: 6, kind: "window", cron: "0 9 * * *", windowMinutes: 15), startsOn: nil, endsOn: nil, today: today)?.freq == .daily)
        // what the form cannot show stays as words, with a way to replace it
        #expect(PlanRules.fromSchedule(Schedule(id: 7, kind: "interval", everyMinutes: 90), startsOn: nil, endsOn: nil, today: today) == nil)
        #expect(PlanRules.fromSchedule(Schedule(id: 8, cron: "0 */3 * * *"), startsOn: nil, endsOn: nil, today: today) == nil)
        #expect(PlanRules.fromSchedule(Schedule(id: 9, cron: "0 9 1-7 * 1"), startsOn: nil, endsOn: nil, today: today) == nil)
        let replaced = PlanRules.replacing(Schedule(id: 9, cron: "0 9 1-7 * 1", nextRun: "2026-12-07T09:00+09:00"), endsOn: nil, today: today)
        #expect(replaced == plan(today, 9, 0, .daily))
        // a first day still ahead is where the repeat starts
        let later = PlanRules.fromSchedule(Schedule(id: 10, cron: "0 9 * * *"), startsOn: "2026-12-01", endsOn: nil, today: today)
        #expect(later?.date == day(2026, 12, 1))
    }

    @Test func weekOrderAndChoices() {
        #expect(PlanRules.weekOrder(first: 1) == [1, 2, 3, 4, 5, 6, 0])
        #expect(PlanRules.weekOrder(first: 0) == [0, 1, 2, 3, 4, 5, 6])
        #expect(PlanRules.weekOrder(first: 6) == [6, 0, 1, 2, 3, 4, 5])
        let wed = plan(day(2026, 11, 11), 9, 0)
        #expect(PlanRules.withFreq(wed, .weekly).days == [3])
        #expect(PlanRules.withFreq(wed, .weekly).freq == .weekly)
        var two = wed; two.days = [1, 5]
        #expect(PlanRules.withFreq(two, .weekly).days == [1, 5])
        #expect(PlanRules.withFreq(wed, .monthly).days.isEmpty)
        #expect(PlanRules.withFreq(plan(day(2026, 11, 8), 9, 0), .weekly).days == [0])
        #expect(PlanRules.toggleDay([1], 3) == [1, 3])
        #expect(PlanRules.toggleDay([1, 3], 1) == [3])
        #expect(PlanRules.toggleDay([3], 3) == [3])
        #expect(PlanRules.defaultUntil(start: day(2026, 11, 9), today: today) == day(2026, 12, 9))
        #expect(PlanRules.defaultUntil(start: day(2026, 10, 1), today: today) == day(2026, 12, 7))
        #expect(PlanRules.circleLabel(short: "월", narrow: "월") == "월")
        #expect(PlanRules.circleLabel(short: "Mon", narrow: "M") == "Mon")
        #expect(PlanRules.circleLabel(short: "lun.", narrow: "L") == "lun")
        #expect(PlanRules.circleLabel(short: "Thứ 2", narrow: "T2") == "T2")
    }

    @Test func runsOnAndFirstDay() {
        let p = plan(today, 9, 0, .weekdays)
        #expect(!PlanRules.runsOn(p, today))                         // Saturday
        #expect(PlanRules.runsOn(p, day(2026, 11, 9)))               // Monday
        #expect(PlanRules.firstDay(p, from: today, now: now) == day(2026, 11, 9))
        #expect(PlanRules.firstDay(plan(today, 9, 0, .daily), from: today, now: now) == today)
        #expect(PlanRules.firstDay(plan(today, 7, 0, .daily), from: today, now: now) == today.adding(days: 1))
        #expect(day(2026, 11, 8).cronWeekday == 0)
        #expect(day(2026, 11, 7).cronWeekday == 6)
    }

    @Test func requestBodies() {
        let p = plan(day(2026, 11, 20), 9, 0, .daily, until: day(2026, 12, 31))
        let created = PlanRules.body(id: nil, name: " Morning ", prompt: " news ", plan: p, timezone: "Asia/Seoul", now: now).objectValue!
        #expect(created["name"] == .string("Morning"))
        #expect(created["prompt"] == .string("news"))
        #expect(created["cron"] == .string("0 9 * * *"))
        #expect(created["window_minutes"] == .number(0))
        #expect(created["starts_on"] == .string("2026-11-20"))
        #expect(created["ends_on"] == .string("2026-12-31"))
        #expect(created["timezone"] == .string("Asia/Seoul"))
        #expect(created["run_at"] == nil)
        let changed = PlanRules.body(id: 3, name: "x", prompt: "y", plan: plan(day(2026, 11, 9), 7, 5), timezone: nil, now: now).objectValue!
        #expect(changed["run_at"] == .string("2026-11-09T07:05"))
        #expect(changed["cron"] == .null && changed["every_minutes"] == .null)
        #expect(changed["timezone"] == nil)
        let renamed = PlanRules.body(id: 3, name: "x", prompt: "y", plan: nil, timezone: nil, now: now).objectValue!
        #expect(Set(renamed.keys) == ["name", "prompt"])
    }

    @Test func cronParsingAndNeverShowingIt() {
        #expect(PlanCron.parse("0 8 25 12 *") == .yearly(month: 12, day: 25, hour: 8, minute: 0))
        #expect(PlanCron.parse("@yearly") == .yearly(month: 1, day: 1, hour: 0, minute: 0))
        #expect(PlanCron.parse("30 7 * * mon-fri") == .weekdays(hour: 7, minute: 30))
        #expect(PlanCron.parse("0 9 * * 0-6") == .daily(hour: 9, minute: 0))
        #expect(PlanCron.parse("0 */4 * * *") == .everyHours(hours: 4, minute: 0))
        #expect(PlanCron.parse("0 9 1-7 * 1") == .custom(cron: "0 9 1-7 * 1"))
        for t in ["0 9 * * *", "@daily", "*/5 * * * *", "Custom schedule (0 9 1-7 * 1)"] { #expect(PlanCron.looksLikeCron(t), "\(t)") }
        for t in ["Every Monday at 9", "매주 월요일 오전 9시"] { #expect(!PlanCron.looksLikeCron(t), "\(t)") }
    }
}
