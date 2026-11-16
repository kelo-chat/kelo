import Foundation
import Testing
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloWork

/// A job waiting to retry says when it runs again: the status line "다시 시도 예정 · 오후 3:40" today, and the day too on another day
/// ("내일 오전 9:10"), in the system's words and the local zone. It is quiet, not a question for the owner.
@Suite struct WorkWaitingTextTests {
    @MainActor @Test func aRetryTodaySaysTheClock() {
        withLanguage("ko") {
            let now = Date()
            let today = Calendar.current.date(bySettingHour: 15, minute: 40, second: 0, of: now)!
            let line = WorkText.jobStatus(Worker.statusWaiting, wakeAt: today.timeIntervalSince1970)
            #expect(line == L(R.string.work_job_retry_at, WorkText.clock(today.timeIntervalSince1970)))
            #expect(line.contains("3:40"))
            #expect(!line.contains("내일"))
            #expect(WorkText.retryAt(today.timeIntervalSince1970, now: now) == WorkText.clock(today.timeIntervalSince1970))
        }
    }

    @MainActor @Test func aRetryTomorrowSaysTomorrowAndTheClock() {
        withLanguage("ko") {
            let now = Date()
            let cal = Calendar.current
            let tomorrow = cal.date(byAdding: .day, value: 1, to: cal.date(bySettingHour: 9, minute: 10, second: 0, of: now)!)!
            let text = WorkText.retryAt(tomorrow.timeIntervalSince1970, now: now)
            #expect(text.contains("내일"))
            #expect(text.contains("9:10"))
            #expect(L(R.string.work_job_retry_at, text) == "다시 시도 예정 · " + text)
        }
    }

    @MainActor @Test func englishSaysTomorrowToo() {
        withLanguage("en") {
            let now = Date()
            let cal = Calendar.current
            let tomorrow = cal.date(byAdding: .day, value: 1, to: cal.date(bySettingHour: 9, minute: 10, second: 0, of: now)!)!
            let text = WorkText.retryAt(tomorrow.timeIntervalSince1970, now: now)
            #expect(text.contains("Tomorrow"))
            #expect(text.contains("9:10"))
        }
    }

    @MainActor @Test func aWaitingJobWithoutAWakeTimeSaysOnlyThatItRetries() {
        withLanguage("ko") {
            #expect(WorkText.jobStatus(Worker.statusWaiting) == L(R.string.work_job_waiting))
        }
    }

    @MainActor @Test func theOtherStatusesKeepTheirWords() {
        withLanguage("ko") {
            #expect(WorkText.jobStatus(Worker.statusBlocked, wakeAt: 1) == L(R.string.work_job_blocked))
            #expect(WorkText.jobStatus(Worker.statusRunning, wakeAt: nil) == L(R.string.agent_status_running))
        }
    }
}
