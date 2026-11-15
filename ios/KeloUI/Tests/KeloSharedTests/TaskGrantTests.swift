import Testing
import Foundation
import KeloCore
@testable import KeloShared

@Suite struct TaskGrantTests {
    @Test func decodeGrantAskWithApply() throws {
        let json = """
        {
            "trigger": "매일 9시",
            "action": "체험단 신청",
            "apply": {
                "site": "example.com",
                "what": "선물 체험단",
                "max_count": 3,
                "total": 10
            }
        }
        """.data(using: .utf8)!

        let ask = try JSONDecoder().decode(TaskGrantAsk.self, from: json)
        #expect(ask.trigger == "매일 9시")
        #expect(ask.action == "체험단 신청")
        let apply = try #require(ask.apply)
        #expect(apply.site == "example.com")
        #expect(apply.what == "선물 체험단")
        #expect(apply.maxCount == 3)
        #expect(apply.total == 10)
    }

    @Test func decodeScheduleGrantApplyRow() throws {
        let json = """
        {
            "id": 1,
            "kind": "apply",
            "site": "example.com",
            "what": "선물 체험단",
            "max_count": 3,
            "total_count": 10,
            "used": 3
        }
        """.data(using: .utf8)!

        let grant = try JSONDecoder().decode(ScheduleGrant.self, from: json)
        #expect(grant.isApply)
        #expect(grant.totalCount == 10)
        #expect(grant.used == 3)
        #expect(grant.left == 7)
    }
}
