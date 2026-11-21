import Testing
import Foundation
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloWork

/// Ports of the Android app's GoalMetaTest, GoalsStateTest and GoalWorkspaceTest.
@Suite struct GoalRulesTests {
    let d = LocalDay(year: 2026, month: 11, day: 20)

    @Test func metaRoundTrips() {
        for m in [GoalMeta(note: "매일 30분", due: d, progress: 40), GoalMeta(note: "", due: d), GoalMeta(note: "메모만"), GoalMeta(note: "", progress: 100)] {
            #expect(GoalMetaCodec.parse(GoalMetaCodec.encode(m)) == m)
        }
        #expect(GoalMetaCodec.encode(GoalMeta(note: "매일 30분 ", due: d, progress: 40)) == "매일 30분\n[due:2026-11-20 progress:40]")
        #expect(GoalMetaCodec.encode(GoalMeta(note: "", progress: 0)) == "[progress:0]")
        #expect(GoalMetaCodec.encode(GoalMeta(note: "plain")) == "plain")
    }

    @Test func workingWinsOverWaitingAndTheListRefreshesWhileEitherShows() throws {
        #expect(GoalsRules.activity(GoalItem(id: 1, title: "a", asking: true, working: true)) == .working)
        #expect(GoalsRules.activity(GoalItem(id: 1, title: "a", asking: true)) == .asking)
        #expect(GoalsRules.activity(GoalItem(id: 1, title: "a")) == nil)
        #expect(GoalsRules.activity(GoalItem(id: 1, title: "a", status: GoalItem.statusDone, asking: true, working: true)) == nil)
        #expect(GoalsRules.refreshMs([GoalItem(id: 1, title: "a", asking: true), GoalItem(id: 2, title: "b", working: true)]) == GoalsRules.workingRefreshMs)
        #expect(GoalsRules.refreshMs([GoalItem(id: 1, title: "a", asking: true)]) == GoalsRules.askingRefreshMs)
        #expect(GoalsRules.refreshMs([GoalItem(id: 1, title: "a")]) == nil)
        let old = try JSONDecoder().decode(GoalItem.self, from: Data(#"{"id":1,"title":"a","asking":true}"#.utf8))
        #expect(!old.working && GoalsRules.activity(old) == .asking)
        let now = try JSONDecoder().decode(GoalItem.self, from: Data(#"{"id":1,"title":"a","asking":false,"working":true}"#.utf8))
        #expect(GoalsRules.activity(now) == .working)
    }

    @Test func oldNotesAndOddValues() {
        #expect(GoalMetaCodec.parse("그냥 메모") == GoalMeta(note: "그냥 메모"))
        let bad = GoalMetaCodec.parse("x\n[due:2026-13-40 progress:250]")
        #expect(bad.note == "x" && bad.due == nil && bad.progress == 100)
        #expect(GoalMetaCodec.parse("[due:2026-11-20] 다음 줄").note == "[due:2026-11-20] 다음 줄")
    }

    @Test func nearestDueFirstThenTheRestInOrder() {
        let goals = [GoalItem(id: 1, title: "a"), GoalItem(id: 2, title: "b", note: "[due:2026-12-01]"), GoalItem(id: 3, title: "c"),
                     GoalItem(id: 4, title: "d", note: "x\n[due:2026-11-10 progress:20]")]
        #expect(GoalsRules.ordered(goals).map(\.id) == [4, 2, 1, 3])
        let today = LocalDay(year: 2026, month: 11, day: 7)
        #expect(GoalsRules.overdue(LocalDay(year: 2026, month: 11, day: 1), today: today))
        #expect(!GoalsRules.overdue(today, today: today))
        #expect(!GoalsRules.overdue(nil, today: today))
        #expect(GoalsRules.tag("  건강 ") == "건강")
        #expect(GoalsRules.tag(String(repeating: "x", count: 60)).count == 60)
        #expect(GoalsRules.snapProgress(44) == 40)
        #expect(GoalsRules.snapProgress(46) == 50)
        #expect(GoalsRules.snapProgress(130) == 100)
    }

    @Test func swipedGoalsAreHiddenUntilUndone() {
        var s = GoalsState(goals: [GoalItem(id: 1, title: "운동"), GoalItem(id: 2, title: "독서", status: GoalItem.statusDone), GoalItem(id: 3, title: "물")],
                           loaded: true, removing: [3, 2])
        #expect(s.tracking.map(\.id) == [1])
        #expect(s.done.isEmpty)
        s.removing.remove(2)
        #expect(s.done.map(\.id) == [2])
        let sug = GoalsState(suggestions: [GoalSuggestion(id: 1, title: "a"), GoalSuggestion(id: 2, title: "b")], settling: [1])
        #expect(sug.shownSuggestions.map(\.id) == [2])
    }

    @Test func readsAGoalPageAndAnOlderGoalList() throws {
        let json = """
        {"goal":{"id":3,"title":"G","category":"","note":"","status":"tracking","origin":"suggestion","plan":{"done":1,"total":2},
                 "next_step":"second","asking":true,"created_at":1,"updated_at":2},
         "facts":[{"key":"a","value":"1","skipped":false,"source":"user","updated_at":1},{"key":"b","value":"","skipped":true}],
         "steps":[{"id":1,"title":"first","status":"done","owner":"user","due":null,"done_at":5},
                  {"id":2,"title":"second","status":"doing","owner":"agent","due":"2026-11-20"}],
         "metrics":[{"id":1,"name":"n","unit":"pt","start":10,"target":20,"values":[{"value":12,"note":"","at":1},{"value":15.5,"at":2}]}],
         "log":[{"id":1,"at":1,"text":"x","kind":"note","by":"agent"}],
         "documents":[{"id":1,"path":"files/a.pdf","title":"A","added_at":1,"exists":true}],
         "schedules":[{"id":4,"name":"Check-in","description":"Every Monday","enabled":true,"goal_id":3,"extra":1}]}
        """
        let w = try JSONDecoder().decode(GoalWorkspace.self, from: Data(json.utf8))
        #expect(w.goal.plan == GoalPlan(done: 1, total: 2))
        #expect(w.goal.nextStep == "second")
        #expect(w.goal.asking)
        #expect(w.facts.count == 2 && w.facts[1].skipped)
        #expect(w.steps[0].mine && !w.steps[1].mine)
        #expect(w.steps[1].due == "2026-11-20")
        #expect(w.metrics.first?.values.map(\.value) == [12, 15.5])
        #expect(w.metrics.first?.target == 20)
        #expect(w.log.count == 1 && w.documents.first?.title == "A")
        #expect(w.schedules.map(\.name) == ["Check-in"])
        let old = try JSONDecoder().decode([GoalItem].self, from: Data(#"[{"id":1,"title":"x","category":"","note":"","status":"tracking"}]"#.utf8))
        #expect(old.first?.plan == GoalPlan())
        #expect(old.first?.asking == false)
        #expect(old.first?.nextStep == nil)
        // kept goals survive the screen cache's round trip
        let again = try JSONDecoder().decode([GoalItem].self, from: JSONEncoder().encode([w.goal]))
        #expect(again == [w.goal])
    }

    @Test func onlyTheUsersOwnStepsTick() {
        let mine = GoalStep(id: 1, title: "a", owner: "user")
        let kelos = GoalStep(id: 2, title: "b", owner: "agent")
        #expect(GoalDetailRules.canTick(mine))
        #expect(!GoalDetailRules.canTick(kelos))
        #expect(GoalDetailRules.toggled(mine) == "done")
        var done = mine; done.status = "done"
        #expect(GoalDetailRules.toggled(done) == "todo")
    }

    @Test func numbersReadLikePeopleWriteThem() {
        #expect(GoalDetailRules.number(72.0) == "72")
        #expect(GoalDetailRules.number(72.5) == "72.5")
        #expect(GoalDetailRules.number(1.0 / 3) == "0.33")
        #expect(GoalDetailRules.number(nil) == "-")
        #expect(GoalDetailRules.withUnit(15.0, "pt") == "15 pt")
        #expect(GoalDetailRules.withUnit(15.0, "") == "15")
    }

    @Test func theChartSpansTheValuesAndTheTarget() {
        let m = GoalMetric(id: 1, name: "n", target: 20, values: [GoalMetricValue(value: 10), GoalMetricValue(value: 15)])
        let pts = GoalDetailRules.chart(m)
        #expect(pts.count == 2 && pts[0] == (0, 0) && pts[1] == (1, 0.5))
        #expect(GoalDetailRules.targetLevel(m) == 1)
        var empty = m; empty.values = []
        #expect(GoalDetailRules.chart(empty).isEmpty)
        #expect(GoalDetailRules.targetLevel(empty) == nil)
    }

    @Test func deletingAGoalAsksAboutItsSchedulesOnlyWhenSomeAreOn() throws {
        let g = try JSONDecoder().decode(GoalItem.self, from: Data(#"{"id":7,"title":"a","schedules_on":2}"#.utf8))
        #expect(g.schedulesOn == 2 && GoalsRules.asksAboutSchedules(g))
        let old = try JSONDecoder().decode(GoalItem.self, from: Data(#"{"id":7,"title":"a"}"#.utf8))
        #expect(old.schedulesOn == 0 && !GoalsRules.asksAboutSchedules(old))
        #expect(GoalsRules.deletePath(7, deleteSchedules: true) == "/api/goals/7?schedules=delete")
        #expect(GoalsRules.deletePath(7, deleteSchedules: false) == "/api/goals/7?schedules=keep")
        let back = try JSONDecoder().decode(GoalItem.self, from: JSONEncoder().encode(g))
        #expect(back.schedulesOn == 2)
    }

    @Test @MainActor func theChoiceStaysWithTheGoalUntilItIsDeletedOrUndone() {
        let m = GoalsModel(api: { nil }, cache: memoryCache())
        let a = GoalItem(id: 1, title: "a", schedulesOn: 1), b = GoalItem(id: 2, title: "b")
        m.state.goals = [a, b]
        m.startRemove(a, pauseSchedules: true)
        m.startRemove(b)
        #expect(m.state.removing == [1, 2] && m.state.pausing == [1])
        #expect(m.state.tracking.isEmpty)
        m.undoRemove(a)
        #expect(m.state.removing == [2] && m.state.pausing.isEmpty)
    }

    @Test func smallerGoalsSitUnderTheirParentAndOrphansStayAtTheTop() {
        let goals = [GoalItem(id: 1, title: "Fitness"), GoalItem(id: 2, title: "Run", parentId: 1), GoalItem(id: 3, title: "Read"),
                     GoalItem(id: 4, title: "Stretch", parentId: 9)]
        let groups = GoalsRules.grouped(goals)
        #expect(groups.map { $0.goal.id } == [1, 3, 4])
        #expect(groups[0].children.map(\.id) == [2])
        #expect(groups[1].children.isEmpty)
    }

    @Test func workItemsAreListedApartFromGoals() {
        let goals = [GoalItem(id: 1, title: "Fitness"), GoalItem(id: 5, title: "Parcel", kind: "work"),
                     GoalItem(id: 6, title: "Booking", status: GoalItem.statusDone, kind: "work")]
        #expect(GoalsRules.work(goals).map(\.id) == [5])
        #expect(GoalsRules.grouped(goals).map { $0.goal.id } == [1])
        #expect(GoalsRules.work(goals).allSatisfy { $0.isWork })
    }

    @Test func readsChildrenSummariesAndLogTitlesAndAnOlderLogStillDecodes() throws {
        let w = try JSONDecoder().decode(GoalWorkspace.self, from: Data("""
            {"goal":{"id":1,"title":"G","summary":"Walking twice a week","description":"Keep it easy.","area":"wellbeing","children":1},
             "children":[{"id":2,"title":"Child","parent_id":1}],
             "log":[{"id":7,"at":1,"text":"Ran 2 km","title":"Run","outcome":"Good","happened_at":1700000000.5,
                     "from_goal":{"id":2,"title":"Child"}},{"id":8,"at":2,"text":"x"}]}
            """.utf8))
        #expect(w.goal.summary == "Walking twice a week" && w.goal.description == "Keep it easy." && w.goal.children == 1)
        #expect(w.children.map(\.id) == [2])
        #expect(w.log[0].title == "Run" && w.log[0].outcome == "Good" && w.log[0].fromGoal?.id == 2)
        #expect(GoalsRules.logDay(w.log[0]) == 1700000000.5)
        #expect(w.log[1].title == "" && w.log[1].fromGoal == nil && w.log[1].happenedAt == nil)
        #expect(GoalsRules.logDay(w.log[1]) == 2)
    }

    @Test func theDeleteNoticeCountsSmallerGoalsOnlyWhenThereAre() {
        #expect(GoalsRules.deleteNotice(GoalItem(id: 1, title: "a")) == nil)
        #expect(GoalsRules.deleteNotice(GoalItem(id: 1, title: "a", children: 2)) == 2)
    }

    @Test func theLogPagesAndEveryEntryIsKept() throws {
        let first = try JSONDecoder().decode(GoalWorkspace.self, from: Data(#"{"goal":{"id":3,"title":"G"},"log":[{"id":1,"text":"a"},{"id":2,"text":"b"}],"log_next":20}"#.utf8))
        #expect(first.logNext == 20 && first.log.count == 2)
        let old = try JSONDecoder().decode(GoalWorkspace.self, from: Data(#"{"goal":{"id":3,"title":"G"}}"#.utf8))
        #expect(old.logNext == nil)
        let page = try JSONDecoder().decode(GoalLogPage.self, from: Data(#"{"items":[{"id":3,"text":"c"},{"id":2,"text":"b"}],"next":null}"#.utf8))
        #expect(page.items.map(\.id) == [3, 2] && page.next == nil)
        let more = GoalDetailRules.withMoreLog(first, page)
        #expect(more.log.map(\.id) == [1, 2, 3] && more.logNext == nil)
        #expect(GoalDetailRules.withMoreLog(more, page).log.count == 3)
        let fresh = try JSONDecoder().decode(GoalWorkspace.self, from: Data(#"{"goal":{"id":3,"title":"G"},"log":[{"id":4,"text":"d"},{"id":3,"text":"c"}],"log_next":20}"#.utf8))
        let merged = GoalDetailRules.refreshed(prev: more, fresh: fresh)
        #expect(merged.log.map(\.id) == [4, 3, 1, 2] && merged.logNext == nil)
        #expect(GoalDetailRules.refreshed(prev: nil, fresh: fresh) == fresh)
        #expect(GoalDetailRules.canLoadMore(workspace: first, loading: false))
        #expect(!GoalDetailRules.canLoadMore(workspace: first, loading: true))
        #expect(!GoalDetailRules.canLoadMore(workspace: more, loading: false))
        #expect(!GoalDetailRules.canLoadMore(workspace: nil, loading: false))
    }

    @Test func aTagKeepsItsLengthAsTyped() {
        let sixty = String(repeating: "a", count: 60)
        #expect(GoalsRules.tag("  \(sixty)  ") == sixty)
        #expect(GoalsRules.tag(String(repeating: "b", count: 100)).count == 100)
    }
}
