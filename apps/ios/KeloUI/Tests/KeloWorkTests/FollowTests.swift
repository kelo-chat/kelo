import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloWork

private let followJson = """
{"items":[
  {"key":"cal:flight-123","kind":"flight","title":"OZ 731","starts_at":1794744000.0,"state":"watching",
   "checks":[{"kind":"flight_status","fields":["gate","time"],"state":"active","next_at":1794700000.0}],
   "prep":[{"what":"online check-in","when":"-24h","state":"later"}],
   "plan":{"prep":[],"tell":{"lead":["-1d"],"on_change":true}}},
  {"key":"cal:dinner","kind":"event","title":"Dinner","starts_at":1794800000.0,"state":"stopped"}
]}
"""

@MainActor @Suite struct FollowTests {
    @Test func decodesTheListAndWordsTheSummary() throws {
        let list = try JSONDecoder().decode(FollowItemList.self, from: Data(followJson.utf8))
        #expect(list.items.count == 2)
        let f = list.items[0]
        #expect(f.title == "OZ 731" && f.kind == "flight" && f.state == "watching")
        #expect(f.prep.first?.state == "later")
        #expect(f.plan?.tell?.lead == ["-1d"])
        #expect(FollowRules.nextCheck(f) == 1794700000.0)
        // lead (flight: "before departure") then the check-in prep
        #expect(FollowRules.planSummary(f).contains(" · "))
        #expect(FollowRules.planSummary(list.items[1]) == L(R.string.follow_summary_default))
    }

    @Test func checkInSummaryComesFromThePrepKindNotItsWords() throws {
        func item(_ what: String, _ kind: String) throws -> FollowItem {
            let j = #"{"items":[{"key":"k","kind":"event","title":"t","starts_at":1794744000.0,"state":"watching","prep":[{"what":"\#(what)","when":"-24h","state":"later","kind":"\#(kind)"}]}]}"#
            return try JSONDecoder().decode(FollowItemList.self, from: Data(j.utf8)).items[0]
        }
        #expect(FollowRules.planSummary(try item("보딩패스 준비", "check_in")).contains(L(R.string.follow_summary_prep_checkin)))
        #expect(!FollowRules.planSummary(try item("online check-in", "other")).contains(L(R.string.follow_summary_prep_checkin)))
    }

    @Test func prepStatesMapToTheirWords() {
        #expect(FollowRules.prepStateText("later") == L(R.string.follow_prep_later))
        #expect(FollowRules.prepStateText("running") == L(R.string.follow_prep_running))
        #expect(FollowRules.prepStateText("done") == L(R.string.follow_prep_done))
        #expect(FollowRules.prepStateText("failed") == L(R.string.follow_prep_failed))
        #expect(FollowRules.leadHours("-3h") == 3)
    }

    @Test func emptyListHidesAndOlderServerIsNotAnError() async {
        let t = RoutedTransport()
        t.on("GET /api/follow", #"{"items":[]}"#)
        let m = FollowModel(api: { t.api() })
        await m.load()
        #expect(m.items.isEmpty && !m.failed)
        let old = RoutedTransport()
        old.on("GET /api/follow", 404, #"{"detail":"no"}"#)
        let m2 = FollowModel(api: { old.api() })
        await m2.load()
        #expect(m2.items.isEmpty && !m2.failed)
    }

    @Test func stoppedItemsFoldUnderTheLiveOnes() throws {
        let list = try JSONDecoder().decode(FollowItemList.self, from: Data(followJson.utf8))
        let split = FollowRules.splitStopped(list.items)
        #expect(split.live.map(\.key) == ["cal:flight-123"])
        #expect(split.stopped.map(\.key) == ["cal:dinner"])
        #expect(FollowRules.splitStopped([]).live.isEmpty && FollowRules.splitStopped([]).stopped.isEmpty)
    }

    @Test func removeCallsDeleteAndDropsTheRow() async {
        let t = RoutedTransport()
        var deletes = 0
        t.on("GET /api/follow", followJson)
        t.on("DELETE /api/follow/cal:flight-123") { _, _ in deletes += 1; return (200, #"{"ok":true,"key":"cal:flight-123","state":"removed"}"#) }
        let m = FollowModel(api: { t.api() })
        await m.load()
        await m.remove("cal:flight-123")
        #expect(deletes == 1)
        #expect(!m.items.contains { $0.key == "cal:flight-123" })
        #expect(m.removing.isEmpty)
    }

    @Test func stopAndReplanCallTheServerAndReload() async {
        let t = RoutedTransport()
        var posts: [String] = []
        t.on("GET /api/follow", followJson)
        t.on("POST /api/follow/cal:flight-123/stop") { call, _ in posts.append("stop"); return (200, #"{"ok":true,"key":"cal:flight-123","state":"stopped"}"#) }
        t.on("POST /api/follow/cal:flight-123/replan") { call, _ in posts.append("replan"); return (200, #"{"ok":true,"key":"cal:flight-123","state":"watching"}"#) }
        let m = FollowModel(api: { t.api() })
        await m.load()
        await m.stop("cal:flight-123")
        await m.replan("cal:flight-123")
        #expect(posts == ["stop", "replan"])
        #expect(m.stopping.isEmpty && m.replanning.isEmpty)
    }
}
