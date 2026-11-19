import Foundation
import Testing
@testable import KeloCore

/// The task window's tabs as chips (brief C1e); mirror of the shared LiveTabsTest.kt.
@Suite struct LiveTabsTests {
    @Test func aSessionItemWithoutTabsParsesToNoTabs() throws {
        let t = try JSONDecoder().decode(BrowserTab.self, from: Data(#"{"id":"owner"}"#.utf8))
        #expect(t.tabs.isEmpty)
    }

    @Test func aSessionItemWithTabsParsesThem() throws {
        let t = try JSONDecoder().decode(BrowserTab.self, from: Data(
            #"{"id":"owner","tabs":[{"id":"t1","title":"A","url":"https://a.example/","active":true},{"id":"t2","title":"","url":"u"}]}"#.utf8))
        #expect(t.tabs.map(\.id) == ["t1", "t2"])
        #expect(t.tabs[0].active)
        #expect(!t.tabs[1].active)
    }

    @Test func chipsShowOnlyWithMoreThanOneTab() {
        #expect(!LiveRules.showTabs([]))
        #expect(!LiveRules.showTabs([LiveTab(id: "t1")]))
        #expect(LiveRules.showTabs([LiveTab(id: "t1"), LiveTab(id: "t2")]))
    }

    @Test func chipsAreTappableOnlyWhileThisDeviceHoldsTheTab() {
        var s = LiveStatus()
        s.youControl = true
        #expect(LiveRules.tabsTappable(s))
        s.youControl = false
        #expect(!LiveRules.tabsTappable(s))
    }

    @Test func onlyATabThatIsNotActiveHasACloseMark() {
        #expect(LiveRules.tabClosable(LiveTab(id: "t2", active: false), tappable: true))
        #expect(!LiveRules.tabClosable(LiveTab(id: "t1", active: true), tappable: true))
        #expect(!LiveRules.tabClosable(LiveTab(id: "t2", active: false), tappable: false))
    }

    @Test func theActionBodyNamesTheAction() {
        #expect(LiveRules.tabAction(close: false) == ["action": .string("activate")])
        #expect(LiveRules.tabAction(close: true) == ["action": .string("close")])
    }
}
