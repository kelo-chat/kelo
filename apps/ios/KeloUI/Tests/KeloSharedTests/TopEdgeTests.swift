import Testing
import Foundation
@testable import KeloShared

/// The top area's rest and scrolled states (TopEdge.swift): at rest the page colour, scrolled the system bar material.
struct TopEdgeTests {
    @Test func restIsNotScrolled() {
        // at rest the offset is -topInset (the safe area and a header sit above the content)
        #expect(!TopEdgeRules.scrolled(offset: -100, topInset: 100))
        #expect(!TopEdgeRules.scrolled(offset: -99.7, topInset: 100))
        #expect(!TopEdgeRules.scrolled(offset: 0, topInset: 0))
    }

    @Test func movingPastRestIsScrolled() {
        #expect(TopEdgeRules.scrolled(offset: -90, topInset: 100))
        #expect(TopEdgeRules.scrolled(offset: -99, topInset: 100))
        #expect(TopEdgeRules.scrolled(offset: 400, topInset: 0))
        // pulled down past rest shows nothing under the bar: still the page colour
        #expect(!TopEdgeRules.scrolled(offset: -160, topInset: 100))
    }

    @Test func registryIsScrolledWhileAnyReporterIs() {
        let edge = TopEdge()
        let a = UUID(), b = UUID()
        #expect(!edge.scrolled)
        edge.report(a, true)
        #expect(edge.scrolled)
        edge.report(b, true)
        edge.report(a, false)
        #expect(edge.scrolled)     // b still scrolled
        edge.report(b, false)
        #expect(!edge.scrolled)
        edge.report(b, false)      // reporting the same state again changes nothing
        #expect(!edge.scrolled)
    }

    @Test func stanceDefaultIsOpenAndNotPushed() {
        #expect(TabStance() == TabStance(open: true, pushed: false))
        #expect(TabStance(open: false, pushed: true) != TabStance())
    }
}
