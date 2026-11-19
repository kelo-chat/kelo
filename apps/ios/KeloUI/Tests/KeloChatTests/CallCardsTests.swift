import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// The chat's call cards (owner 2026-11-20): the same cases as CallCardsTest.kt.
@Suite struct CallCardsTests {
    private let t: Int64 = 1_700_000_000_000

    private func msg(_ key: String, mine: Bool, _ text: String, id: Int64? = nil, at: Int64 = 0, call: String? = nil, fromCall: Bool = false) -> ChatItem {
        .msg(.init(key: key, mine: mine, text: text, id: id, at: at, fromCall: fromCall, callId: call))
    }

    @Test func theLinesOfOneCallFoldIntoOneCardAndStayOutOfTheChatAsBubbles() {   // the owner's report
        let a = msg("a", mine: true, "typed", id: 1)
        let m2 = msg("m2", mine: true, "여보세요.", id: 2, at: t, call: "c1", fromCall: true)
        let tool = ChatItem.error(key: "e", ChatError.message("x"))
        let m3 = msg("m3", mine: false, "네, 남호님. 잘 들려요.", id: 3, at: t + 60_000, call: "c1")
        let m4 = msg("m4", mine: true, "나 목소리 너무 끊겨.", id: 4, at: t + 190_000, call: "c1")
        let b = msg("b", mine: true, "typed 2", id: 5)
        let m6 = msg("m6", mine: false, "안녕하세요", id: 6, call: "c2")
        let rows = CallCards.fold([a, m2, tool, m3, m4, b, m6])
        #expect(rows.count == 5)
        #expect(rows[0] == a)
        #expect(rows[1] == .call(ChatItem.CallCard(key: "call:c1:m2", callId: "c1", lines: [m2.asMsg!, m3.asMsg!, m4.asMsg!])))
        #expect(rows[2] == tool)
        #expect(rows[3] == b)
        #expect(rows[4] == .call(ChatItem.CallCard(key: "call:c2:m6", callId: "c2", lines: [m6.asMsg!])))
        #expect(rows.allSatisfy { $0.asMsg?.callId == nil })
    }

    @Test func aCallSplitByAnotherMessageMakesTwoCards() {
        let rows = CallCards.fold([msg("c1a", mine: false, "a", call: "c1"), msg("typed", mine: true, "x"), msg("c1c", mine: false, "c", call: "c1")])
        #expect(rows.compactMap { $0.asCall?.key } == ["call:c1:c1a", "call:c1:c1c"])
        #expect(rows.count == 3)
    }

    @Test func rowsWithoutCallIdsPassThroughAsTheyAre() {
        let items = [msg("a", mine: true, "x"), ChatItem.error(key: "e", ChatError.message("x"))]
        #expect(CallCards.fold(items) == items)
    }

    @Test func newRowsOfACallsReplyTakeItsCallId() {
        let before = [msg("k1", mine: true, "hi", call: "c1", fromCall: true)]
        let after = before + [msg("k2", mine: false, "네")]
        let tagged = CallCards.tagNew(before: before, after: after, callId: "c1")
        #expect(tagged[1].asMsg?.callId == "c1")
        #expect(tagged[1].asMsg?.fromCall == true)
        let old = msg("k0", mine: false, "old")
        #expect(CallCards.tagNew(before: [old], after: [old], callId: "c1")[0].asMsg?.callId == nil)
    }

    @Test func minutesAndPreview() {
        func c(_ at: Int64...) -> ChatItem.CallCard {
            ChatItem.CallCard(key: "k", callId: "c", lines: at.map { ChatItem.Msg(key: "m\($0)", mine: false, text: "x", at: $0) })
        }
        #expect(CallCards.minutes(c(t, t + 40_000)) == 1)
        #expect(CallCards.minutes(c(t, t + 190_000)) == 3)
        #expect(CallCards.minutes(c(t, t + 211_000)) == 4)
        #expect(CallCards.minutes(c(0, t + 600_000)) == 1)
        let folded = ChatItem.CallCard(key: "k", callId: "c", lines: [
            ChatItem.Msg(key: "a", mine: false, text: "네,\n남호님   잘 들려요.", callId: "c"),
            ChatItem.Msg(key: "b", mine: true, text: "owner words", callId: "c"),
        ])
        #expect(CallCards.preview(folded) == "owner words")
        let kelo = ChatItem.CallCard(key: "k", callId: "c", lines: [ChatItem.Msg(key: "a", mine: false, text: "네,\n남호님   잘 들려요.")])
        #expect(CallCards.preview(kelo) == "네, 남호님 잘 들려요.")
        let owners = ChatItem.CallCard(key: "k", callId: "c", lines: [ChatItem.Msg(key: "a", mine: false, text: "네"), ChatItem.Msg(key: "b", mine: true, text: "여보세요  ")])
        #expect(CallCards.preview(owners) == "여보세요")
    }

    @Test func cardWithFindsTheCardHoldingAMessage() {
        let c = ChatItem.CallCard(key: "call:c1:a", callId: "c1", lines: [ChatItem.Msg(key: "a", mine: false, text: "x", id: 7, callId: "c1")])
        let rows: [ChatItem] = [msg("z", mine: true, "y", id: 1), .call(c)]
        #expect(CallCards.cardWith(rows, id: 7) == c)
        #expect(CallCards.cardWith(rows, id: 1) == nil)
    }

    @Test func aStoredMessageReadsItsCall() throws {
        let with = try JSONDecoder().decode(ChatMessage.self, from: Data(#"{"id":5,"role":"user","content":"x","via":"call","call":"c1"}"#.utf8))
        #expect(with.callId == "c1")
        #expect(ChatReducer.item(with).asMsg?.callId == "c1")
        let without = try JSONDecoder().decode(ChatMessage.self, from: Data(#"{"id":5,"role":"user","content":"x","via":"call"}"#.utf8))
        #expect(without.callId == "")
        let encoded = try JSONEncoder().encode(with)
        #expect(try JSONDecoder().decode(ChatMessage.self, from: encoded).callId == "c1")
    }
}
