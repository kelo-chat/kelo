import Foundation
import Testing
@testable import KeloCore

/// Port of android agent/PageDialogTest.kt: the page's own pop-up in the live view (owner report 2026-11-19).
@Suite struct PageDialogTests {
    private func status(_ json: String) -> LiveStatus? {
        if case .status(let s) = LiveMessages.parse(json) { return s }
        return nil
    }

    @Test func aStatusWithAPopUpCarriesItsLine() throws {
        let s = try #require(status(#"{"type":"status","dialog":{"kind":"confirm","message":"Sign in first?","answer":"cancel","count":2,"at":1.5,"text":"The page asked: “Sign in first?” Kelo answered Cancel."}}"#))
        #expect(s.dialog?.kind == "confirm")
        #expect(s.dialog?.answer == "cancel")
        #expect(s.dialog?.count == 2)
        #expect(LiveRules.dialogLine(s) == "The page asked: “Sign in first?” Kelo answered Cancel.")
    }

    @Test func noPopUpAnOlderServerOrAnEmptyLineShowsNothing() throws {
        #expect(LiveRules.dialogLine(try #require(status(#"{"type":"status"}"#))) == nil)
        #expect(LiveRules.dialogLine(try #require(status(#"{"type":"status","dialog":{"kind":"alert","text":"  "}}"#))) == nil)
    }
}
