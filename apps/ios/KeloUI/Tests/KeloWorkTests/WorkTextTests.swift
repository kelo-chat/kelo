import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloWork

/// The work page's words: schedules are always said in words (never their cron expression), and the server's lists are read
/// with the extra fields KeloCore's models leave out.
@Suite struct WorkTextTests {
    @MainActor @Test func schedulesAreNeverShownAsCron() {
        withLanguage("en") {
        let cases: [Schedule] = [
            Schedule(id: 1, cron: "0 9 * * *"), Schedule(id: 2, cron: "30 7 * * 1-5"), Schedule(id: 3, cron: "0 10 * * 0,6"),
            Schedule(id: 4, cron: "15 18 * * 1,3"), Schedule(id: 5, cron: "0 9 15 * *"), Schedule(id: 6, cron: "0 8 25 12 *"),
            Schedule(id: 7, cron: "0 */3 * * *"), Schedule(id: 8, kind: "interval", everyMinutes: 90),
            Schedule(id: 9, kind: "once", runAt: "2026-11-09T07:30:00+09:00"),
            Schedule(id: 10, cron: "0 9 1-7 * 1", description: "0 9 1-7 * 1"),
            Schedule(id: 11, cron: "0 9 1-7 * 1", description: "First Monday of the month at 9"),
        ]
        for s in cases {
            let text = WorkText.schedule(s)
            #expect(!text.isEmpty)
            #expect(!PlanCron.looksLikeCron(text), "cron shown for \(s.id): \(text)")
            #expect(!text.contains("*"))
        }
        #expect(WorkText.schedule(cases[10]) == "First Monday of the month at 9")
        #expect(WorkText.schedule(cases[9]) == L(R.string.sched_text_rule))
        }
    }

    @MainActor @Test func everyLanguageSaysTheRepeat() {
        for lang in Languages.all {
            withLanguage(lang.code) {
                for r: PlanRepeat in [.daily(hour: 9, minute: 0), .weekly(days: [1, 3], hour: 18, minute: 15), .yearly(month: 12, day: 25, hour: 8, minute: 0),
                                      .interval(minutes: 90), .everyHours(hours: 3, minute: 0)] {
                    let t = WorkText.repeatText(r)
                    #expect(!t.isEmpty && !t.contains("%"), "\(lang.code): \(t)")
                }
            }
        }
    }

    @MainActor @Test func intervalsAndRelativeTimes() {
        withLanguage("en") {
            #expect(WorkText.interval(1440) == P(R.plurals.work_every_days, 1, 1))
            #expect(WorkText.interval(120) == P(R.plurals.agent_repeat_every_hours, 2, 2))
            #expect(WorkText.interval(45) == P(R.plurals.work_every_minutes, 45, 45))
            #expect(WorkText.rel(1_000, now: 1_000) == L(R.string.agent_rel_now))
            #expect(WorkText.rel(1_000 + 3 * 3600, now: 1_000) == P(R.plurals.agent_rel_hours_future, 3, 3))
            #expect(WorkText.later(120) == P(R.plurals.agent_rel_hours_future, 2, 2))
        }
    }

    @Test func approvalQuestionWording() {
        #expect(AskWords.askWord("Opening a site", language: "en") == "opening a site")
        #expect(AskWords.askWord("GitHub sign-in", language: "en") == "GitHub sign-in")
        #expect(AskWords.askWord("Opening", language: "ko") == "Opening")
        #expect(AskWords.objectParticle("사이트 열기") == "를")
        #expect(AskWords.objectParticle("로그인 정보 입력") == "을")
        #expect(AskWords.objectParticle("GitHub") == "을")
        #expect(AskWords.objectParticle("Google Drive") == "를")
    }

    @Test func scheduleListKeepsFirstAndLastDays() throws {
        let raw = try JSON.parse("""
        {"items":[{"id":1,"name":"a","prompt":"p","kind":"cron","cron":"0 9 * * *","enabled":true,"starts_on":"2026-12-01","ends_on":"2026-12-31"},
                  {"id":2,"name":"b","prompt":"p","kind":"once","run_at":"2026-11-09T07:30","enabled":true}],"timezone":"Asia/Seoul"}
        """)
        let book = WorkModel.book(raw)
        #expect(book.items.map(\.id) == [1, 2])
        #expect(book.timezone == "Asia/Seoul")
        #expect(book.spans[1] == ScheduleSpan(startsOn: "2026-12-01", endsOn: "2026-12-31"))
        #expect(book.spans[2] == ScheduleSpan(startsOn: nil, endsOn: nil))
    }

    @Test func followupButtonEmphasis() {
        #expect(FollowupButtonsView.primary(.resumeWorker(workerId: "w")))
        #expect(FollowupButtonsView.primary(.answer(approvalId: "a", approve: true)))
        #expect(!FollowupButtonsView.primary(.answer(approvalId: "a", approve: false)))
        #expect(!FollowupButtonsView.primary(.drop(followupId: "f")))
    }

    @Test func thumbnailsDecode() {
        #expect(BrowserImage.decode(nil) == nil)
        #expect(BrowserImage.decode("not base64!!") == nil)
    }
}
