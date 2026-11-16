import Testing
import Foundation
import KeloCore
@testable import KeloWork

@Suite struct WorkTimelineTests {
    @Test func aDoneTaskWithARunningJobIsInBackground() {
        let task = AgentTask(id: 1, title: "task", status: AgentTask.statusDone)
        let job = Worker(id: "w1", kind: "browser", status: Worker.statusRunning)
        let runningNode = TaskNode(task: task, jobs: [JobNode(worker: job)])
        #expect(runningNode.inBackground)

        let doneJob = Worker(id: "w1", kind: "browser", status: "done")
        let doneNode = TaskNode(task: task, jobs: [JobNode(worker: doneJob)])
        #expect(!doneNode.inBackground)
    }

    @Test func tabForJobGoing() {
        let stopped = Worker(id: "w1", kind: "browser", status: Worker.statusStopped, tab: "task:w1")
        #expect(Timeline.tabFor(stopped) == nil)

        let running = Worker(id: "w1", kind: "browser", status: Worker.statusRunning, tab: "task:w1")
        #expect(Timeline.tabFor(running) == "task:w1")
    }
}
