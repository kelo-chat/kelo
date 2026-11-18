import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// The views the server shows in the chat (chat_views.py): the cards follow them, one card per tab in the whole list (Android ChatViewsTest).
@Suite struct ChatViewsTests {
    private func msg(_ key: String, mine: Bool, _ text: String, id: Int64?) -> ChatItem { .msg(.init(key: key, mine: mine, text: text, id: id)) }
    private func card(_ tab: String, at: Double = 100, status: String = "old") -> ChatItem.Browser {
        .init(key: "card-\(tab)", id: "c", title: "Coupang", host: "coupang.com", status: status, image: nil, tab: tab, at: at)
    }
    private func view(_ key: String, anchor: Int64, status: String = "보는 중", kind: String = "browser") -> ChatViewItem {
        ChatViewItem(key: key, kind: kind, workerId: "", ownerAsked: false, anchorId: anchor, title: "Coupang", url: "https://coupang.com",
                     host: "coupang.com", status: status, image: nil, width: 0, height: 0)
    }

    @Test func aCardWhoseViewIsListedTakesTheViewsWords() {
        let out = ChatReducer.applyViews([msg("m829", mine: false, "r", id: 829), .browser(card("owner"))],
                                         [view("owner", anchor: 829, status: "새 상태")], checkedAt: 200)
        #expect(out.count == 2)
        guard case .browser(let b) = out[1] else { return #expect(Bool(false), "a card") }
        #expect(b.status == "새 상태")
    }

    @Test func aCardWhoseViewIsGoneGoesWhenItIsOlderThanTheAnswerAndAStillNewOneStays() {
        #expect(ChatReducer.applyViews([msg("m1", mine: true, "x", id: 1), .browser(card("owner", at: 100))], [], checkedAt: 200).count == 1)
        #expect(ChatReducer.applyViews([msg("m1", mine: true, "x", id: 1), .browser(card("owner", at: 199))], [], checkedAt: 200).count == 2)
    }

    @Test func aViewWithoutACardGetsOneRightAfterItsAnchorMessage() {
        let items = [msg("m828", mine: true, "q", id: 828), msg("m829", mine: false, "r", id: 829), msg("m830", mine: false, "p", id: 830)]
        let out = ChatReducer.applyViews(items, [view("task:w1", anchor: 829)], checkedAt: 200)
        #expect(out.map(\.key) == ["m828", "m829", "view:task:w1", "m830"])
        guard case .browser(let b) = out[2] else { return #expect(Bool(false), "a card") }
        #expect(b.tab == "task:w1" && b.title == "Coupang")
    }

    @Test func aViewWhoseAnchorIsOlderThanThePageGoesAtTheTopAndNewerAtTheEnd() {
        let items = [msg("m828", mine: true, "q", id: 828), msg("m829", mine: false, "r", id: 829)]
        #expect(ChatReducer.applyViews(items, [view("owner", anchor: 5)], checkedAt: 200).first?.key == "view:owner")
        #expect(ChatReducer.applyViews(items, [view("owner", anchor: 900)], checkedAt: 200).last?.key == "view:owner")
    }

    @Test func withoutInsertNoCardIsAddedOnAPageThatIsNotTheNewest() {
        #expect(ChatReducer.applyViews([msg("m1", mine: true, "x", id: 1)], [view("owner", anchor: 1)], checkedAt: 200, insert: false).count == 1)
    }

    @Test func aClosedViewTakesItsCardAndAnOpenOneDoesNot() {
        let items: [ChatItem] = [msg("m1", mine: true, "x", id: 1), .browser(card("owner")),
                                 .browser(.init(key: "p", id: "p", title: "", host: "", status: "", image: nil, tab: "android", device: true))]
        let closed = ChatReducer.apply(items, .view(.init(key: "owner", state: "closed", reason: "done")), newKey: { "n" })
        #expect(closed.count == 2)
        let opened = ChatReducer.apply(items, .view(.init(key: "owner", state: "open")), newKey: { "n" })
        #expect(opened == items)
    }

    @Test func aBrowserCardOfAnEarlierReplyMovesToThisReplyWhenTheTabIsUsedAgain() {
        let items: [ChatItem] = [msg("m1", mine: true, "x", id: 1), msg("a2", mine: false, "answer", id: 2), .browser(card("owner")),
                                 msg("m3", mine: true, "again", id: 3)]
        let out = ChatReducer.apply(items, .browser(.init(id: "b9", title: "Coupang", host: "coupang.com", status: "다시 보는 중", tab: "owner")),
                                    newKey: { "n" })
        let cards = out.compactMap { item -> ChatItem.Browser? in if case .browser(let b) = item { return b }; return nil }
        #expect(cards.count == 1 && cards[0].status == "다시 보는 중")
        #expect(out.last?.key == cards[0].key)
    }
}
