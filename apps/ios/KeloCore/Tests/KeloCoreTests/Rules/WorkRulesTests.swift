import Foundation
import Testing
@testable import KeloCore

/// Port of android work/WorkRulesTest.kt: the work timeline's nesting, the four sections, the agenda and the badge.
@Suite struct WorkRulesTests {
    private let utc = TimeZone(identifier: "UTC")!
    private let today = LocalDay(year: 2026, month: 11, day: 7)
    private func at(_ day: Int, _ hour: Int) -> Double {
        var c = DateComponents(); c.year = 2026; c.month = 11; c.day = day; c.hour = hour
        var cal = Calendar(identifier: .gregorian); cal.timeZone = utc
        return cal.date(from: c)!.timeIntervalSince1970
    }

    private var parent: AgentTask { AgentTask(id: 10, title: "여행 준비", status: AgentTask.statusDone, startedAt: at(7, 9), day: "2026-11-07") }
    private var run1: AgentTask { AgentTask(id: 11, title: "숙소 찾기 1", status: AgentTask.statusDone, startedAt: at(7, 10), parentId: 10, workerId: "w1") }
    private var run2: AgentTask { AgentTask(id: 12, title: "숙소 찾기 2", status: AgentTask.statusRunning, startedAt: at(7, 11), parentId: 10, workerId: "w1") }
    private var child: AgentTask { AgentTask(id: 13, title: "요약", status: AgentTask.statusDone, startedAt: at(7, 12), parentId: 10) }
    private var plain: AgentTask { AgentTask(id: 5, title: "날씨", status: AgentTask.statusFailed, source: "schedule:3", startedAt: at(6, 8), day: "2026-11-06") }
    private let job = Worker(id: "w1", kind: "browser", title: "숙소 찾기", status: Worker.statusRunning, parentTaskId: 10, tab: "task:w1", steps: 30, maxSteps: 200)
    private let orphan = Worker(id: "w9", kind: "research", status: Worker.statusRunning, parentTaskId: 999)

    private func ids(_ groups: [DayGroups.Group<TaskNode>]) -> [Int64] { groups.flatMap { g in g.items.map(\.task.id) } }

    @Test func jobsAndTheirRunsNestUnderTheTaskThatHandedThemOff() throws {
        let n = Timeline.nest([child, run2, run1, parent, plain], [job, orphan])
        #expect(n.roots.map(\.task.id) == [10, 5])
        let root = try #require(n.roots.first)
        #expect(root.jobs.map(\.worker.id) == ["w1"])
        #expect(root.jobs.count == 1)
        #expect(root.jobs.first?.runs.map(\.id) == [11, 12])   // oldest run first
        #expect(root.children.map(\.id) == [13])
        #expect(n.orphanJobs.map(\.worker.id) == ["w9"])
    }

    @Test func aFinishedTaskWithARunningJobIsStillNow() {
        let n = Timeline.nest([run2, run1, parent, plain], [job, orphan])
        let now = Timeline.now(n)
        #expect(now.tasks.map(\.task.id) == [10])
        #expect(now.jobs.map(\.worker.id) == ["w9"])
        #expect(now.count == 2)
        var doneJob = job
        doneJob.status = "done"
        let calm = Timeline.nest([parent, plain], [doneJob])
        #expect(Timeline.now(calm).empty)
    }

    @Test func aDoneTaskWithARunningJobIsInBackground() {
        let runningNode = TaskNode(task: parent, jobs: [JobNode(worker: job)])
        #expect(runningNode.inBackground)

        var doneJob = job
        doneJob.status = "done"
        let doneNode = TaskNode(task: parent, jobs: [JobNode(worker: doneJob)])
        #expect(!doneNode.inBackground)
    }

    @Test func pastGroupsByDayAndFilters() {
        let n = Timeline.nest([parent, plain], [])
        let all = Timeline.past(n, filter: .all, today: today, zone: utc)
        #expect(all.map(\.label) == [.today, .yesterday])
        #expect(ids(Timeline.past(n, filter: .done, today: today, zone: utc)) == [10])
        #expect(ids(Timeline.past(n, filter: .problems, today: today, zone: utc)) == [5])
        #expect(ids(Timeline.past(n, filter: .automatic, today: today, zone: utc)) == [5])
    }

    @Test func aFailedJobMakesItsTaskAProblem() {
        var failed = job
        failed.status = Worker.statusFailed
        let n = Timeline.nest([parent], [failed])
        #expect(n.roots.count == 1)
        #expect(n.roots.first?.problem == true)
        #expect(ids(Timeline.past(n, filter: .problems, today: today, zone: utc)) == [10])
    }

    private func pending(_ id: String, needsCard: Bool, askedAt: Double) throws -> PendingApproval {
        let card = needsCard ? #","card":{"id":"c","label":"Visa","brand":"visa","last4":"4242","needs_card":true}"# : ""
        let pay = needsCard ? #","payment":{"site":"s","summary":"x","amount":1,"currency":"KRW","amount_text":"1원"}"# : ""
        let o = try JSON.parse(#"{"type":"approval_request","approval_id":"\#(id)","name":"t","asked_at":\#(askedAt)\#(pay)\#(card)}"#)
        return try #require(PendingApproval.parse(o))
    }

    @Test func cardDeliveriesComeFirstThenTheOldestQuestionThenStoppedJobs() throws {
        let asks = Timeline.asks(
            pending: [try pending("a", needsCard: false, askedAt: 5.0), try pending("b", needsCard: true, askedAt: 9.0), try pending("c", needsCard: false, askedAt: 1.0)],
            workers: [job, Worker(id: "w2", status: Worker.statusNeedsInput, updatedAt: 3.0), Worker(id: "w3", status: Worker.statusInterrupted, updatedAt: 1.0)])
        #expect(asks.map(\.key) == ["ask:b", "ask:c", "ask:a", "askjob:w3", "askjob:w2"])
        let first = try #require(asks.first)
        guard case .approval = first else { Issue.record("first ask is not an approval"); return }
        #expect(first.deliver)
    }

    @Test func followupsOnHoldComeLastWithoutRepeatingWhatIsListed() throws {
        func hold(_ id: String, _ at: Double, _ acts: FollowupAction...) -> Followup {
            Followup(id: id, kind: "reply", title: "t\(id)", stage: Followup.onHoldStage, createdAt: at, actions: acts)
        }
        let asks = Timeline.asks(
            pending: [try pending("a", needsCard: false, askedAt: 5.0)],
            workers: [Worker(id: "w2", status: Worker.statusNeedsInput, updatedAt: 3.0)],
            followups: [
                hold("f2", 9.0), hold("f1", 2.0),
                hold("f3", 1.0, FollowupAction(kind: "resume", workerId: "w2")),
                hold("f4", 1.0, FollowupAction(kind: "approve", approvalId: "a")),
                Followup(id: "f5", kind: "reply", title: "", stage: "first", createdAt: 0.0, actions: []),
            ])
        #expect(asks.map(\.key) == ["ask:a", "askjob:w2", "askhold:f1", "askhold:f2"])
    }

    @Test func theLiveTabOfATaskOrAJob() throws {
        let tabs = [BrowserTab(id: "task:x", status: "working", taskId: 12), BrowserTab(id: "old", status: "ended", taskId: 5)]
        #expect(Timeline.tabFor(run2, tabs: tabs) == "task:x")
        #expect(Timeline.tabFor(plain, tabs: tabs) == nil)                      // its tab has ended
        #expect(Timeline.tabFor(run1, tabs: [], workers: [job]) == "task:w1")
        var stoppedJob = job
        stoppedJob.status = Worker.statusStopped
        #expect(Timeline.tabFor(stoppedJob) == nil)
        #expect(Timeline.tabFor(job) == "task:w1")
        #expect(Timeline.tabFor(orphan) == nil)                                 // research with no picture: no browser
        let p = try #require(Timeline.progress(job))
        #expect(abs(p - 0.15) < 0.001)
        #expect(Timeline.progress(orphan) == nil)
    }

    private func sched(_ id: Int64, _ next: Double?, enabled: Bool = true, paused: Bool = false, name: String? = nil) -> Schedule {
        Schedule(id: id, name: name ?? "s\(id)", enabled: enabled, nextRunTs: next, paused: paused)
    }

    @Test func theAgendaGoesByDayThenTimeAndKeepsTheRestApart() {
        let plan = Agenda.build([
            sched(1, at(8, 9)), sched(2, at(7, 18)), sched(3, at(7, 7)), sched(4, nil), sched(5, at(9, 9), enabled: false),
            sched(6, at(9, 9), paused: true), sched(7, at(8, 9), name: "a"),
        ], zone: utc)
        #expect(plan.days.map(\.date) == [LocalDay(year: 2026, month: 11, day: 7), LocalDay(year: 2026, month: 11, day: 8)])
        #expect(plan.days[0].entries.map(\.schedule.id) == [3, 2])
        #expect(plan.days[1].entries.map(\.schedule.id) == [7, 1])   // same time: by name
        #expect(plan.unscheduled.map(\.id) == [4])
        #expect(plan.paused.map(\.id) == [6, 5])                      // paused by a run before switched off
        #expect(!plan.empty)
        #expect(Agenda.build([], zone: utc).empty)
    }

    @Test func agendaDayWords() {
        #expect(Agenda.whenOf(today, today: today) == .today)
        #expect(Agenda.whenOf(today.adding(days: 1), today: today) == .tomorrow)
        #expect(Agenda.whenOf(today.adding(days: 2), today: today) == .later)
    }

    @Test func theBadgeShowsOnlyWhileSomethingWaitsForTheOwner() {
        #expect(WorkBadge.of(nil) == nil)
        #expect(WorkBadge.of(AgentStatus()) == nil)
        // each case on its own lights it, with its count
        #expect(WorkBadge.of(AgentStatus(pendingApprovals: 2)) == WorkBadge.Shown(count: 2))
        #expect(WorkBadge.of(AgentStatus(openFollowups: 1)) == WorkBadge.Shown(count: 1))
        #expect(WorkBadge.of(AgentStatus(secretRequests: 1)) == WorkBadge.Shown(count: 1))
        #expect(WorkBadge.of(AgentStatus(pendingApprovals: 1, openFollowups: 2, unseenTasks: 9, secretRequests: 1)) == WorkBadge.Shown(count: 4))
        // finished work and notifications nobody looked at: no badge, no dot
        #expect(WorkBadge.of(AgentStatus(unseenTasks: 3, unseenNotifications: 5)) == nil)
        // handled: the next poll has the count gone, and the badge with it
        #expect(WorkBadge.of(AgentStatus(pendingApprovals: 0, openFollowups: 0, secretRequests: 0)) == nil)
        #expect(WorkBadge.of(AgentStatus(pendingApprovals: 0, openFollowups: 1, secretRequests: 1)) == WorkBadge.Shown(count: 2))
    }

    @Test func theBadgeSaysWhatWaitsAndAnAnsweredEntryLeavesItAtOnce() {
        let s = AgentStatus(pendingApprovals: 2, secretRequests: 2, secretKinds: ["key": 1, "login": 1])
        #expect(WorkBadge.parts(s) == [WorkBadge.Part(kind: WorkBadge.approvals, count: 2), WorkBadge.Part(kind: WorkBadge.keys, count: 1),
                                       WorkBadge.Part(kind: WorkBadge.logins, count: 1)])
        #expect(WorkBadge.parts(AgentStatus(secretRequests: 1)) == [WorkBadge.Part(kind: WorkBadge.entries, count: 1)])
        // the Telegram token is the only thing that waits: cancelling it clears the badge without waiting for the next poll
        let one = AgentStatus(secretRequests: 1, secretKinds: ["key": 1])
        #expect(WorkBadge.of(one) == WorkBadge.Shown(count: 1))
        let after = WorkBadge.entryClosed(one, kind: "key")
        #expect(WorkBadge.of(after) == nil && after?.secretKinds.isEmpty == true)
        #expect(WorkBadge.entryClosed(after, kind: "key")?.secretRequests == 0)
    }

    @Test func waitingEntriesAreRowsInAsksAfterTheApprovals() throws {
        let token = SecretRequest(id: "sr1", kind: "key", name: "telegram", label: "텔레그램 봇 토큰", createdAt: 4)
        let draft = SecretRequest(id: "sr2", kind: "login", name: "x", source: "draft", createdAt: 2)
        let done = SecretRequest(id: "sr3", kind: "key", state: SecretRequest.stateSaved, createdAt: 1)
        let asks = Timeline.asks(pending: [try pending("a", needsCard: false, askedAt: 5)],
                                 workers: [Worker(id: "w2", status: Worker.statusNeedsInput, updatedAt: 3)], entries: [token, draft, done])
        #expect(asks.map(\.key) == ["ask:a", "askentry:sr2", "askentry:sr1", "askjob:w2"])
        #expect(asks[2].chatCard && !asks[1].chatCard)
    }

    @Test func watchersThatNeedALookComeFirst() {
        let list = [
            Watcher(id: 1, name: "b", state: "off"), Watcher(id: 2, name: "a", enabled: true, state: "watching"),
            Watcher(id: 3, name: "c", enabled: true, state: "error"),
            Watcher(id: 4, name: "d", enabled: true, state: "watching", lastFiredAt: 50.0),
        ]
        #expect(Watching.order(list).map(\.id) == [3, 4, 2, 1])
    }
}

/// Port of android work/WorkRulesTest.kt "열린 브라우저": the open browsers the timeline lists, and which ones it leaves to a running task.
@Suite struct OpenBrowsersTests {
    private func at(_ day: Int, _ hour: Int) -> Double {
        var c = DateComponents(); c.year = 2026; c.month = 11; c.day = day; c.hour = hour
        var cal = Calendar(identifier: .gregorian); cal.timeZone = TimeZone(identifier: "UTC")!
        return cal.date(from: c)!.timeIntervalSince1970
    }

    private let chatTab = BrowserTab(id: "16", label: "Phone", kind: "device", title: "쿠팡", url: "https://www.coupang.com/", host: "www.coupang.com",
                                     source: "browser", status: "idle", lastActivity: 100, taskId: 509)
    private let androidDevice = BrowserTab(id: "android", label: "Pixel", kind: "device", source: "device", status: "idle")
    private let noRunning = Timeline.Now(tasks: [], jobs: [])

    @Test func anIdleOpenTabWhoseTaskIsDoneIsListed() {
        let done = AgentTask(id: 509, title: "쿠팡", status: AgentTask.statusDone, startedAt: at(7, 9))
        let now = Timeline.now(Timeline.nest([done], []))
        #expect(Timeline.openTabs([chatTab], now: now).map(\.id) == ["16"])
    }

    @Test func anEndedTabIsNotListed() {
        var ended = chatTab
        ended.status = "ended"
        #expect(Timeline.openTabs([ended], now: noRunning).isEmpty)
    }

    @Test func theAndroidDeviceIsNotAnOpenBrowserEvenThoughItsKindIsTheSameAsAPhonesChatTab() {
        #expect(chatTab.kind == androidDevice.kind)            // the kind cannot tell them apart
        #expect(chatTab.isBrowserTab)
        #expect(!androidDevice.isBrowserTab)
        var olderServer = androidDevice
        olderServer.source = ""                                 // an older server sends no source: the id still says it
        #expect(!olderServer.isBrowserTab)
        #expect(Timeline.openTabs([androidDevice, chatTab], now: noRunning).map(\.id) == ["16"])
    }

    @Test func aTabARunningTaskOrJobAlreadyShowsIsNotListedAgain() {
        let running = AgentTask(id: 509, title: "쿠팡", status: AgentTask.statusRunning, startedAt: at(7, 9))
        var working = chatTab
        working.status = "working"
        #expect(Timeline.openTabs([working], now: Timeline.now(Timeline.nest([running], []))).isEmpty)

        let job = Worker(id: "w1", kind: "browser", title: "숙소 찾기", status: Worker.statusRunning, parentTaskId: 10, tab: "task:w1", steps: 30, maxSteps: 200)
        let run = AgentTask(id: 12, title: "숙소 찾기 2", status: AgentTask.statusRunning, startedAt: at(7, 11), parentId: 10, workerId: "w1")
        let nowJob = Timeline.now(Timeline.nest([run], [job]))
        let jobTab = BrowserTab(id: "task:w1", kind: "task", source: "browser", status: "working", taskId: 12)
        #expect(Timeline.openTabs([jobTab, chatTab], now: nowJob, workers: [job]).map(\.id) == ["16"])
    }

    @Test func workingTabsComeFirstThenTheNewest() {
        var a = chatTab, b = chatTab, c = chatTab
        a.id = "a"; a.lastActivity = 5
        b.id = "b"; b.lastActivity = 9
        c.id = "c"; c.lastActivity = 1; c.status = "working"
        #expect(Timeline.openTabs([a, b, c], now: noRunning).map(\.id) == ["c", "b", "a"])
    }

    @Test func theServersListingDecodesWithItsSource() throws {
        let json = """
        {"available":true,"items":[
          {"id":"android","kind":"device","source":"device","label":"Pixel","title":"Pixel","status":"idle"},
          {"id":"16","kind":"device","source":"browser","label":"Phone","title":"쿠팡","url":"https://www.coupang.com/","host":"www.coupang.com","status":"idle","task_id":509}]}
        """
        let listing = try JSONDecoder().decode(BrowserTabs.self, from: Data(json.utf8))
        #expect(Timeline.openTabs(listing.items, now: noRunning).map(\.id) == ["16"])
    }
}
