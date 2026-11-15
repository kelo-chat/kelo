import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloMy

@Suite struct ModelEffortRulesTests {
    private func provider(_ json: String) throws -> MyProvider { try JSONDecoder().decode(MyProvider.self, from: Data(json.utf8)) }

    @Test func myModelOffersTheLevelsLikeTheChatSheet() throws {
        let custom = try provider(#"{"name":"custom","effort_levels":["low","medium","high","max"]}"#)
        #expect(ModelEffortRules.effort(custom, facts: nil) == .levels(["", "low", "medium", "high", "max"]))
        #expect(ModelEffortRules.effort(custom, facts: ModelEffort()) == .levels(["", "low", "medium", "high", "max"]))   // says nothing
        #expect(ModelEffortRules.effort(custom, facts: ModelEffort(effortLevels: [], effortNote: "이 서버는 생각 수준 설정을 받지 않아요"))
                == .note("이 서버는 생각 수준 설정을 받지 않아요"))
        #expect(ModelEffortRules.effort(custom, facts: ModelEffort(effortLevels: [])) == .none)
        let agy = try provider(#"{"name":"agy","effort_note":"set by the name"}"#)
        #expect(ModelEffortRules.effort(agy, facts: nil) == .note("set by the name"))
        #expect(ModelEffortRules.effort(nil, facts: nil) == .none)
        #expect(ModelEffortRules.shownLevel(["", "low", "medium", "high", "max"], "xhigh") == "high")
        #expect(ModelEffortRules.shownLevel(["", "low", "medium", "high", "max"], "") == "")
    }

    @Test func theModelListReadsTheNote() throws {
        let l = try JSONDecoder().decode(ModelList.self, from: Data(#"""
            {"provider":"custom","models":["k"],"info":{"k":{"images":false,"effort_levels":[],"effort_note":"no"},"j":{"images":true}}}
            """#.utf8))
        #expect(l.info["k"] == ModelEffort(effortLevels: [], effortNote: "no"))
        #expect(l.info["j"]?.effortLevels == nil)   // the list says nothing about j: the provider's levels apply
    }
}
