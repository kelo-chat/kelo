import Testing
import Foundation
import KeloCore
@testable import KeloChat

private func keys() -> () -> String {
    var n = 0
    return { n += 1; return "k\(n)" }
}

private func msg(_ key: String, mine: Bool, _ text: String, id: Int64? = nil) -> ChatItem { .msg(.init(key: key, mine: mine, text: text, id: id)) }

@Suite struct ChatReducerTests {
    @Test func textGrowsTheNewestReply() {
        let k = keys()
        var items = [msg("m1", mine: true, "hi")]
        items = ChatReducer.apply(items, .text("Hel"), newKey: k)
        items = ChatReducer.apply(items, .text("lo"), newKey: k)
        #expect(items.count == 2)
        #expect(items.last?.asMsg?.text == "Hello")
        #expect(items.last?.asMsg?.mine == false)
    }

    @Test func hiddenToolsShowNothingAndResultsSettleTheChip() {
        let k = keys()
        var items: [ChatItem] = []
        items = ChatReducer.apply(items, .toolStarted(.init(id: "t0", name: "memory_read", hidden: true)), newKey: k)
        #expect(items.isEmpty)
        items = ChatReducer.apply(items, .toolStarted(.init(id: "t1", name: "web_search", label: "Searching")), newKey: k)
        items = ChatReducer.apply(items, .toolResult(.init(id: "t1", name: "web_search", output: "ok", isError: false, approved: nil)), newKey: k)
        guard case .tool(let t) = items[0] else { Issue.record("no chip"); return }
        #expect(t.state == .ok)
        #expect(t.shown == "Searching")
        items = ChatReducer.apply(items, .toolStarted(.init(id: "t2", name: "gmail_send")), newKey: k)
        items = ChatReducer.apply(items, .toolResult(.init(id: "t2", name: "gmail_send", output: "", isError: false, approved: false)), newKey: k)
        if case .tool(let d) = items[1] { #expect(d.state == .denied) }
    }

    @Test func connectAndVaultCardsAppearAndOlderOnesExpire() {
        let k = keys()
        var items: [ChatItem] = []
        for _ in 0..<2 {
            items = ChatReducer.apply(items, .toolStarted(.init(id: "c", name: "connect_service", arguments: ["service": "github"])), newKey: k)
            items = ChatReducer.apply(items, .toolResult(.init(id: "c", name: "connect_service", output: "asked", isError: false, approved: nil)), newKey: k)
        }
        let connects = items.compactMap { if case .connect(let c) = $0 { return c } else { return nil } }
        #expect(connects.count == 2)
        #expect(connects[0].state == .expired && connects[1].state == .open && connects[1].service == "github")
        items = ChatReducer.apply(items, .toolStarted(.init(id: "v", name: "request_login")), newKey: k)
        items = ChatReducer.apply(items, .toolResult(.init(id: "v", name: "request_login", output: "Saved a request for 'login-example-com'.",
                                                          isError: false, approved: nil)), newKey: k)
        let vault = items.compactMap { if case .vault(let v) = $0 { return v } else { return nil } }
        #expect(vault.map(\.name) == ["login-example-com"])
        // a refused request shows no form
        items = ChatReducer.apply(items, .toolStarted(.init(id: "w", name: "request_api_access")), newKey: k)
        let before = items.count
        items = ChatReducer.apply(items, .toolResult(.init(id: "w", name: "request_api_access", output: "'x-key'", isError: false, approved: false)), newKey: k)
        #expect(items.count == before)
        #expect(ChatReducer.settleCard(items, vault[0].key, .done).contains { if case .vault(let v) = $0 { return v.state == .done } else { return false } })
    }

    @Test func approvalsAnsweredHereElsewhereOrNever() {
        let k = keys()
        let ask = TurnEvent.approvalRequest(.init(approvalId: "a1", name: "gmail_send", label: "Sending mail", canRememberSchedule: true,
                                                  lines: [ApprovalLine(label: "To", value: "x@y.z")]))
        var items = ChatReducer.apply([], ask, newKey: k)
        #expect(items[0].asApproval?.open == true)
        #expect(items[0].asApproval?.canRememberSchedule == true)
        #expect(ChatReducer.answer(items, "a1", true)[0].asApproval?.answer == true)
        // answered on another phone: the tool's result says so
        let elsewhere = ChatReducer.apply(items, .toolResult(.init(id: "x", name: "gmail_send", output: "", isError: false, approved: true)), newKey: k)
        #expect(elsewhere.first { $0.asApproval != nil }?.asApproval?.answer == true)
        // the reply ended: nobody can answer it any more
        items = ChatReducer.endTurn(items)
        #expect(items[0].asApproval?.expired == true)
        #expect(Outcomes.of(items[0].asApproval!) == .expired)
        // a payment never offers "keep allowing in this job"
        let pay = ChatReducer.apply([], .approvalRequest(.init(approvalId: "p", name: "confirm_purchase",
            payment: PaymentInfo(site: "shop", summary: "", amount: 1, currency: "KRW", amountText: "1원"), canRememberSchedule: true)), newKey: k)
        #expect(pay[0].asApproval?.canRememberSchedule == false)
    }

    @Test func browserRowsUpdateInPlaceWithAFilmstrip() {
        let k = keys()
        func shot(_ img: String?) -> TurnEvent {
            var o: [String: JSON] = ["type": "browser", "id": "b", "title": "T", "url": "https://s.io", "host": "s.io", "status": "loading",
                                     "width": 10, "height": 5]
            if let img { o["image"] = .string(img) }
            return TurnEvent.parse(.object(o))
        }
        var items: [ChatItem] = []
        for img in ["data:image/png;base64,AA", "data:image/png;base64,AA", nil, "data:image/png;base64,BB", "data:image/png;base64,CC", "data:image/png;base64,DD"] {
            items = ChatReducer.apply(items, shot(img), newKey: k)
        }
        #expect(items.count == 1)
        guard case .browser(let b) = items[0] else { Issue.record("no row"); return }
        #expect(b.recent == ["data:image/png;base64,BB", "data:image/png;base64,CC", "data:image/png;base64,DD"])
        #expect(b.image == "data:image/png;base64,DD")
    }

    @Test func cardNeededRowsAndSending() {
        let k = keys()
        func needed(_ expires: Int) -> TurnEvent {
            TurnEvent.parse(["type": "card_needed", "id": "e", "approval_id": "a", "card": ["id": "c1", "label": "Visa ••4242"],
                             "site": "shop", "amount": 1, "currency": "KRW", "amount_text": "1원", "expires_at": .number(Double(expires))])
        }
        var items = ChatReducer.apply([], needed(100), newKey: k)
        items = ChatReducer.apply(items, needed(200), newKey: k)
        #expect(items.count == 1)
        if case .cardNeeded(let c) = items[0] { #expect(c.expiresAt == 200) }
        items = ChatReducer.cardSend(items, "a", .sent)
        if case .cardNeeded(let c) = ChatReducer.endTurn(items)[0] { #expect(!c.ended) }   // sent: nothing to end
    }

    @Test func phaseKeepsItsStartForTheSamePhase() {
        let p = ChatReducer.phase(Phase(.waiting, since: 1), .thinking, now: 5)
        #expect(p == Phase(.thinking, since: 5))
        #expect(ChatReducer.phase(p, .thinking, now: 9) == p)
        #expect(ChatReducer.phase(p, .toolStarted(.init(id: "1", name: "web_search", label: "Searching")), now: 9) == Phase(.tool, label: "Searching", since: 9))
        #expect(ChatReducer.phase(p, .toolStarted(.init(id: "1", name: "memory", hidden: true)), now: 9) == p)
        #expect(ChatReducer.vaultName("use 'login-x-com' now") == "login-x-com")
        #expect(ChatReducer.isReplies(.unknown("replies")))
    }

    @Test func followupOutcomes() {
        #expect(ChatReducer.outcome(.answer(approvalId: "a", approve: true), .done) == .approved)
        #expect(ChatReducer.outcome(.snooze(followupId: "f", minutes: 30), .done) == .snoozed)
        #expect(ChatReducer.outcome(.drop(followupId: "f"), .gone) == .gone)
        #expect(ChatReducer.outcome(.drop(followupId: "f"), .failed) == nil)
    }
}

@Suite struct ChatRulesTests {
    @Test func messageActionsAndRetry() {
        let items = [msg("u1", mine: true, "first"), msg("r1", mine: false, "one"), msg("u2", mine: true, "second"),
                     msg("r2", mine: false, "two"), ChatItem.error(key: "e", .network)]
        #expect(MessageActions.actions(items, key: "u1", busy: false) == [.copy, .share, .select, .edit])
        #expect(MessageActions.actions(items, key: "r1", busy: false) == [.copy, .share, .select, .report])
        #expect(MessageActions.retryText(items, key: "r1") == nil)
        #expect(MessageActions.retryText(items, key: "e") == "second")
        #expect(MessageActions.actions(items, key: "e", busy: false) == [.copy, .retry])
        #expect(MessageActions.actions(items, key: "e", busy: true) == [.copy])
        let newest = Array(items.dropLast())
        #expect(MessageActions.retryText(newest, key: "r2") == "second")
        #expect(MessageActions.actions(newest, key: "r2", busy: false).contains(.retry))
    }

    @Test func quickReplies() {
        let items = [msg("r1", mine: false, "?"), msg("u1", mine: true, "x"), msg("r2", mine: false, "??")]
        #expect(!QuickReplyRules.isOpen(items, key: "r1"))
        #expect(QuickReplyRules.isOpen(items, key: "r2"))
        let one = ReplySet(questions: [ReplyQuestion(question: "Which?", options: ["A", "B"])])
        #expect(QuickReplyRules.sendsAtOnce(one))
        #expect(QuickReplyRules.compose(one, picks: [0: "A"]) == "A")
        let two = ReplySet(questions: [ReplyQuestion(question: "Size?", options: ["S"]), ReplyQuestion(question: "Color?", options: ["Red"])])
        #expect(!QuickReplyRules.sendsAtOnce(two))
        #expect(QuickReplyRules.compose(two, picks: [1: "Red", 0: "S"]) == "Size?: S\nColor?: Red")
        #expect(QuickReplyRules.compose(two, picks: [1: "Red"]) == "Color?: Red")
    }

    @Test func answersToWriteOpenATextBoxAndSendTheTypedWords() throws {
        let form = ReplySet(questions: [
            ReplyQuestion(question: "체중 (현재 → 목표)", options: ["직접 입력할게요", "말하고 싶지 않아요"], typed: ["직접 입력할게요"], hint: "현재 체중과 목표 체중"),
            ReplyQuestion(question: "기간", options: ["1개월", "3개월", "6개월"]),
            ReplyQuestion(question: "식단 제한", options: ["없어요", "있어요 (직접 입력)", "말하고 싶지 않아요"]),   // an older server: no "typed"
        ])
        #expect(QuickReplyRules.isTyped(form.questions[2], "있어요 (직접 입력)"))
        #expect(!QuickReplyRules.isTyped(form.questions[2], "없어요"))
        for l in ["Other", "기타", "Other (please specify)", "Something else…", "その他", "其他", "Otro", "Свой вариант", "Tự nhập", "I'll type it"] {
            #expect(QuickReplyRules.asksTyping(l), "\(l)")
        }
        for l in ["말하고 싶지 않아요", "없어요", "Rather not say", "Prefiero no decirlo", "Otra vez", "1개월", "Another time", "직접 할게요"] {
            #expect(!QuickReplyRules.asksTyping(l), "\(l)")
        }
        let picks = [0: "직접 입력할게요", 1: "3개월", 2: "있어요 (직접 입력)"]
        #expect(QuickReplyRules.writing(form, picks: picks) == [0, 2])
        #expect(!QuickReplyRules.canSend(form, picks: picks, typed: [:]))
        #expect(!QuickReplyRules.canSend(form, picks: picks, typed: [0: "80 → 70kg", 2: "  "]))
        let typed = [0: " 80kg → 70kg ", 2: "유당불내증\n견과류"]
        #expect(QuickReplyRules.canSend(form, picks: picks, typed: typed))
        #expect(QuickReplyRules.compose(form, picks: picks, typed: typed) == "체중 (현재 → 목표): 80kg → 70kg\n기간: 3개월\n식단 제한: 유당불내증 견과류")
        #expect(QuickReplyRules.compose(form, picks: [1: "1개월", 2: "없어요"], typed: [0: "ignored"]) == "기간: 1개월\n식단 제한: 없어요")
        let one = ReplySet(questions: [ReplyQuestion(question: "Diet", options: ["None", "Other"])])
        #expect(QuickReplyRules.sendsOnTap(one, one.questions[0], "None"))
        #expect(!QuickReplyRules.sendsOnTap(one, one.questions[0], "Other"))
        #expect(!QuickReplyRules.showsSend(one, picks: [:]))
        #expect(QuickReplyRules.showsSend(one, picks: [0: "Other"]))
        #expect(QuickReplyRules.compose(one, picks: [0: "Other"], typed: [0: "gluten free"]) == "gluten free")
        #expect(QuickReplyRules.placeholder(form.questions[0]) == "현재 체중과 목표 체중")
        #expect(QuickReplyRules.placeholder(form.questions[2]) == "식단 제한")
        #expect(QuickReplyRules.placeholder(ReplyQuestion(question: "", options: ["a", "Other"])) == nil)
        let s = try JSONDecoder().decode(ReplySet.self, from: Data(#"{"questions":[{"question":"Q","options":["a","Other"],"typed":["Other"],"hint":"h"}]}"#.utf8))
        #expect(s.questions[0].typed == ["Other"] && s.questions[0].hint == "h")
        let old = try JSONDecoder().decode(ReplySet.self, from: Data(#"{"questions":[{"question":"Q","options":["a","b"]}]}"#.utf8))
        #expect(old.questions[0].typed.isEmpty)
    }

    @Test func scrollAndUnread() {
        #expect(ChatScroll.follow(atBottom: false, lastIsMine: true))
        #expect(!ChatScroll.follow(atBottom: false, lastIsMine: false))
        #expect(!ChatScroll.unread(true, atBottom: true, replyGrew: true))
        #expect(ChatScroll.unread(false, atBottom: false, replyGrew: true))
        #expect(ChatScroll.unread(true, atBottom: false, replyGrew: false))
        let a = ChatScroll.replySignature([msg("r", mine: false, "ab")])
        #expect(a != ChatScroll.replySignature([msg("r", mine: false, "abc")]))
        #expect(a == ChatScroll.replySignature([msg("r", mine: false, "ab"), msg("u", mine: true, "x")]))
    }

    @Test func draftsAndAttachmentText() {
        #expect(DraftText.append("", "hi") == "hi")
        #expect(DraftText.append("a  ", "b") == "a\nb")
        #expect(DraftText.append("a", "  ") == "a")
        let w = AttachmentWords(line: { "📎 \($0)" }, ask: { "Read \($0)." }, separator: ", ")
        #expect(AttachmentText.compose("  ", names: ["a.pdf", "b.png"], words: w) == "Read a.pdf, b.png.")
        #expect(AttachmentText.compose("look", names: ["a.pdf"], words: w) == "look\n\n📎 a.pdf")
        #expect(AttachmentText.compose(" look ", names: [], words: w) == "look")
    }

    @Test func uploadRules() {
        #expect(!UploadRules.tooLarge(99_000_000))
        #expect(UploadRules.tooLarge(99_000_001))
        #expect(UploadRules.tooLarge(150_000_000))
        #expect(UploadRules.target(w: 4000, h: 3000) == (2048, 1536))
        #expect(UploadRules.target(w: 800, h: 600) == (800, 600))
        #expect(UploadRules.jpegName("IMG_1.HEIC", now: 5) == "IMG_1.jpg")
        #expect(UploadRules.jpegName(nil, now: 5) == "photo-5.jpg")
        #expect(UploadRules.stored("uploads/a.pdf", fallback: "x") == "a.pdf")
        #expect(UploadRules.stored("", fallback: "x") == "x")
        #expect(UploadRules.error(ApiError(status: 413, detail: "big")) == .tooLarge)
        #expect(UploadRules.error(ApiError(status: 0, detail: "")) == .network)
        #expect(UploadRules.error(ApiError(status: 415, detail: "PDF only")) == .server("PDF only"))
    }

    @Test func reportBodyHoldsOnlyWhatTheSheetSays() {
        let items = [msg("u", mine: true, "my question"), msg("r", mine: false, "**bad** answer<message_break/>more")]
        #expect(ReportRules.replyText(items, key: "r") == "bad answer\n\nmore")
        #expect(ReportRules.replyText(items, key: "u") == nil)
        #expect(ReportRules.askedBefore(items, key: "r") == "my question")
        var d = ReportDraft()
        #expect(!ReportRules.canSend(d))
        d.reason = .privacy
        d.comment = "  "
        let body = ReportRules.body(d, reply: "bad", asked: "my question", appVersion: "1.0")
        #expect(body["reason"]?.stringValue == "privacy")
        #expect(body["comment"] == nil)
        #expect(body["asked"] == nil)   // only when ticked
        d.includeAsked = true
        #expect(ReportRules.body(d, reply: "bad", asked: "my question", appVersion: "1.0")["asked"]?.stringValue == "my question")
    }

    @Test func smallFormats() {
        #expect(CardCountdown.text(272) == "4:32")
        #expect(CardCountdown.text(-3) == "0:00")
        #expect(SiteTile.letter(host: "www.github.com", title: "") == "G")
        #expect(SiteTile.letter(host: "", title: "  ") == "?")
        #expect(BrowserImage.bytes("data:image/png;base64,AAEC") == Data([0, 1, 2]))
        #expect(BrowserImage.bytes("https://x") == nil)
        #expect(BrowserImage.frameRatio(width: 100, height: 400) == 1)
        #expect(BrowserImage.frameRatio(width: 1000, height: 100) == 2.4)
        let a = SpeechMerge.anchor("hello")
        #expect(SpeechMerge.merge(a, spoken: "  big   world ") == "hello big world")
        #expect(SpeechMerge.merge(SpeechMerge.Anchor(before: "a", after: "."), spoken: "b") == "a b.")
    }

    @Test func revealPacing() {
        var s = RevealPacing.start(100, animate: true)
        #expect(s.shown == 0 && s.target == 100)
        #expect(RevealPacing.start(100, animate: false).done)
        s = RevealPacing.advance(s, dtMs: 100)
        #expect(s.shown > 0 && s.shown <= 100)
        // never more than a second behind
        let far = RevealPacing.advance(RevealPacing.State(shown: 0, target: 10_000, perMs: 0.01), dtMs: 100)
        #expect(far.shown >= 1000 - 0.001)
        #expect(RevealPacing.retarget(RevealPacing.State(shown: 50, target: 50, perMs: 0), 20).shown == 20)
        #expect(abs(RevealPacing.duration(1000) - 700) < 0.001)
    }

    @Test func pullingDownLetsGoOfTheNewest() {
        #expect(ChatScroll.letsGo(dragY: 20))
        #expect(!ChatScroll.letsGo(dragY: -20))
        #expect(!ChatScroll.letsGo(dragY: 0))
    }
}
