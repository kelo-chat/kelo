import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// The emoji on the user's messages (Android chat/ReactionsTest.kt): the event, where it lands, the stored field, what VoiceOver says.
@Suite struct ReactionsTests {
    let golf = "\u{1F3CC}\u{FE0F}"                     // 🏌️
    let runner = "\u{1F3C3}\u{200D}\u{2642}\u{FE0F}"    // 🏃‍♂️

    private func keys() -> () -> String {
        var n = 0
        return { n += 1; return "k\(n)" }
    }

    private func msg(_ item: ChatItem) -> ChatItem.Msg? { if case .msg(let m) = item { return m }; return nil }

    @Test func theEventIsParsed() throws {
        #expect(TurnEvent.parse(try JSON.parse(#"{"type":"reaction","message_id":42,"emoji":"👀"}"#)) == .reaction(messageId: 42, emoji: Reactions.read))
        #expect(TurnEvent.parse(try JSON.parse(#"{"type":"reaction","message_id":7,"emoji":"🏃‍♂️"}"#)) == .reaction(messageId: 7, emoji: runner))
    }

    @Test func readThenTheFinalEmojiLandOnTheJustSentMessage() {
        let k = keys()
        var items: [ChatItem] = [
            .msg(.init(key: "m1", mine: true, text: "old", id: 1, reaction: "\u{2705}")),
            .msg(.init(key: "m2", mine: false, text: "ok", id: 2)),
            .msg(.init(key: "k9", mine: true, text: "주말에 골프 칠까?")),
        ]
        items = ChatReducer.apply(items, .reaction(messageId: 3, emoji: Reactions.read), newKey: k)
        #expect(msg(items[2])?.reaction == Reactions.read)
        items = ChatReducer.apply(items, .text("좋아요"), newKey: k)
        items = ChatReducer.apply(items, .reaction(messageId: 3, emoji: golf), newKey: k)
        #expect(msg(items[2])?.reaction == golf)
        #expect(msg(items[0])?.reaction == "\u{2705}")
        #expect(msg(items[3])?.reaction == "")
    }

    @Test func anEarlierMessageIsFoundByItsId() {
        let items: [ChatItem] = [
            .msg(.init(key: "m5", mine: true, text: "book a table", id: 5, reaction: Reactions.read)),
            .msg(.init(key: "m6", mine: false, text: "what time?", id: 6)),
            .msg(.init(key: "k1", mine: true, text: "7pm", reaction: Reactions.read)),
        ]
        let out = ChatReducer.apply(items, .reaction(messageId: 5, emoji: runner), newKey: keys())
        #expect(msg(out[0])?.reaction == runner)
        #expect(msg(out[2])?.reaction == Reactions.read)
        #expect(Reactions.target(items, messageId: 5) == 0)
        #expect(Reactions.target(items, messageId: 99) == 2)
    }

    @Test func nothingToReactToLeavesTheListAsItIs() {
        let items: [ChatItem] = [.msg(.init(key: "m1", mine: false, text: "hi", id: 1))]
        #expect(ChatReducer.apply(items, .reaction(messageId: 1, emoji: golf), newKey: keys()) == items)
        #expect(Reactions.target(items, messageId: 1) == nil)
    }

    @Test func storedMessagesCarryIt() throws {
        let list = ChatMessage.parseList(try JSON.parse(#"[{"id":1,"role":"user","content":"hi","reaction":"🏌️"},{"id":2,"role":"assistant","content":"yo"}]"#))
        #expect(list[0].reaction == golf)
        #expect(list[1].reaction == "")
        if case .msg(let m) = ChatReducer.item(list[0]) { #expect(m.reaction == golf) } else { Issue.record("not a message") }
    }

    @Test func whatVoiceOverSays() {
        #expect(Reactions.isRead(Reactions.read))
        #expect(!Reactions.isRead(golf))
        #expect(Reactions.spoken(Reactions.read, readWord: "읽음") { "반응: \($0)" } == "읽음")
        #expect(Reactions.spoken(runner, readWord: "Read") { "Reaction: \($0)" } == "Reaction: \(runner)")
    }

    @Test func theChipSitsNearTheBubblesEndCorner() {
        // in points: chip box 26 wide (22 plus its 2 pt halo), 10 in from the end edge (12 to what shows)
        #expect(Reactions.chipStart(bubbleWidth: 300, chipWidth: 26, inset: 10) == 264)
        #expect(Reactions.chipStart(bubbleWidth: 70, chipWidth: 26, inset: 10) == 34)
    }

    @Test func aShortBubbleGetsTheChipCentredUnderIt() {
        #expect(Reactions.chipStart(bubbleWidth: 46, chipWidth: 26, inset: 10) == 10)   // where both rules meet
        #expect(Reactions.chipStart(bubbleWidth: 40, chipWidth: 26, inset: 10) == 7)    // centred: 7 on each side
        #expect(Reactions.chipStart(bubbleWidth: 22, chipWidth: 26, inset: 10) == -2)   // narrower than the chip: centred
        for w in stride(from: CGFloat(0), through: 400, by: 0.5) {
            let x = Reactions.chipStart(bubbleWidth: w, chipWidth: 26, inset: 10)
            #expect(x + 26 <= max(w - 10, (w + 26) / 2))             // never past the end inset (or centred)
            #expect(x >= w - 26 - x - 0.0001)                         // never nearer the start edge than the end edge
        }
    }
}
