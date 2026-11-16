import Foundation
import Testing
@testable import KeloCore

/// A job scheduled to retry: GET /api/workers gives status "waiting" with `wake_at` (epoch seconds). It is not a question for the owner,
/// not active and not resumable by hand; it runs again by itself.
@Suite struct WorkerWaitingTests {
    private func worker(_ json: String) throws -> Worker {
        try JSONDecoder().decode(Worker.self, from: Data(json.utf8))
    }

    @Test func aWaitingJobKeepsItsWakeTime() throws {
        let w = try worker(#"{"id":"w1","kind":"browser","title":"t","status":"waiting","started_at":1,"updated_at":2,"wake_at":1790000000.5,"waiting_for":"retry"}"#)
        #expect(w.status == Worker.statusWaiting)
        #expect(w.wakeAt == 1_790_000_000.5)
        #expect(w.waiting)
        #expect(w.needsYou == false)
        #expect(w.running == false)
        #expect(w.active == false)
        #expect(w.canResume == false)
    }

    @Test func aJobWithoutAWakeTimeHasNone() throws {
        let w = try worker(#"{"id":"w2","status":"running","wake_at":null}"#)
        #expect(w.wakeAt == nil)
        #expect(w.waiting == false)
        #expect(w.running)
    }

    @Test func aBlockedJobStillNeedsTheOwner() throws {
        let w = try worker(#"{"id":"w3","status":"blocked"}"#)
        #expect(w.needsYou)
        #expect(w.waiting == false)
        #expect(w.canResume)
    }

    @Test func waitingIsNotInTheResumableStatuses() {
        #expect(Worker.resumable.contains(Worker.statusWaiting) == false)
    }

    @Test func aWaitingJobStaysInTheTasksBackground() {
        // the task is over but its job still goes on (retrying): the task keeps its place under "in the background"
        #expect(Timeline.jobGoing.contains(Worker.statusWaiting))
    }

    @Test func theWakeTimeSurvivesTheListForm() throws {
        let w = try worker(#"{"id":"w4","status":"waiting","wake_at":1790000100}"#)
        let copy = Worker(id: w.id, status: w.status, wakeAt: w.wakeAt)
        #expect(copy.wakeAt == 1_790_000_100)
        #expect(copy.waiting)
    }
}
