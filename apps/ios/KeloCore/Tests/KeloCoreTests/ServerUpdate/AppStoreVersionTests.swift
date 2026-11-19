import Testing
import Foundation
@testable import KeloCore

// The App Store's own answer about the newest version (iTunes lookup), docs/version-notices.md part 3 (store builds).
@Suite struct AppStoreVersionTests {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)
    private func ago(_ hours: Double) -> Date { now.addingTimeInterval(-hours * 3600) }

    @Test func theLookupAsksTheDevicesStorefront() {
        let url = AppStoreVersionRules.lookupURL(bundleId: "chat.kelo.app", country: "kr")
        #expect(url.absoluteString == "https://itunes.apple.com/lookup?bundleId=chat.kelo.app&country=KR")
    }

    @Test func theVersionIsReadFromTheFirstResult() {
        let body = Data(#"{"resultCount":1,"results":[{"version":"1.0.3","bundleId":"chat.kelo.app"}]}"#.utf8)
        #expect(AppStoreVersionRules.parse(body) == "1.0.3")
        #expect(AppStoreVersionRules.parse(Data(#"{"resultCount":0,"results":[]}"#.utf8)) == nil)   // not in this storefront
        #expect(AppStoreVersionRules.parse(Data("<html>".utf8)) == nil)
        #expect(AppStoreVersionRules.parse(Data(#"{"results":[{"version":"  "}]}"#.utf8)) == nil)
    }

    @Test func aNewerStoreVersionCountsNumerically() {
        #expect(AppStoreVersionRules.newer("1.0.10", than: "1.0.9"))
        #expect(AppStoreVersionRules.newer("1.0.3", than: "1.0.2"))
        #expect(!AppStoreVersionRules.newer("1.0.2", than: "1.0.2"))
        #expect(!AppStoreVersionRules.newer("1.0.1", than: "1.0.2"))
        #expect(!AppStoreVersionRules.newer("", than: "1.0.2"))
        #expect(!AppStoreVersionRules.newer("1.0.2", than: ""))   // an unknown running version is never judged
    }

    @Test func theStoreIsAskedAtMostEverySixHours() {
        #expect(AppStoreVersionRules.due(lastCheck: nil, now: now))
        #expect(!AppStoreVersionRules.due(lastCheck: ago(5.9), now: now))
        #expect(AppStoreVersionRules.due(lastCheck: ago(6), now: now))
        #expect(AppStoreVersionRules.due(lastCheck: ago(-1), now: now))   // a clock set back counts as due
    }

    @Test func theCacheKeepsTheTimeAndTheVersion() {
        let text = AppStoreVersionRules.encode(checked: now, version: "1.0.3")
        #expect(AppStoreVersionRules.decode(text)?.version == "1.0.3")
        #expect(AppStoreVersionRules.decode(text)?.checked == now)
        #expect(AppStoreVersionRules.decode("garbage") == nil)
    }
}
