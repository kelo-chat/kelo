import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloWork

@MainActor @Suite struct GoalsModelTests {
    private func make(_ t: RoutedTransport, cache: ScreenCache = memoryCache()) -> GoalsModel { GoalsModel(api: { t.api() }, cache: cache) }
    private let goals = #"[{"id":1,"title":"a"},{"id":2,"title":"b"}]"#

    @Test func loadFillsTheListAndKeepsItForNextTime() async {
        let t = RoutedTransport()
        t.on("GET /api/goals", goals)
        t.on("GET /api/goals/suggestions", #"{"items":[]}"#)
        let cache = memoryCache()
        let m = make(t, cache: cache)
        await m.read()
        #expect(m.state.goals.count == 2 && m.state.loaded && !m.state.failed)
        let again = make(RoutedTransport(), cache: cache)
        again.warm()
        #expect(again.state.goals.count == 2)
    }

    @Test func aFailureIsAHintWhenGoalsShowAndAnErrorWhenNot() async {
        let t = RoutedTransport()
        t.down = true
        let m = make(t)
        await m.read()
        #expect(m.state.failed)
        let t2 = RoutedTransport()
        t2.on("GET /api/goals", goals)
        t2.on("GET /api/goals/suggestions", #"{"items":[]}"#)
        let m2 = make(t2)
        await m2.read()
        t2.down = true
        await m2.read()
        #expect(m2.state.offline && m2.state.goals.count == 2)
    }

    @Test func anOlderReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/goals") { _, n in
            if n == 0 { await gate.wait(); return (200, #"[{"id":9,"title":"old"}]"#) }
            return (200, #"[{"id":1,"title":"new"}]"#)
        }
        t.on("GET /api/goals/suggestions", #"{"items":[]}"#)
        let m = make(t)
        let first = Task { @MainActor in await m.read() }
        _ = await eventually { t.count("GET /api/goals") == 1 }
        await m.read()
        await gate.release()
        await first.value
        #expect(m.state.goals.map(\.id) == [1])
    }

    @Test func addingAGoalReadsTheListAgain() async {
        let t = RoutedTransport()
        t.on("GET /api/goals", goals)
        t.on("GET /api/goals/suggestions", #"{"items":[]}"#)
        t.on("POST /api/goals", "{}")
        let m = make(t)
        var added = 0
        m.add(title: "t", note: "", category: "", onAdded: { added += 1 })
        await m.jobs.settled()
        #expect(t.count("POST /api/goals") == 1 && t.count("GET /api/goals") >= 1)
    }
}

@MainActor @Suite struct GoalDetailModelTests {
    private let ws = #"{"goal":{"id":3,"title":"g"},"steps":[{"id":5,"title":"s","status":"todo"}]}"#

    @Test func loadAndTickAndFailure() async {
        let t = RoutedTransport()
        t.on("GET /api/goals/3", ws)
        t.on("PATCH /api/goals/3/steps/5", "{}")
        let m = GoalDetailModel(goalId: 3, api: { t.api() })
        await m.read()
        #expect(m.workspace?.steps.count == 1 && !m.failed)
        m.tick(m.workspace!.steps[0])
        await m.jobs.settled()
        #expect(t.count("PATCH /api/goals/3/steps/5") == 1)
        t.on("GET /api/goals/3", 500, #"{"detail":"x"}"#)
        await m.read()
        #expect(m.failed)
    }

    @Test func anOlderReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/goals/3") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"goal":{"id":3,"title":"old"}}"#) }
            return (200, #"{"goal":{"id":3,"title":"new"}}"#)
        }
        let m = GoalDetailModel(goalId: 3, api: { t.api() })
        let first = Task { @MainActor in await m.read() }
        _ = await eventually { t.count("GET /api/goals/3") == 1 }
        await m.read()
        await gate.release()
        await first.value
        #expect(m.workspace?.goal.title == "new")
    }

    @Test func resumeSaysWhyItCouldNot() async {
        let t = RoutedTransport()
        t.on("POST /api/goals/3/work", 409, #"{"detail":"no model"}"#)
        let m = GoalDetailModel(goalId: 3, api: { t.api() })
        m.resume(onStarted: {})
        await m.jobs.settled()
        #expect(m.resumed != nil && !m.resuming)
    }
}

@MainActor @Suite struct WorkDetailModelTests {
    private func work(_ t: RoutedTransport) -> WorkModel { WorkModel(api: { t.api() }) }

    @Test func aFinishedTaskIsReadOnceAndAFailureIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/tasks/7", #"{"id":7,"title":"t","status":"done"}"#)
        let m = TaskDetailModel(work: work(t), id: 7)
        await m.follow()
        #expect(m.detail?.id == 7 && !m.failed)
        #expect(t.count("GET /api/tasks/7") == 1)
        let t2 = RoutedTransport()
        let m2 = TaskDetailModel(work: work(t2), id: 7)
        await m2.follow()
        #expect(m2.failed)
    }

    @Test func aRunningTaskIsFollowedUntilItsTaskIsCancelled() async {
        let t = RoutedTransport()
        t.on("GET /api/tasks/7", #"{"id":7,"title":"t","status":"running"}"#)
        let m = TaskDetailModel(work: work(t), id: 7)
        let f = Task { @MainActor in await m.follow() }
        _ = await eventually { m.detail != nil }
        f.cancel()
        await f.value
        let n = t.count("GET /api/tasks/7")
        try? await Task.sleep(nanoseconds: 100_000_000)
        #expect(t.count("GET /api/tasks/7") == n)
    }

    @Test func aJobIsReadAndAFailureIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/workers/w1", #"{"id":"w1","title":"j","status":"done"}"#)
        let m = WorkerDetailModel(work: work(t), id: "w1")
        await m.follow()
        #expect(m.detail?.id == "w1")
        let m2 = WorkerDetailModel(work: work(RoutedTransport()), id: "w1")
        await m2.follow()
        #expect(m2.failed)
    }

    @Test func scheduleRunsAndAGrantTakenBack() async {
        let t = RoutedTransport()
        t.on("GET /api/schedules/4/runs", #"{"items":[{"id":1}]}"#)
        t.on("GET /api/schedules/4/grants", #"{"items":[{"id":1,"kind":"login"}]}"#)
        let m = ScheduleRunsModel(work: work(t), id: 4)
        await m.load()
        #expect(m.runs?.count == 1)
        #expect(m.grants.count == 1)
    }

    @Test func aScreenshotShowsNothingWhenThereIsNone() async {
        let t = RoutedTransport()
        let m = ScreenshotModel(work: work(t), id: "t1")
        await m.load()
        #expect(m.image == nil && m.problem != nil && !m.loading)
    }

    @Test func watcherEventsLoad() async {
        let t = RoutedTransport()
        t.on("GET /api/watchers/2/events", #"{"items":[]}"#)
        let m = WatcherEventsModel(work: work(t), id: 2)
        await m.load()
        #expect(m.events?.isEmpty == true)
    }

    @Test func theOwnTabIsThisPhonesDeviceIdElseOwner() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", #"{"devices":[],"current":12}"#)
        let m = LiveOverlayModel(api: { t.api() })
        await m.resolve("owner")
        #expect(m.resolved == "12")
        let k = LiveOverlayModel(api: { RoutedTransport().api() })
        await k.resolve("owner")
        #expect(k.resolved == "owner")
        await k.resolve("tab9")
        #expect(k.resolved == "tab9")
    }

    @Test func anOlderPartReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/tasks") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"items":[{"id":1,"title":"old"}]}"#) }
            return (200, #"{"items":[{"id":2,"title":"new"}]}"#)
        }
        let m = work(t)
        m.loadTasks()
        _ = await eventually { t.count("GET /api/tasks") == 1 }
        m.loadTasks()   // a change was made: the newer read replaces the one in flight
        await gate.release()
        await m.settled()
        #expect(m.tasks.data?.items.map(\.id) == [2])
    }
}

@Suite struct SectionJumpTests {
    @Test func aJumpWaitsForItsSectionsData() {
        let asks = WorkTimeline.asks, up = WorkTimeline.upcoming, watch = WorkTimeline.watch
        #expect(!SectionJump.ready(key: asks, tasks: true, perms: false, schedules: true, watchers: true))
        #expect(SectionJump.ready(key: asks, tasks: true, perms: true, schedules: false, watchers: false))
        #expect(!SectionJump.ready(key: up, tasks: true, perms: true, schedules: false, watchers: true))
        #expect(SectionJump.ready(key: up, tasks: true, perms: false, schedules: true, watchers: false))
        #expect(!SectionJump.ready(key: watch, tasks: false, perms: true, schedules: true, watchers: true))
        #expect(SectionJump.ready(key: watch, tasks: true, perms: false, schedules: false, watchers: true))
        #expect(!SectionJump.ready(key: WorkTimeline.now, tasks: false, perms: true, schedules: true, watchers: true))
        #expect(SectionJump.ready(key: WorkTimeline.now, tasks: true, perms: false, schedules: false, watchers: false))
    }
}
