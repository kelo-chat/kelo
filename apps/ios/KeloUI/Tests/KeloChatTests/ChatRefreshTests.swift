import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// The news check while the newest page is on screen keeps the rows that are not stored messages (ChatReducer.refreshNewest,
/// Android refreshNewest).
@Suite struct ChatRefreshTests {
    private func msg(_ key: String, mine: Bool, _ text: String, id: Int64?) -> ChatItem { .msg(.init(key: key, mine: mine, text: text, id: id)) }
    private func card(_ key: String) -> ChatItem {
        .browser(.init(key: key, id: key, title: "Coupang", host: "coupang.com", status: "", image: nil, tab: "owner"))
    }

    @Test func aBrowserCardAfterTheReplyStaysWhenAMessageIsStoredAfterIt() {
        let shown = [msg("u828", mine: true, "q", id: 828), msg("a829", mine: false, "reply", id: 829), card("card")]
        let stored = [msg("m828", mine: true, "q", id: 828), msg("m829", mine: false, "reply", id: 829),
                      msg("m830", mine: false, "promise line", id: 830)]
        let out = ChatReducer.refreshNewest(shown, stored)
        #expect(out?.map(\.key) == ["u828", "a829", "card", "m830"])
    }

    @Test func noShownMessageOnThePageMeansThePageReplacesTheList() {
        let shown = [msg("old", mine: true, "x", id: 1), card("card")]
        #expect(ChatReducer.refreshNewest(shown, [msg("m900", mine: true, "y", id: 900)]) == nil)
    }

    @Test func aShownMessageThePageNoLongerHasGoesInsideItsRangeAndAnOlderOneStays() {
        let shown = [msg("old", mine: true, "x", id: 5), msg("gone", mine: false, "deleted", id: 7), msg("kept", mine: true, "q", id: 8)]
        let stored = [msg("m6", mine: true, "a", id: 6), msg("m8", mine: true, "q", id: 8)]
        let out = ChatReducer.refreshNewest(shown, stored)
        #expect(out?.map(\.key) == ["old", "kept"])
    }
}
