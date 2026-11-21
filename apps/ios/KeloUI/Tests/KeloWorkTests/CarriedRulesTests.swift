import Testing
import Foundation
import KeloCore
import KeloShared
@testable import KeloWork

/// Ports of the Android app's CarriedRulesTest: the one list of what Kelo carries.
@Suite struct CarriedRulesTests {
    static let sample = #"""
    {"items":[
      {"key":"follow:cal:x","title":"Pick up the parcel","state":"open","starts_at":1800000000,"all_day":false,"work":null,
       "follow":[{"key":"cal:x","kind":"appointment","title":"Pick up the parcel","starts_at":1800000000,"state":"planned"}]},
      {"key":"work:7","title":"Trip to Busan","state":"open","starts_at":1795000000,"all_day":true,
       "work":{"id":7,"title":"Trip to Busan","category":"","note":"","status":"tracking","kind":"work"},
       "follow":[{"key":"mail:1","kind":"flight","title":"KE123","starts_at":1795000000,"state":"watching"}]},
      {"key":"work:9","title":"Renew the passport","state":"open","starts_at":null,"work":{"id":9,"title":"Renew the passport","status":"tracking","kind":"work"},"follow":[]},
      {"key":"work:3","title":"Old errand","state":"done","starts_at":1700000000,"work":{"id":3,"title":"Old errand","status":"done","kind":"work"},"follow":[]}
    ]}
    """#

    private func items() throws -> [CarriedItem] {
        try JSONDecoder().decode(CarriedList.self, from: Data(Self.sample.utf8)).items
    }

    @Test func readsTheOneListWithItsFollowItemsUnderTheWorkItem() throws {
        let all = try items()
        #expect(all.count == 4)
        #expect(all[1].isWork && !all[0].isWork)
        #expect(CarriedRules.linked(all[1]).map(\.key) == ["mail:1"])
        #expect(CarriedRules.linked(all[0]).map(\.key) == ["cal:x"])
        #expect(all[1].work?.title == "Trip to Busan")
    }

    @Test func openRowsComeFirstNearestDateFirstThenUndatedThenDone() throws {
        #expect(CarriedRules.ordered(try items()).map(\.key) == ["work:7", "follow:cal:x", "work:9", "work:3"])
    }

    @Test func eachThingShowsOnce() throws {
        let keys = CarriedRules.ordered(try items()).map(\.key)
        #expect(keys.count == Set(keys).count)
        #expect(!keys.contains("follow:mail:1"))   // the trip's flight is inside the trip's row
    }

    @Test func theFollowItemsOfAWorkItemAreFoundByItsId() throws {
        let all = try items()
        #expect(CarriedRules.followOf(all, goalId: 7).map(\.key) == ["mail:1"])
        #expect(CarriedRules.followOf(all, goalId: 9).isEmpty)
        #expect(CarriedRules.followOf(all, goalId: 42).isEmpty)
    }

    @Test func aStoppedRowIsNotOpen() throws {
        let stopped = CarriedItem(key: "follow:cal:y", title: "Old", state: "stopped", startsAt: 1, allDay: false, work: nil, follow: [])
        #expect(!CarriedRules.isOpen(stopped))
        #expect(CarriedRules.ordered([stopped]).map(\.key) == ["follow:cal:y"])
    }
}
