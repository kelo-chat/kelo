import Testing
@testable import KeloMy

@Suite struct NotifyBundleTests {
    @Test func oneGroup() { #expect(NotifyRules.thread == "kelo") }

    @Test func onlyNewsIsTakenDown() {
        #expect(NotifyRules.isNews(.replies) && NotifyRules.isNews(.schedules) && NotifyRules.isNews(.payments))
        #expect(!NotifyRules.isNews(.approvals) && !NotifyRules.isNews(.urgent))
    }

    @Test func newsIdsFromUserInfo() {
        let ids = NotifyRules.newsToTakeDown([
            (id: "a", userInfo: ["channel": "replies"]),
            (id: "b", userInfo: ["channel": "approvals"]),
            (id: "c", userInfo: ["dial": "tel:1"]),
            (id: "d", userInfo: ["channel": "payments"]),
        ])
        #expect(ids == ["a", "d"])
    }
}
