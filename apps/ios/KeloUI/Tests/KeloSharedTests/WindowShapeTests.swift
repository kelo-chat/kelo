import Testing
import Foundation
import SwiftUI
@testable import KeloShared

/// The window classes and pane rules. The same vectors as the Kotlin side's WindowClass tests (apps/shared, run foldable-shared;
/// those tests did not exist yet when these were written, so these numbers are the brief's and Part 3 of the design).
struct WindowShapeTests {
    @Test func widthClassEdges() {
        let cases: [(CGFloat, WidthClass)] = [
            (0, .narrow), (360, .narrow), (599, .narrow), (599.9, .narrow),
            (600, .middle), (673, .middle), (839, .middle),
            (840, .wide), (841, .wide), (1199, .wide),
            (1200, .roomy), (1280, .roomy), (1366, .roomy),
        ]
        for (w, expected) in cases { #expect(WidthClass.of(width: w) == expected, "width \(w)") }
    }

    @Test func twoPanesNeedWidthAndHeight() {
        #expect(!WindowShape(width: 360, height: 780).twoPanes)
        #expect(!WindowShape(width: 673, height: 841).twoPanes)      // an unfolded foldable, portrait: one centred column
        #expect(WindowShape(width: 841, height: 673).twoPanes)       // landscape: two panes
        #expect(WindowShape(width: 840, height: 480).twoPanes)
        #expect(!WindowShape(width: 840, height: 479).twoPanes)
        #expect(!WindowShape(width: 932, height: 430).twoPanes)      // a phone on its side stays one pane
        #expect(WindowShape(width: 1280, height: 800).twoPanes)
        #expect(WindowShape(width: 834, height: 1194).widthClass == .middle)   // iPad 11" portrait
        #expect(WindowShape(width: 1194, height: 834).twoPanes)                // iPad 11" landscape
    }

    @Test func columnAndBubbleLimits() {
        let phone = WindowShape(width: 412, height: 800)
        #expect(phone.chatColumnMax == nil && phone.bubbleMax == nil && phone.pageColumnMax == nil && phone.tabBarMax == nil)
        #expect(!phone.centredColumn)
        let middle = WindowShape(width: 673, height: 841)
        #expect(middle.chatColumnMax == 720 && middle.bubbleMax == 540 && middle.pageColumnMax == 640 && middle.tabBarMax == 560)
        #expect(middle.centredColumn)
        // the page column and the tab bar cap hold in every class that is not narrow
        for w: CGFloat in [900, 1280] {
            let s = WindowShape(width: w, height: 600)
            #expect(s.pageColumnMax == 640 && s.tabBarMax == 560 && s.bubbleMax == 540 && s.chatColumnMax == 860, "width \(w)")
        }
        #expect(WindowShape(width: 600, height: 400).bubbleMax == 540)
    }

    @Test func sidePaneIsLiveWhenOpenElseWorkAndOnlyOnTheChatTabInTwoPanes() {
        let wide = WindowShape(width: 841, height: 673)
        #expect(wide.side(onChatTab: true, liveOpen: false) == .work)
        #expect(wide.side(onChatTab: true, liveOpen: true) == .live)
        #expect(wide.side(onChatTab: false, liveOpen: true) == .none)
        #expect(WindowShape(width: 673, height: 841).side(onChatTab: true, liveOpen: true) == .none)
    }

    @Test func splitStartsAtFiftyFive() {
        #expect(SplitRules.defaultRatio == 0.55)
        #expect(SplitRules.leftWidth(total: 1000, ratio: 0.55) == 550)
        #expect(SplitRules.leftWidth(total: 841, ratio: SplitRules.defaultRatio) == 463)
    }

    /// The same vectors as the Kotlin firstShare test (WindowRulesTest).
    @Test func dividerKeepsEachPaneAtLeast320AndAWindowTooNarrowKeepsTheDefault() {
        let low = SplitRules.firstShare(wantedFirst: 10, total: 900)
        let high = SplitRules.firstShare(wantedFirst: 890, total: 900)
        let mid = SplitRules.firstShare(wantedFirst: 500, total: 1000)
        let narrow = SplitRules.firstShare(wantedFirst: 100, total: 600)
        #expect(abs(low - 320.0 / 900.0) < 0.0001)
        #expect(abs(high - 580.0 / 900.0) < 0.0001)
        #expect(mid == 0.5)
        #expect(narrow == SplitRules.defaultRatio)
        #expect(SplitRules.leftWidth(total: 1000, ratio: 0.05) == 320)
        #expect(SplitRules.leftWidth(total: 1000, ratio: 0.99) == 680)
        #expect(SplitRules.clamp(0.3, total: 0) == SplitRules.defaultRatio)
    }

    @Test func splitIsKeptPerDevice() {
        let suite = "kelo.test.split.\(UUID().uuidString)"
        let d = UserDefaults(suiteName: suite)!
        defer { d.removePersistentDomain(forName: suite) }
        let prefs = Prefs(defaults: d)
        #expect(SplitRules.stored(prefs) == SplitRules.defaultRatio)
        SplitRules.store(0.62, prefs)
        #expect(SplitRules.stored(prefs) == 0.62)
        prefs.set("ui.split_permille", 0)
        #expect(SplitRules.stored(prefs) == SplitRules.defaultRatio)
    }

    @MainActor @Test func listTabsKeepTheChromeWhenAPageOpensBesideTheList() {
        let r = Router()
        r.go(.my)
        r.paths[.my] = NavigationPath([1])
        #expect(r.headerHidden && r.barHidden)       // one pane: a pushed page has the whole screen
        r.twoPane = true
        #expect(!r.headerHidden && !r.barHidden)     // two panes: it opens in the second pane
        r.go(.chat)
        r.paths[.chat] = NavigationPath([1])
        #expect(r.headerHidden && r.barHidden)       // the chat's own pushed pages still do not
        r.hideChrome("x", true)
        r.go(.my)
        #expect(r.headerHidden && r.barHidden)       // a full-screen asker always wins
    }
}
