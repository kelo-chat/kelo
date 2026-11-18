import Testing
import Foundation
import KeloCore
@testable import KeloChat

/// Android chat/ToolFoldTest.kt: tool chips of one kind in a row fold into one chip; one live browser card per reply and tab; a list on a
/// permission card; a step limit from a newer server shows no error bubble.
@Suite struct ToolFoldTests {
    final class Keys { var n = 0; func next() -> String { n += 1; return "k\(n)" } }

    func run(_ evs: [TurnEvent], start: [ChatItem] = [], keys: Keys) -> [ChatItem] {
        evs.reduce(start) { items, ev in ChatReducer.apply(items, ev, newKey: keys.next) }
    }

    func started(_ id: String, _ name: String, group: String? = nil, done: String = "", _ kv: [String: String] = [:]) -> TurnEvent {
        .toolStarted(.init(id: id, name: name, arguments: kv.mapValues { .string($0) }, label: "\(name)…", group: group ?? name, done: done))
    }
    func ok(_ id: String, _ name: String) -> TurnEvent { .toolResult(.init(id: id, name: name, output: "ok", isError: false, approved: nil)) }
    func failed(_ id: String, _ name: String) -> TurnEvent { .toolResult(.init(id: id, name: name, output: "Error", isError: true, approved: nil)) }
    func browser(_ id: String, _ host: String, _ status: String, tab: String) -> TurnEvent {
        .browser(.init(id: id, title: host, host: host, status: status, image: nil, width: 0, height: 0, tab: tab))
    }
    func tools(_ rows: [ChatItem]) -> [ChatItem.Tool] { rows.compactMap { if case .tool(let t) = $0 { return t }; return nil } }
    func cards(_ rows: [ChatItem]) -> [ChatItem.Browser] { rows.compactMap { if case .browser(let b) = $0 { return b }; return nil } }

    @Test func theSameToolAskedTwiceIsOneChipWithBothCardsUnderIt() {
        let k = Keys()
        let ask = { (id: String) in TurnEvent.approvalRequest(.init(approvalId: id, name: "gcal_create_event", label: "일정 넣는 중")) }
        let items = run([started("c1", "gcal_create_event", done: "일정 {n}개 넣음", ["title": "출국"]), ask("a1"), ok("c1", "gcal_create_event"),
                         started("c2", "gcal_create_event", done: "일정 {n}개 넣음", ["title": "귀국"]), ask("a2"), ok("c2", "gcal_create_event")], keys: k)
        let rows = ChatReducer.foldTools(items)
        #expect(rows.count == 3)
        let chip = tools(rows)[0]
        #expect(chip.members.count == 2)
        let ids: [String] = rows.compactMap { if case .approval(let a) = $0 { return a.id }; return nil }
        #expect(ids == ["a1", "a2"])
        #expect(ChatReducer.foldedText(chip, ChatReducer.doneCount(chip)) == "일정 2개 넣음")
        let split = ChatReducer.foldTools(run([started("c1", "gcal_create_event"), ok("c1", "gcal_create_event"), .text("하나 더"),
                                               started("c2", "gcal_create_event"), ok("c2", "gcal_create_event")], keys: k))
        #expect(tools(split).count == 2)
    }

    @Test func onePhotoLookedAtTwiceCountsOnce() {
        let k = Keys()
        let items = run([started("v1", "view_image", done: "사진 {n}장 봄", ["name": "IMG_1.jpg"]), ok("v1", "view_image"),
                         started("v2", "view_image", done: "사진 {n}장 봄", ["name": "IMG_1.jpg"]), ok("v2", "view_image")], keys: k)
        let chip = tools(ChatReducer.foldTools(items))[0]
        #expect(ChatReducer.foldedText(chip, ChatReducer.doneCount(chip)) == "사진 1장 봄")
        let two = run([started("v3", "view_image", done: "사진 {n}장 봄", ["name": "IMG_2.jpg"]), ok("v3", "view_image")], start: items, keys: k)
        #expect(ChatReducer.doneCount(tools(ChatReducer.foldTools(two))[0]) == 2)
    }

    @Test func consecutiveCallsOfOneKindFoldIntoOneChipThatGrowsInPlace() {
        let k = Keys()
        let reads = (1...6).map { started("r\($0)", "email_read", done: "메일 {n}개 읽음", ["uid": "\($0)"]) }
        var items = run([started("l1", "email_list", done: "메일 {n}번 확인"), ok("l1", "email_list")] + reads, keys: k)
        var rows = ChatReducer.foldTools(items)
        #expect(rows.count == 2)
        let chip = tools(rows)[1]
        #expect(chip.folded && chip.members.count == 6)
        items = run([ok("r1", "email_read"), ok("r2", "email_read"), ok("r3", "email_read")], start: items, keys: k)
        rows = ChatReducer.foldTools(items)
        let half = tools(rows)[1]
        #expect(half.key == chip.key)
        #expect(half.members.filter { $0.state == .ok }.count == 3)
        items = run([ok("r4", "email_read"), ok("r5", "email_read"), ok("r6", "email_read")], start: items, keys: k)
        let done = tools(ChatReducer.foldTools(items))[1]
        #expect(ChatReducer.foldedText(done, 6) == "메일 6개 읽음")
        #expect(done.members.map(\.detail) == ["1", "2", "3", "4", "5", "6"])
        #expect(items.count == 7)
    }

    @Test func everyKindFoldsAndSomethingInBetweenStartsANewChip() {
        let k = Keys()
        let items = run([
            started("s1", "web_search", done: "Searched the web {n} times", ["query": "a"]), ok("s1", "web_search"),
            started("s2", "web_search", ["query": "b"]), failed("s2", "web_search"),
            started("f1", "web_fetch", ["url": "https://www.example.org/x"]), ok("f1", "web_fetch"),
            started("f2", "web_fetch"), ok("f2", "web_fetch"),
            .text("중간 설명"),
            started("s3", "web_search"), ok("s3", "web_search"),
        ], keys: k)
        let chips = tools(ChatReducer.foldTools(items))
        #expect(chips.count == 3)
        #expect(chips.map(\.members.count) == [2, 2, 0])
        #expect(chips[1].members[0].detail == "example.org")
        #expect(ChatReducer.foldedText(chips[0], chips[0].members.filter { $0.state == .ok }.count) == "Searched the web 1 times")
        #expect(ChatReducer.foldedText(chips[1], 2) == "web_fetch… · 2")
        #expect(!chips[2].folded)
    }

    @Test func browserStepsShowAsTheCardNotAsChips() {
        let k = Keys()
        let items = run([
            started("b1", "browser_open", group: "browser"), browser("b1", "a.example", "사이트 여는 중", tab: "owner"), ok("b1", "browser_open"),
            started("b2", "browser_click", group: "browser"), browser("b2", "a.example", "페이지 누르는 중", tab: "owner"), ok("b2", "browser_click"),
            started("b3", "browser_open", group: "browser"), browser("b3", "b.example", "사이트 여는 중", tab: "owner"), ok("b3", "browser_open"),
        ], keys: k)
        let rows = ChatReducer.foldTools(items)
        #expect(rows.count == 1)
        #expect(cards(rows).first?.host == "b.example" && cards(rows).first?.hosts.count == 2)
        let bare = ChatReducer.foldTools(run([started("x1", "browser_open", group: ""), ok("x1", "browser_open"),
                                              started("x2", "browser_read", group: ""), ok("x2", "browser_read")], keys: k))
        #expect(bare.count == 1 && tools(bare).first?.members.count == 2)
    }

    @Test func aNewTabOrANewReplyGetsItsOwnCard() {
        let k = Keys()
        var items = run([browser("b1", "a.example", "s", tab: "owner"), browser("b2", "j.example", "s", tab: "task:w1"),
                         browser("b3", "a.example", "s2", tab: "owner")], keys: k)
        #expect(cards(items).count == 2)
        items = run([browser("b4", "a.example", "s3", tab: "owner")], start: items + [.msg(.init(key: "u", mine: true, text: "또 해줘"))], keys: k)
        #expect(cards(items).count == 2)                                  // one card per tab in the whole list: the owner's card moves here
        #expect(cards(items).last?.status == "s3")
    }

    @Test func aPermissionCardForManyCarriesItsListAndAStepLimitFromANewServerShowsNoErrorBubble() throws {
        let k = Keys()
        let ev = TurnEvent.parse(try JSON.parse("""
            {"type":"approval_request","approval_id":"a1","id":"c1","name":"request_task_permission","arguments":{},"detail":"d","label":"x",
             "ask":"홍보 메일 보낸 곳 37개 모두: 구독 취소. 진행할까요?","items":["보낸 곳 1","보낸 곳 2",""]}
        """))
        guard case .approvalRequest(let a) = ev else { Issue.record("not an approval"); return }
        #expect(a.items == ["보낸 곳 1", "보낸 곳 2"])
        #expect(run([ev], keys: k).first?.asApproval?.items.count == 2)
        let limit = TurnEvent.parse(try JSON.parse(#"{"type":"step_limit","steps":30,"progress":12,"next":"background","job_id":"w1"}"#))
        #expect(limit == .stepLimit(steps: 30, next: "background", progress: 12))
        #expect(run([limit], keys: k).isEmpty)
        if case .error(_, let e)? = run([.stepLimit(steps: 30)], keys: k).first { #expect(e == .stepLimit) } else { Issue.record("no error row") }
        let st = TurnEvent.parse(try JSON.parse(#"{"type":"tool_started","id":"t","name":"email_read","arguments":{},"label":"메일 읽는 중","group":"email_read","done":"메일 {n}개 읽음"}"#))
        guard case .toolStarted(let t) = st else { Issue.record("not a tool"); return }
        #expect(t.done == "메일 {n}개 읽음" && t.group == "email_read")
    }

    @Test func aTaskSetupCardAndAPaymentStepAndAPinNeedRead() throws {
        let grant = TurnEvent.parse(try JSON.parse("""
            {"type":"approval_request","approval_id":"g1","name":"task_allow","urgent":true,"missed":"자리","grant":{"trigger":"표가 나오면",
             "action":"예매","once":true,"purchase":{"max_amount":60000,"max_amount_text":"60,000원","max_count":1,
             "card":{"label":"생활비","last4":"4242","auto_pay":true,"auto_limit":30000,"auto_limit_text":"30,000원"}},
             "change":{"what":"x","where":["connector:notion","site:a.example"]}}}
        """))
        guard case .approvalRequest(let a) = grant, let g = a.grant, let p = g.purchase else { Issue.record("no grant"); return }
        #expect(a.urgent && a.missed == "자리" && g.once)
        #expect(TaskGrantRules.cardLine(p) == .autoLimit && TaskGrantRules.aboveCardLimit(p))
        #expect(g.change.map(TaskGrantRules.places) == "notion, a.example")
        #expect(TaskGrantRules.parse(try JSON.parse(#"{"scope":"x"}"#)) == nil)
        let step = TurnEvent.parse(try JSON.parse("""
            {"type":"payment_step","step_id":"ps_1","kind":"app","state":"waiting","code":"482913","expires_at":1000,"seconds_total":180,
             "order":{"amount_text":"24,000원","items":[{"name":"머그","quantity":2},{"name":"컵"}]},"qr":"http://not-a-data-url"}
        """))
        guard case .paymentStep(let s) = step else { Issue.record("no step"); return }
        #expect(s.items == ["머그 × 2", "컵"] && s.qr == nil && s.open)
        #expect(PaymentStepRules.codeText(s) == "482 913")
        #expect(PaymentStepRules.secondsLeft(s, nowS: 940) == 60)
        #expect(PaymentStepRules.expired(s, nowS: 1000))
        #expect(PaymentStepRules.dots(10) == "●●●●●●●●")
        var ended = s; ended.state = "done"
        #expect(PaymentStepRules.upsert([ended], s) == [ended])   // an older copy never reopens a step this phone saw end
        let need = TurnEvent.parse(try JSON.parse(#"{"type":"pin_needed","approval_id":"a9","site":"shop","pin":{"id":"pin_1","digits":6},"expires_at":5}"#))
        guard case .pinNeeded(let n) = need else { Issue.record("no pin"); return }
        #expect(n.slotId == "a9" && n.pinId == "pin_1")
        var rows = PinNeedRow.upsert([], n)
        rows = PinNeedRow.set(rows, "a9", .working)
        rows = PinNeedRow.upsert(rows, n)
        #expect(rows.count == 1 && rows[0].send == .working)
    }

    /// Android aScreenshotShowsUnderTheReplyAndComesBackWithTheStoredMessage.
    @Test func aScreenshotShowsUnderTheReplyAndComesBackWithTheStoredMessage() throws {
        let img = ChatImage(id: "img_1", url: "/api/chat-images/img_1", width: 800, height: 500, caption: "예치금")
        // before any text: the reply starts with the picture; text that follows joins the same row
        var items = run([.image(img), .text("이 화면이에요.")], keys: Keys())
        #expect(items.count == 1)
        guard case .msg(let m) = items[0] else { Issue.record("not a message"); return }
        #expect(m.text == "이 화면이에요." && !m.mine)
        #expect(m.images.map(\.id) == ["img_1"])
        // after text: under it
        items = run([.text("보세요"), .image(img)], keys: Keys())
        #expect(items.count == 1)
        if case .msg(let m2) = items[0] { #expect(m2.images.count == 1 && m2.text == "보세요") }
        // after the user's own message: a reply row of its own
        items = run([.image(img)], start: [.msg(.init(key: "u", mine: true, text: "보여 줘"))], keys: Keys())
        #expect(items.count == 2)
        // the stored message brings it back
        let stored = try JSON.decode(ChatMessage.self, from: try JSON.parse("""
            {"id": 9, "role": "assistant", "content": "x", "images": [{"id": "img_1", "url": "/api/chat-images/img_1", "width": 8, "height": 5}]}
        """))
        guard case .msg(let back) = ChatReducer.item(stored) else { Issue.record("not a message"); return }
        #expect(back.images.first?.width == 8 && back.images.first?.height == 5)
    }
}
