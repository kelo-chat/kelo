import Testing
import Foundation
import KeloCore
@testable import KeloShared

@Suite struct UsageLimitPartsTests {
    private func sub(role: String, windows: [UsageWindow] = [UsageWindow(label: "Session", usedPercent: 10)], error: String? = nil) -> Subscription {
        Subscription(provider: "test", windows: windows, error: error, role: role)
    }

    private func account(role: String) -> ProviderAccount {
        ProviderAccount(provider: "test", role: role)
    }

    @Test func mainAndBackgroundSubscription() {
        let u = MyUsage(subscriptions: [sub(role: "main"), sub(role: "background")])
        let p = UsageLogic.limitParts(u)
        #expect(p.current == sub(role: "main"))
        #expect(p.others.count == 1)
        #expect(p.others.first?.role == "background")
        #expect(p.currentAccount == nil)
        #expect(p.otherAccounts.isEmpty)
        #expect(p.otherCount == 1)
    }

    @Test func onlyApiKeyAccount() {
        let a = account(role: "main")
        let u = MyUsage(accounts: [a])
        let p = UsageLogic.limitParts(u)
        #expect(p.current == nil)
        #expect(p.currentAccount == a)
        #expect(p.others.isEmpty)
        #expect(p.otherAccounts.isEmpty)
        #expect(p.otherCount == 0)
    }

    @Test func olderServerOnlySubscription() {
        let s = Subscription(provider: "claude", windows: [UsageWindow(label: "Session", usedPercent: 50)])
        let u = MyUsage(subscription: s)
        let p = UsageLogic.limitParts(u)
        #expect(p.current != nil)
        #expect(p.current?.role == "main")
        #expect(p.current?.windows == s.windows)
        #expect(p.others.isEmpty)
    }

    @Test func subscriptionWithNoWindowsAndNoErrorIsDropped() {
        let u = MyUsage(subscriptions: [
            sub(role: "main"),
            Subscription(provider: "other", windows: [], error: nil, role: "background")
        ])
        let p = UsageLogic.limitParts(u)
        #expect(p.current == sub(role: "main"))
        #expect(p.others.isEmpty)
        #expect(p.otherCount == 0)
    }
}
