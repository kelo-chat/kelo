import Testing
import Foundation
@testable import KeloCore

// Search across the chat history: the shared rules (query trimming, paging, loaded or not, snippet parts). Android's
// MessageSearchRules decides the same things.

@Suite struct MessageSearchTests {
    private func hit(_ id: Int64, _ snippet: String = "x") -> MessageHit { MessageHit(id: id, role: "user", snippet: snippet) }

    // ---- query -----------------------------------------------------------------------------------------------------------

    @Test func aBlankQueryMakesNoRequest() {
        #expect(MessageSearchRules.query("") == nil)
        #expect(MessageSearchRules.query("   \n\t ") == nil)
    }

    @Test func theQuerySentIsTrimmedOfSpacesAndLineBreaks() {
        #expect(MessageSearchRules.query("  날씨 \n") == "날씨")
        #expect(MessageSearchRules.query("a b") == "a b")   // inside the words nothing changes
        #expect(MessageSearchRules.query("a   \t b") == "a b")   // inner whitespace collapses to one space
    }

    // ---- paths ------------------------------------------------------------------------------------------------------------

    @Test func searchPathCarriesTheQueryLimitAndBefore() {
        #expect(MessageSearchRules.searchPath(query: "hi") == "/api/messages/search?q=hi&limit=20")
        #expect(MessageSearchRules.searchPath(query: "hi", limit: 5, before: 42) == "/api/messages/search?q=hi&limit=5&before=42")
    }

    @Test func theSameQueryIsSentWhateverItsSpacing() {
        #expect(MessageSearchRules.searchPath(query: "  a   b ") == MessageSearchRules.searchPath(query: "a b"))
    }

    @Test func theQueryIsPercentEncodedAsOneValue() {
        let p = MessageSearchRules.searchPath(query: "a&b c")
        #expect(p == "/api/messages/search?q=a%26b%20c&limit=20")
    }

    @Test func limitsStayInTheServersRange() {
        #expect(MessageSearchRules.clampLimit(0) == 1)
        #expect(MessageSearchRules.clampLimit(500) == 50)
        #expect(MessageSearchRules.searchPath(query: "x", limit: 999).hasSuffix("limit=50"))
    }

    @Test func aroundPathAsksForTheWindow() {
        #expect(MessageSearchRules.aroundPath(id: 7) == "/api/messages/around?id=7&n=10")
        #expect(MessageSearchRules.aroundPath(id: 7, n: 3) == "/api/messages/around?id=7&n=3")
    }

    // ---- paging -----------------------------------------------------------------------------------------------------------

    @Test func aFullPageMovesBeforeItsSmallestId() {
        let page = [hit(90), hit(70), hit(80)]
        #expect(MessageSearchRules.nextBefore(page, limit: 3) == 70)
    }

    @Test func aShortPageIsTheEnd() {
        #expect(MessageSearchRules.nextBefore([hit(5), hit(4)], limit: 3) == nil)
        #expect(MessageSearchRules.nextBefore([], limit: 3) == nil)
    }

    @Test func aPageThatOverlapsTheShownHitsAddsNothingTwice() {
        let shown = [hit(9), hit(8), hit(7)]
        let next = MessageSearchRules.appendPage(shown, [hit(7), hit(6), hit(5)])
        #expect(next.map(\.id) == [9, 8, 7, 6, 5])
    }

    // ---- opening a hit ----------------------------------------------------------------------------------------------------

    @Test func aLoadedHitIsScrolledToAndAnUnloadedOneIsFetched() {
        #expect(MessageSearchRules.opening(12, loaded: [11, 12, 13]) == .scroll)
        #expect(MessageSearchRules.opening(3, loaded: [11, 12, 13]) == .fetchWindow)
        #expect(MessageSearchRules.opening(3, loaded: []) == .fetchWindow)
    }

    // ---- snippets ---------------------------------------------------------------------------------------------------------

    @Test func aSnippetSplitsIntoPlainAndMatchedPieces() {
        let parts = MessageSearchRules.parts("어제 «날씨» 좋았어")
        #expect(parts == [.init(text: "어제 ", matched: false), .init(text: "날씨", matched: true),
                           .init(text: " 좋았어", matched: false)])
    }

    @Test func severalMatchesInOneSnippetStayApart() {
        let parts = MessageSearchRules.parts("«a» and «b»")
        #expect(parts.filter(\.matched).map(\.text) == ["a", "b"])
        #expect(parts.filter { !$0.matched }.map(\.text) == [" and "])
    }

    @Test func markersAreNeverShownAndAnUnclosedMatchRunsToTheEnd() {
        #expect(MessageSearchRules.parts("plain » stray").map(\.text).joined() == "plain  stray")
        #expect(MessageSearchRules.parts("«open match").map(\.text).joined() == "open match")
        #expect(MessageSearchRules.parts("«open match").first?.matched == true)
    }

    @Test func aSnippetWithoutMatchesIsOnePlainPiece() {
        #expect(MessageSearchRules.parts("nothing here") == [.init(text: "nothing here", matched: false)])
        #expect(MessageSearchRules.parts("").isEmpty)
    }

    // ---- the server's JSON --------------------------------------------------------------------------------------------------

    @Test func hitsDecodeFromTheServersList() throws {
        let json = #"[{"id": 12, "role": "assistant", "snippet": "a «b» c", "created_at": 1700000000.5}, {"id": "x"}, {"role": "user"}]"#
        let hits = MessageHit.parseList(try JSON.parse(json))
        #expect(hits.map(\.id) == [12])
        #expect(hits.first?.role == "assistant")
        #expect(hits.first?.snippet == "a «b» c")
        #expect(hits.first?.createdAt == 1700000000.5)
    }
}
