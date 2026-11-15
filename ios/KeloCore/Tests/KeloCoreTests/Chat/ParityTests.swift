import Testing
import Foundation
@testable import KeloCore

@Suite struct ParityCoreTests {
    @Test func pendingApprovalKeepsWhatAsked() throws {
        let j = try JSON.parse(#"{"items":[{"type":"approval_request","approval_id":"a1","name":"browser_click","asked_at":5,"source":"worker","worker_id":"w9","title":"Apply to campaigns"},{"type":"approval_request","approval_id":"a2","name":"x","worker_id":"  "}],"resolved":[{"approval_id":"h1","outcome":"solved"}]}"#)
        let list = PendingApproval.parseList(j)
        #expect(list.count == 2)
        #expect(list[0].source == "worker")
        #expect(list[0].workerId == "w9")
        #expect(list[0].title == "Apply to campaigns")
        #expect(list[1].workerId == nil)
        #expect(PendingApproval.parseResolved(j) == ["h1": "solved"])
    }

    @Test func browserShotNamesItsTab() throws {
        guard case .browser(let b) = TurnEvent.parse(try JSON.parse(#"{"type":"browser","id":"t1","tab":"worker-3"}"#)) else { Issue.record("not a browser event"); return }
        #expect(b.tab == "worker-3")
        guard case .browser(let old) = TurnEvent.parse(try JSON.parse(#"{"type":"browser","id":"t1"}"#)) else { return }
        #expect(old.tab == "")
    }

    @Test func taskPermissions() throws {
        let j = try JSON.parse(#"{"items":[{"id":"g1","site":"shop.example","action":"체험단 신청","max_count":24,"used":5,"task_id":12},{"site":"no id"}]}"#)
        let g = TaskPermission.parseList(j)
        #expect(g.count == 1)
        #expect(g[0].maxCount == 24 && g[0].used == 5)
        #expect(g[0].action == "체험단 신청")
    }
}
