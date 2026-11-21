import Foundation
import Testing
@testable import KeloCore

/// Port of android agent/AgentRulesTest.kt (DayGroupsTest, RelTimeTest, CronBuilderTest, PendingApprovalTest, AvatarVersionTest).
@Suite struct DayGroupsTests {
    private let today = LocalDay(year: 2026, month: 11, day: 7)
    private func task(_ id: Int64, _ day: String, started: Double = 0) -> AgentTask { AgentTask(id: id, startedAt: started, day: day) }

    @Test func groupsTodayYesterdayAndDatesInOrder() {
        let items = [task(5, "2026-11-07"), task(4, "2026-11-07"), task(3, "2026-11-06"), task(2, "2026-11-01"), task(1, "2025-12-31")]
        let g = DayGroups.group(items, today: today) { DayGroups.dayOf($0) }
        #expect(g.map(\.label) == [.today, .yesterday, .date(LocalDay(year: 2026, month: 11, day: 1)), .date(LocalDay(year: 2025, month: 12, day: 31))])
        #expect(g[0].items.map(\.id) == [5, 4])
        #expect(g[1].items.map(\.id) == [3])
    }

    @Test func keepsItemOrderAndJoinsADayThatComesBack() {
        let items = [task(3, "2026-11-07"), task(2, "2026-11-06"), task(1, "2026-11-07")]
        let g = DayGroups.group(items, today: today) { DayGroups.dayOf($0) }
        #expect(g.count == 2)
        #expect(g[0].items.map(\.id) == [3, 1])
    }

    @Test func missingDayFallsBackToStartTimeThenUnknown() {
        let utc = TimeZone(identifier: "UTC")!
        var c = DateComponents(); c.year = 2026; c.month = 11; c.day = 6; c.hour = 12
        var cal = Calendar(identifier: .gregorian); cal.timeZone = utc
        let started = cal.date(from: c)!.timeIntervalSince1970
        #expect(DayGroups.dayOf(task(1, "", started: started), zone: utc) == LocalDay(year: 2026, month: 11, day: 6))
        #expect(DayGroups.dayOf(task(2, "garbage")) == nil)
        #expect(DayGroups.label(nil, today: today) == .unknown)
        #expect(DayGroups.parseDay("2026-11-05T10:00:00") == LocalDay(year: 2026, month: 11, day: 5))
    }
}

@Suite struct RelTimeTests {
    private let now = 1_800_000_000.0
    private func rel(_ u: RelTime.Unit, _ n: Int64, _ future: Bool) -> RelTime.Rel { RelTime.Rel(unit: u, amount: n, future: future) }

    @Test func nowAndSoon() {
        #expect(RelTime.of(now - 20, now: now) == rel(.now, 0, false))
        #expect(RelTime.of(now + 59, now: now) == rel(.now, 0, true))
    }

    @Test func minutesHoursDays() {
        #expect(RelTime.of(now - 180, now: now) == rel(.minutes, 3, false))
        #expect(RelTime.of(now + 59 * 60, now: now) == rel(.minutes, 59, true))
        #expect(RelTime.of(now + 8 * 3600 + 600, now: now) == rel(.hours, 8, true))
        // 1 h 50 min away reads as two hours
        #expect(RelTime.of(now + 6600, now: now) == rel(.hours, 2, true))
        #expect(RelTime.of(now - 23 * 3600, now: now) == rel(.hours, 23, false))
        #expect(RelTime.of(now + 24 * 3600, now: now) == rel(.days, 1, true))
        #expect(RelTime.of(now - 3 * 86400 - 100, now: now) == rel(.days, 3, false))
    }

    @Test func roundingAtTheEdgeMovesToTheNextUnit() {
        // 59.6 minutes rounds to 60: shown as one hour, never "60 minutes"
        #expect(RelTime.of(now + 59.6 * 60, now: now) == rel(.hours, 1, true))
    }
}

@Suite struct CronBuilderTests {
    private func day(_ y: Int, _ m: Int, _ d: Int) -> LocalDay { LocalDay(year: y, month: m, day: d) }

    @Test func nextRunPrefersTheServerNumber() {
        #expect(ScheduleTimes.nextRunSeconds(Schedule(id: 1, nextRun: "2026-11-08T09:00:00+09:00", nextRunTs: 100.0)) == 100.0)
        #expect(ScheduleTimes.nextRunSeconds(Schedule(id: 1, nextRun: "2026-11-08T09:00:00+09:00")) == 1_794_096_000.0)
        #expect(ScheduleTimes.nextRunSeconds(Schedule(id: 1)) == nil)
    }
}

@Suite struct PendingApprovalTests {
    @Test func readsThePendingList() throws {
        let json = try JSON.parse("""
            {"items":[
              {"type":"approval_request","approval_id":"a1","id":"c1","name":"browser_click","detail":"주문 버튼","label":"사이트에서 누르기",
               "site":"shop.example","can_remember":true,"task_id":7,"expires_at":1,"turn_id":"t","asked_at":12.5,"unattended":true},
              {"type":"approval_request","approval_id":"a2","name":"confirm_purchase","label":"결제",
               "payment":{"site":"shop.example","summary":"책","amount":12000,"currency":"KRW","amount_text":"12,000원"},
               "card":{"id":"k","label":"내 카드","brand":"visa","last4":"1234","auto_pay":false,"needs_card":true}},
              {"type":"approval_request","name":"no id"}
            ]}
            """)
        let list = PendingApproval.parseList(json)
        try #require(list.count == 2)
        let a = list[0]
        #expect(a.id == "a1"); #expect(a.site == "shop.example"); #expect(a.canRemember); #expect(a.unattended)
        #expect(a.askedAt == 12.5)
        #expect(try #require(list[1].event.card).needsCard)
        #expect(list[1].canRemember == false)
        #expect(PendingApproval.parse(try JSON.parse(#"{"type":"text"}"#)) == nil)
    }
}

@Suite struct AvatarVersionTests {
    /// the server's version is a millisecond time: it must fit (an Int overflowed and broke the profile and the status)
    @Test func bigVersionsDecode() throws {
        let d = JSONDecoder()
        #expect(try d.decode(Profile.self, from: Data(#"{"agent_name":"K","avatar_version":1794027761958}"#.utf8)).avatarVersion == 1_794_027_761_958)
        #expect(try d.decode(AgentStatus.self, from: Data(#"{"busy":false,"avatar_version":1794027761958}"#.utf8)).avatarVersion == 1_794_027_761_958)
        #expect(try d.decode(AvatarSaved.self, from: Data(#"{"avatar_version":1794027761958,"mime":"image/jpeg"}"#.utf8)).avatarVersion == 1_794_027_761_958)
    }
}

/// Port of LookbackRulesTest (in android agent/LiveRulesTest.kt). `runWord` (R.string ids) has no Swift counterpart, nor does
/// Lookback.available (the client sets it on a 404), so those checks are left out.
@Suite struct LookbackRulesTests {
    private func r(_ id: Int64, _ status: String, conf: Double = 0.5, date: String? = nil, rule: String? = nil) -> LearnedRule {
        LearnedRule(id: id, rule: rule ?? "규칙 \(id)", evidence: "", date: date ?? "2026-11-0\(id)", confidence: conf, status: status)
    }

    @Test func rulesFallIntoProposedAcceptedAndRejected() {
        let g = LookbackRules.group([
            r(1, "accepted"), r(2, "proposed", conf: 0.4), r(3, "proposed", conf: 0.9), r(4, "rejected", date: "2026-11-01"),
            r(5, "rejected", date: "2026-11-05"), r(6, "weird"), r(7, "proposed", rule: "  "),
        ])
        #expect(g.proposed.map(\.id) == [3, 2])
        #expect(g.accepted.map(\.id) == [1])
        #expect(g.rejected.map(\.id) == [5, 4])
    }

    @Test func inUseMeansAcceptedOrASureProposal() {
        #expect(LookbackRules.inUse(r(1, "accepted", conf: 0.1)))
        #expect(LookbackRules.inUse(r(2, "proposed", conf: 0.8)))
        #expect(!LookbackRules.inUse(r(3, "proposed", conf: 0.79)))
        #expect(!LookbackRules.inUse(r(4, "rejected", conf: 1.0)))
    }

    @Test func theLastRunBecomesAPlainSentenceFromItsFieldsNeverTheLogText() throws {
        let log = "2 new, 0 updated, 0 dropped; 사용자가 승인을 3회 거절함"
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "done", added: 2, reason: log)) == .done(newRules: 2))
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "done")) == .done(newRules: 0))
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "skipped", code: "no_messages")) == .noChat)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "skipped", code: "switched_off")) == .switchedOff)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "skipped", code: "in_use")) == .inUse)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "skipped")) == .skipped)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "failed", code: "model_error")) == .failedModel)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "failed", code: "bad_output")) == .failedAnswer)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "failed")) == .failed)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "pending", code: "no_model")) == .noModel)
        #expect(LookbackRules.runLine(LookbackRun(at: 1, status: "busy")) == nil)
        let run = try JSONDecoder().decode(LookbackRun.self, from: Data(
            #"{"at":2.0,"status":"done","code":"","added":2,"updated":0,"dropped":0,"goals":1,"held":1,"reason":"2 new","changed":true}"#.utf8))
        #expect(run.added == 2 && run.held == 1)
    }

    @Test func whenTheRunHappenedInWords() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "Asia/Seoul")!
        func at(_ d: Int, _ h: Int) -> Date { cal.date(from: DateComponents(year: 2026, month: 11, day: d, hour: h, minute: 30))! }
        let now = at(8, 21)
        #expect(LookbackRules.runWhen(at(8, 3).timeIntervalSince1970, now: now, calendar: cal) == .lastNight)
        #expect(LookbackRules.runWhen(at(7, 23).timeIntervalSince1970, now: now, calendar: cal) == .lastNight)
        #expect(LookbackRules.runWhen(at(8, 14).timeIntervalSince1970, now: now, calendar: cal) == .today)
        #expect(LookbackRules.runWhen(at(7, 10).timeIntervalSince1970, now: now, calendar: cal) == .day)
        #expect(LookbackRules.runWhen(at(5, 3).timeIntervalSince1970, now: now, calendar: cal) == .day)
    }

    @Test func aRuleJustKeptOrDiscardedStaysAmongTheSuggestionsForTheUndo() {
        let rules = [r(1, "accepted", conf: 0.9), r(2, "proposed", conf: 0.6), r(3, "rejected")]
        let g = LookbackRules.group(rules, justDecided: [1, 3])
        #expect(g.proposed.map(\.id).sorted() == [1, 2, 3])
        #expect(g.accepted.isEmpty && g.rejected.isEmpty)
        #expect(LookbackRules.group(rules, justDecided: []).accepted.map(\.id) == [1])
        #expect(LookbackRules.undoSeconds == 5)
    }

}

/// A blocked run's one line from the server (`block_line`) is decoded; a job that is not blocked has none.
@Suite struct ScheduleBlockLineTests {
    @Test func blockLineIsDecodedForAJobThatWaits() throws {
        let json = #"{"id": 3, "name": "메일", "prompt": "check", "block_line": "Waiting for Gmail to be connected.", "paused": false}"#
        let s = try JSONDecoder().decode(Schedule.self, from: Data(json.utf8))
        #expect(s.blockLine == "Waiting for Gmail to be connected.")
        #expect(s.paused == false)
        let plain = try JSONDecoder().decode(Schedule.self, from: Data(#"{"id": 4, "name": "x", "prompt": "y"}"#.utf8))
        #expect(plain.blockLine == nil)
    }
}
