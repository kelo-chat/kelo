import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// Picture cards on a choice question: how the server's "cards" are read, the spoken label, the one tap rule a button and a card share,
/// and the in-place swap after a reply (Android ReplyCardsTest, the same cases).
@Suite struct ReplyCardsTests {
    private func question(cards: String?, options: [String] = ["Blue mug", "Green mug"]) throws -> ReplyQuestion {
        let opts = try JSONSerialization.data(withJSONObject: options)
        let cardsPart = cards.map { ",\"cards\":\($0)" } ?? ""
        let text = "{\"question\":\"Which one?\",\"options\":\(String(decoding: opts, as: UTF8.self))\(cardsPart)}"
        return try JSONDecoder().decode(ReplyQuestion.self, from: Data(text.utf8))
    }

    @Test func cardsAreReadWithPictureDetailAndMark() throws {
        let q = try question(cards: """
            [{"image":{"image_id":"img_ab12","url":"/api/chat-images/img_ab12","width":480,"height":320},"detail":"9,900원 · 화","recommended":true},{"detail":"8,500원 · 수"}]
            """)
        let cards = try #require(q.cardList)
        #expect(cards.count == 2)
        #expect(cards[0].image?.url == "/api/chat-images/img_ab12")
        #expect(cards[0].image?.imageId == "img_ab12")
        #expect(cards[0].image?.width == 480)
        #expect(cards[0].recommended)
        #expect(cards[1].image == nil)
        #expect(cards[1].detail == "8,500원 · 수")
    }

    @Test func withoutCardsOrWithAMismatchTheChoiceStaysPlain() throws {
        #expect(try question(cards: nil).cardList == nil)
        #expect(try question(cards: #"[{"detail":"only one"}]"#).cardList == nil)
        #expect(try question(cards: #"[{"detail":"a"},{"detail":"b"},{"detail":"c"}]"#).cardList == nil)
    }

    @Test func emptyCardsShowNothingAndOneEmptyCardKeepsItsPlace() throws {
        #expect(try question(cards: #"[{},{}]"#).cardList == nil)
        // one empty card among others keeps the list, in order
        let cards = try #require(try question(cards: #"[{},{"detail":"x"}]"#).cardList)
        #expect(cards.count == 2)
        #expect(cards[0].isEmpty)
        #expect(cards[1].detail == "x")
    }

    @Test func anUnreadableCardStillLinesUpWithItsOption() throws {
        // a card that is not an object reads as an empty card, so the second option keeps its own card
        let cards = try #require(try question(cards: #"["junk",{"detail":"second"}]"#).cardList)
        #expect(cards.count == 2)
        #expect(cards[1].detail == "second")
    }

    @Test func theSpokenLabelIsOptionDetailRecommended() {
        let c = ReplyCard(detail: "14,800원 · 내일 새벽 도착", recommended: true)
        #expect(ReplyCardRules.label("파란 머그", c, recommended: "추천") == "파란 머그, 14,800원 · 내일 새벽 도착, 추천")
        #expect(ReplyCardRules.label("Green mug", ReplyCard(), recommended: "Recommended") == "Green mug")
        #expect(ReplyCardRules.label("Mug", ReplyCard(detail: "5"), recommended: "Recommended") == "Mug, 5")
    }

    @Test func aCardTapAnswersLikeTheOptionsButton() throws {
        let q = try question(cards: #"[{"detail":"a"},{"detail":"b"}]"#)
        let set = ReplySet(questions: [q])
        // a card tap (ReplyCardList -> QuickReplyRules.tap) for the second option is the same send its button makes: the option's
        // own words, answered as question 0
        #expect(q.cardList?.count == 2)
        #expect(QuickReplyRules.tap(set, 0, q.options[1]) == .send("Green mug", [ReplyAnswer(0, "Green mug")]))
        #expect(QuickReplyRules.tap(set, 0, q.options[0]) == .send("Blue mug", [ReplyAnswer(0, "Blue mug")]))
    }

    @Test func aTapThatPicksTogglesLikeTheButton() {
        let set = ReplySet(questions: [ReplyQuestion(question: "Size?", options: ["S", "M"]), ReplyQuestion(question: "Color?", options: ["Red"])])
        #expect(QuickReplyRules.tap(set, 0, "S") == .pick)
        var picks: [Int: String] = [:]
        picks = QuickReplyRules.toggled(picks, 0, "S")
        #expect(picks == [0: "S"])
        picks = QuickReplyRules.toggled(picks, 0, "M")
        #expect(picks == [0: "M"])
        picks = QuickReplyRules.toggled(picks, 0, "M")
        #expect(picks == [:])
    }

    @Test func anAnswerToWriteOpensABoxInsteadOfSending() {
        let set = ReplySet(questions: [ReplyQuestion(question: "Diet?", options: ["None", "Other"])])
        // the "Other" card (or chip) is an answer to write: a tap picks it, the typed words are sent
        #expect(QuickReplyRules.tap(set, 0, QuickReplyRules.other) == .pick)
    }

    @Test func aTapOutsideTheQuestionsPicksOnly() {
        let set = ReplySet(questions: [ReplyQuestion(question: "Q", options: ["A", "B"])])
        #expect(QuickReplyRules.tap(set, 3, "A") == .pick)
    }

    // ---- the in-place swap after a reply ----

    private func msg(_ key: String, mine: Bool = false, text: String = "", id: Int64? = nil, images: [ChatImage] = []) -> ChatItem {
        .msg(.init(key: key, mine: mine, text: text, id: id, images: images))
    }

    @Test func storedMessagesSwapInPlaceKeepingOrderKeysAndToolRows() {
        let shown: [ChatItem] = [
            msg("u1", mine: true, text: "hi"),
            msg("a-streamed", text: "raw streamed text"),
            .notice(key: "retry", .retry),
            msg("a-second", text: "second streamed"),
        ]
        let stored: [ChatItem] = [
            msg("m10", mine: true, text: "hi", id: 10),
            msg("m11", text: "tidy stored text", id: 11),
            msg("m12", text: "second stored", id: 12),
        ]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["u1", "a-streamed", "retry", "a-second"])
        #expect(merged[1].asMsg?.text == "tidy stored text")
        #expect(merged[1].asMsg?.id == 11)
        #expect(merged[3].asMsg?.text == "second stored")
        #expect(merged[2] == .notice(key: "retry", .retry))
    }

    @Test func aMessageNotStoredYetStaysAsStreamed() {
        // the reply that is still being written has no stored row yet: it must not take an older stored text
        let shown: [ChatItem] = [msg("m5", text: "old", id: 5), msg("a-now", text: "still writing")]
        let stored: [ChatItem] = [msg("m5", text: "old stored", id: 5)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["m5", "a-now"])
        #expect(merged[0].asMsg?.text == "old stored")
        #expect(merged[1].asMsg?.text == "still writing")
        #expect(merged[1].asMsg?.id == nil)
    }

    @Test func shownPicturesSurviveTheSwap() {
        let shot = ChatImage(id: "img_1", url: "/api/chat-images/img_1", width: 10, height: 10, caption: "")
        let other = ChatImage(id: "img_2", url: "/api/chat-images/img_2", width: 10, height: 10, caption: "")
        let shown: [ChatItem] = [msg("a", text: "x", images: [shot, other])]
        let stored: [ChatItem] = [msg("m1", text: "x stored", id: 1, images: [shot])]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged[0].asMsg?.images == [shot, other])
        #expect(merged[0].asMsg?.text == "x stored")
    }

    // Rules of mergeStored (Android ChatHistory.mergeStored): by id, streamed rows by position from the newest end, appended newer rows.

    @Test func theSameListSwapsTextAndKeepsEveryRowIdentity() {
        let shown: [ChatItem] = [msg("k1", mine: true, text: "hi", id: 1), msg("k2", text: "old", id: 2)]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "hi", id: 1), msg("s2", text: "tidy", id: 2)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["k1", "k2"])
        #expect(merged.map { $0.asMsg?.id } == [1, 2])
        #expect(merged[1].asMsg?.text == "tidy")
    }

    @Test func aStoredMessageFromAnotherDeviceInTheMiddleIsNotInserted() {
        // id 9 came from another phone between the two shown messages: rows are swapped by id and nothing moves
        let shown: [ChatItem] = [msg("k1", mine: true, text: "a", id: 1), msg("k3", text: "c", id: 3)]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "a", id: 1), msg("s9", mine: true, text: "b", id: 9), msg("s3", text: "c2", id: 3)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["k1", "k3"])
        #expect(merged[1].asMsg?.text == "c2")
        #expect(!merged.contains { $0.asMsg?.id == 9 })
    }

    @Test func aStreamedReplyTakesItsStoredMessageAmongOthersFromAnotherDevice() {
        // the streamed reply (no id) matches the newest unshown stored reply by position, the other device's message is skipped
        let shown: [ChatItem] = [msg("k1", mine: true, text: "hi", id: 1), msg("a-streamed", text: "raw")]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "hi", id: 1), msg("s9", text: "elsewhere", id: 9), msg("s10", text: "tidy", id: 10)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["k1", "a-streamed"])
        #expect(merged[1].asMsg?.id == 10)
        #expect(merged[1].asMsg?.text == "tidy")
    }

    @Test func aStreamedPairTakesTheStoredPairKeepingKeys() {
        let shown: [ChatItem] = [msg("u", mine: true, text: "question"), msg("a", text: "streamed")]
        let stored: [ChatItem] = [msg("s10", mine: true, text: "question", id: 10), msg("s11", text: "answer", id: 11)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["u", "a"])
        #expect(merged.map { $0.asMsg?.id } == [10, 11])
    }

    @Test func theWalkStopsAtTheFirstAuthorMismatch() {
        // the newest streamed row (theirs) takes the newest stored one (theirs); the next (mine) meets a theirs and stops
        let shown: [ChatItem] = [msg("x", mine: true, text: "mine streamed"), msg("y", text: "theirs streamed")]
        let stored: [ChatItem] = [msg("s5", text: "first", id: 5), msg("s6", text: "second", id: 6)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["x", "y"])
        #expect(merged[0].asMsg?.text == "mine streamed")
        #expect(merged[1].asMsg?.id == 6)
    }

    @Test func storedMessagesNewerThanTheNewestMatchAreAppendedInOrder() {
        let shown: [ChatItem] = [msg("k1", mine: true, text: "a", id: 1), msg("k2", text: "b", id: 2), .notice(key: "n", .retry)]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "a", id: 1), msg("s2", text: "b", id: 2),
                                  msg("s3", mine: true, text: "from the phone", id: 3), msg("s4", text: "its reply", id: 4)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["k1", "k2", "n", "s3", "s4"])
        #expect(merged[3].asMsg?.text == "from the phone")
        #expect(merged[4].asMsg?.id == 4)
    }

    @Test func noMatchLeavesTheRowsAsTheyAre() {
        let shown: [ChatItem] = [msg("a-now", text: "still writing")]
        let stored: [ChatItem] = [msg("s5", mine: true, text: "older", id: 5)]
        #expect(ChatReducer.mergeStored(shown, stored).map(\.key) == ["a-now"])
        #expect(ChatReducer.mergeStored(shown, stored)[0].asMsg?.id == nil)
    }

    // A reply split by a tool chip (live: text, chip, text) is one reply once stored: one bubble (Android ChatMergeTest).

    private func chip(_ key: String) -> ChatItem {
        .tool(.init(key: key, id: "t-\(key)", name: "email_update", state: .ok, service: nil, label: ""))
    }

    @Test func aReplySplitByAToolChipIsOneBubbleAfterReload() {
        let shown: [ChatItem] = [msg("u1", mine: true, text: "q", id: 1), msg("a2", text: "before the tool"), chip("t3"), msg("a4", text: "after the tool")]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "q", id: 1), msg("s2", text: "before the tool\n\nafter the tool", id: 2)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["u1", "t3", "a4"])
        #expect(merged[2].asMsg?.text == "before the tool\n\nafter the tool")
        #expect(merged[2].asMsg?.id == 2)
    }

    @Test func aTurnWithTwoStoredRowsKeepsTwoBubbles() {
        let shown: [ChatItem] = [msg("a2", text: "part one"), chip("t3"), msg("a4", text: "part two")]
        let stored: [ChatItem] = [msg("s2", text: "part one", id: 2), msg("s3", text: "part two", id: 3)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["a2", "t3", "a4"])
        #expect(merged.compactMap(\.asMsg?.text) == ["part one", "part two"])
    }

    @Test func aReplyWithoutAToolIsMatchedAsBefore() {
        let shown: [ChatItem] = [msg("u1", mine: true, text: "q", id: 1), msg("a2", text: "streamed")]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "q", id: 1), msg("s2", text: "the saved reply", id: 2)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["u1", "a2"])
        #expect(merged[1].asMsg?.text == "the saved reply")
    }

    // A card between the parts of one reply (owner report 2026-11-19): the stored row is one bubble, never the first part again.

    private func loginCard(_ key: String) -> ChatItem {
        .secret(.init(key: key, request: SecretRequest(id: "sr-\(key)", kind: SecretRequest.kindLogin, name: "shop")))
    }

    @Test func aReplySplitByALoginCardIsOneBubbleAfterReload() {
        let shown: [ChatItem] = [msg("u1", mine: true, text: "q", id: 1), msg("a2", text: "before the card"), loginCard("s3"),
                                 msg("a4", text: "after the card")]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "q", id: 1), msg("s2", text: "before the card\n\nafter the card", id: 2)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["u1", "s3", "a4"])
        #expect(merged[2].asMsg?.text == "before the card\n\nafter the card")
        #expect(merged[2].asMsg?.id == 2)
    }

    @Test func anyRowsBetweenThePartsOfOneReplyLeaveOneBubble() {
        let shown: [ChatItem] = [msg("u1", mine: true, text: "q", id: 1), msg("a2", text: "one"), loginCard("s3"), msg("a4", text: "two"),
                                 .notice(key: "n5", .retry), chip("t6"), msg("a7", text: "three")]
        let stored: [ChatItem] = [msg("s1", mine: true, text: "q", id: 1), msg("s2", text: "one\n\ntwo\n\nthree", id: 2)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["u1", "s3", "n5", "t6", "a7"])
        #expect(merged.compactMap(\.asMsg?.text) == ["q", "one\n\ntwo\n\nthree"])
    }

    @Test func twoStoredRowsAroundACardKeepTwoBubbles() {
        let shown: [ChatItem] = [msg("a2", text: "part one"), loginCard("s3"), msg("a4", text: "part two")]
        let stored: [ChatItem] = [msg("s2", text: "part one", id: 2), msg("s3m", text: "part two", id: 3)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["a2", "s3", "a4"])
        #expect(merged.compactMap(\.asMsg?.text) == ["part one", "part two"])
    }

    @Test func aStoredMessageBetweenTwoStreamedBubblesStillEndsTheReply() {
        let shown: [ChatItem] = [msg("a1", text: "older streamed"), msg("m5", text: "stored", id: 5), loginCard("s6"),
                                 msg("a7", text: "newest streamed")]
        let stored: [ChatItem] = [msg("s5", text: "stored", id: 5), msg("s6m", text: "newest saved", id: 6)]
        let merged = ChatReducer.mergeStored(shown, stored)
        #expect(merged.map(\.key) == ["a1", "m5", "s6", "a7"])
        #expect(merged.compactMap(\.asMsg?.text) == ["older streamed", "stored", "newest saved"])
    }
}
