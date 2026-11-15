import Foundation
import Testing
@testable import KeloCore

@Suite struct TurnEventParseTests {
    @Test func parsesAPoll() throws {
        let poll = TurnPoll.parse(try JSON.parse("""
            {"events":[
              {"type":"thinking"},
              {"type":"text","text":"hi"},
              {"type":"tool_started","id":"t","name":"connect_service","arguments":{"service":"github"}},
              {"type":"tool_result","id":"t","name":"connect_service","output":"ok","is_error":false,"approved":null},
              {"type":"tool_result","id":"u","name":"x","output":"no","is_error":true,"approved":false},
              {"type":"approval_request","approval_id":"a1","name":"send","arguments":{"k":1}},
              {"type":"done","input_tokens":3,"output_tokens":4,"steps":2},
              {"type":"something_new"}
            ],"next":8,"done":true}
        """))
        #expect(poll.next == 8 && poll.done)
        let e = poll.events
        #expect(e[0] == .thinking)
        #expect(e[1] == .text("hi"))
        guard case .toolStarted(let ts) = e[2], case .toolResult(let r1) = e[3], case .toolResult(let r2) = e[4],
              case .approvalRequest(let ap) = e[5] else { Issue.record("wrong kinds"); return }
        #expect(ts.service == "github")
        #expect(r1.approved == nil)
        #expect(r2.approved == false && r2.isError)
        #expect(ap.detail == nil)
        #expect(e[6] == .done(inputTokens: 3, outputTokens: 4, steps: 2))
        #expect(e[7] == .unknown("something_new"))
    }

    @Test func paymentApprovalWithCardAndLines() throws {
        let ev = TurnEvent.parse(try JSON.parse("""
            {"type":"approval_request","approval_id":"p1","id":"call9","name":"confirm_purchase","label":"Pay","site":"shop.example",
             "can_remember":false,"can_remember_schedule":true,"expires_at":1700000000.5,
             "payment":{"site":"shop.example","summary":"책 1권","amount":15000,"amount_text":"₩15,000"},
             "card":{"id":"c1","label":"생활비","brand":"visa","last4":"4242","auto_pay":false,"needs_card":true,"reason":"first_site","reason_text":"처음 결제하는 사이트예요"},
             "lines":[{"label":"Site","value":"shop.example"},{"label":"x","value":"  "},{"label":1,"value":"y"},{"value":"a"},{"value":"b"},{"value":"c"}]}
        """))
        guard case .approvalRequest(let a) = ev else { Issue.record("not an approval"); return }
        #expect(a.toolId == "call9" && a.site == "shop.example" && a.canRememberSchedule && a.expiresAt == 1700000000.5)
        #expect(a.payment == PaymentInfo(site: "shop.example", summary: "책 1권", amount: 15000, currency: "KRW", amountText: "₩15,000"))
        #expect(a.card?.needsCard == true && a.card?.card.last4 == "4242" && a.card?.reason == "first_site")
        // blank values and non-string fields are left out, at most four
        #expect(a.lines == [ApprovalLine(label: "Site", value: "shop.example"), ApprovalLine(label: "", value: "y"),
                            ApprovalLine(label: "", value: "a"), ApprovalLine(label: "", value: "b")])
    }

    @Test func browserEventsAndOthers() throws {
        let shot = TurnEvent.parse(try JSON.parse(#"{"type":"browser","id":"t1","status":"Looking at the site","image":"https://evil/x.png","width":720}"#))
        guard case .browser(let b) = shot else { Issue.record("not a browser card"); return }
        #expect(b.image == nil && b.width == 720)   // only data:image/ URLs are shown
        #expect(TurnEvent.parse(try JSON.parse(#"{"type":"browser_control","id":"owner","controller":"user"}"#)) == .browserControl(id: "owner", controller: "user"))
        #expect(TurnEvent.parse(try JSON.parse(#"{"type":"worker","id":"w1","status":"running","title":"찾기"}"#)) == .worker(id: "w1", status: "running", title: "찾기"))
        #expect(TurnEvent.parse(try JSON.parse(#"{"type":"followup","id":"c1","followup_id":7,"kind":"reply","status":"open"}"#)) == .followup(followupId: "7", status: "open"))
        guard case .paymentAuto(let p) = TurnEvent.parse(try JSON.parse(#"{"type":"payment_auto","id":"x","payment":{"host":"h.example","amount_text":"$3"}}"#)) else {
            Issue.record("not payment_auto"); return
        }
        #expect(p.site == "h.example" && p.card == nil)
        #expect(TurnEvent.parse(.array([])) == .unknown(""))
    }

    @Test func pendingApprovals() throws {
        let list = PendingApproval.parseList(try JSON.parse("""
            {"items":[{"type":"approval_request","approval_id":"a1","name":"browser_click","site":"x.example","can_remember":true,"asked_at":5,"unattended":true,"task_id":3,"can_remember_schedule":true},
                      {"type":"approval_request","approval_id":"","name":"x"},
                      {"type":"approval_request","approval_id":"a2","name":"confirm_purchase","payment":{"amount":1},"can_remember_schedule":true}]}
        """))
        #expect(list.map(\.id) == ["a1", "a2"])
        #expect(list[0].canRemember && list[0].unattended && list[0].taskId == 3 && list[0].askedAt == 5 && list[0].canRememberSchedule)
        #expect(list[1].isPayment && !list[1].canRememberSchedule)   // never for a payment
    }

    /// Android chat/ToolFoldTest.kt aScreenshotShowsUnderTheReplyAndComesBackWithTheStoredMessage: the "image" event and the
    /// stored message's "images".
    @Test func readsAScreenshotAndLeavesOutAForeignUrl() throws {
        let ev = TurnEvent.parse(try JSON.parse("""
            {"type":"image","image_id":"img_1","url":"/api/chat-images/img_1","width":800,"height":500,"caption":"예치금"}
        """))
        guard case .image(let img) = ev else { Issue.record("not an image: \(ev)"); return }
        #expect(img == ChatImage(id: "img_1", url: "/api/chat-images/img_1", width: 800, height: 500, caption: "예치금"))
        // a url that is not the server's own is left out
        let bad = TurnEvent.parse(try JSON.parse(#"{"type":"image","url":"https://x.example/a.png"}"#))
        if case .image = bad { Issue.record("a foreign url was read as a picture") }
        let stored = try JSON.decode(StoredMessage.self, from: try JSON.parse("""
            {"id": 9, "role": "assistant", "content": "x", "images": [{"id": "img_1", "url": "/api/chat-images/img_1", "width": 8, "height": 5}]}
        """))
        #expect(stored.images.count == 1 && stored.images.first?.width == 8)
        #expect(try JSON.decode(StoredMessage.self, from: try JSON.parse(#"{"id": 1}"#)).images.isEmpty)
    }
}

/// A scripted server: each poll takes the next answer (a TurnPoll or an Error); with none left it waits until cancelled.
final class FakeTurnApi: TurnApi, @unchecked Sendable {
    let queue: Box<[Any]>
    let polls = Box<[Int]>([])
    let stopped = Box<[String]>([])
    let started = Box<(String, String)?>(nil)
    let waiting = Box(false)

    init(_ answers: [Any]) { queue = Box(answers) }

    func start(text: String, timezone: String) async throws -> String { started.value = (text, timezone); return "t1" }

    func poll(id: String, after: Int) async throws -> TurnPoll {
        polls.update { $0.append(after) }
        var next: Any?
        queue.update { if !$0.isEmpty { next = $0.removeFirst() } }
        guard let next else {
            waiting.value = true
            while true { try await Task.sleep(nanoseconds: 1_000_000_000) }
        }
        if let p = next as? TurnPoll { return p }
        throw next as! Error
    }

    func stop(id: String) async throws { stopped.update { $0.append(id) } }
}

struct Odd: Error {}

@Suite struct TurnFollowerTests {
    let sleeps = Box<[UInt64]>([])
    func follower(_ api: TurnApi) -> TurnFollower { TurnFollower(api: api) { [sleeps] ms in sleeps.update { $0.append(ms) } } }

    @Test func followsFromNextUntilDone() async throws {
        let api = FakeTurnApi([
            TurnPoll(events: [.thinking, .text("안")], next: 2, done: false),
            TurnPoll(events: [], next: 2, done: false),
            TurnPoll(events: [.text("녕"), .done(inputTokens: 1, outputTokens: 2, steps: 1)], next: 4, done: true),
        ])
        let seen = Box<[TurnEvent]>([])
        try await follower(api).talk(text: "hi", timezone: "Asia/Seoul") { e in seen.update { $0.append(e) } }
        #expect(api.started.value?.0 == "hi" && api.started.value?.1 == "Asia/Seoul")
        #expect(api.polls.value == [0, 2, 2])
        #expect(seen.value.count == 4)
        #expect(api.stopped.value.isEmpty)
    }

    @Test func retriesTransientFailuresAndSaysReconnectingAfterTwo() async throws {
        let api = FakeTurnApi([
            TurnPoll(events: [.text("a")], next: 1, done: false),
            ApiError(status: 0, detail: "offline"), ApiError(status: 502, detail: "bad gateway"), ApiError(status: 429, detail: "slow down"), Odd(),
            TurnPoll(events: [.text("b")], next: 2, done: true),
        ])
        let seen = Box<[TurnEvent]>([])
        try await follower(api).talk(text: "x", timezone: "UTC") { e in seen.update { $0.append(e) } }
        #expect(seen.value == [.text("a"), .reconnecting, .text("b")])
        #expect(api.polls.value == [0, 1, 1, 1, 1, 1])
        #expect(sleeps.value == [500, 1000, 1500, 2000])
    }

    @Test func failureCountResetsAfterAnAnswer() async throws {
        let api = FakeTurnApi([ApiError(status: 500, detail: "x"), TurnPoll(events: [], next: 0, done: false),
                               ApiError(status: 500, detail: "x"), TurnPoll(events: [], next: 0, done: true)])
        let seen = Box<[TurnEvent]>([])
        try await follower(api).talk(text: "x", timezone: "UTC") { e in seen.update { $0.append(e) } }
        #expect(seen.value.isEmpty)
        #expect(sleeps.value == [500, 500])
    }

    @Test func backoffIsCappedAtFourSeconds() {
        #expect(TurnFollower.backoff(1) == 500 && TurnFollower.backoff(8) == 4000 && TurnFollower.backoff(29) == 4000)
    }

    @Test func givesUpAfterThirtyFailuresInARow() async {
        let api = FakeTurnApi(Array(repeating: ApiError(status: 503, detail: "down"), count: 40))
        await #expect(throws: ApiError(status: 503, detail: "down")) { try await follower(api).talk(text: "x", timezone: "UTC") { _ in } }
        #expect(api.polls.value.count == TurnFollower.giveUpAfter)
        #expect(sleeps.value.count == TurnFollower.giveUpAfter - 1)
    }

    @Test func permanentErrorsAreNotRetried() async {
        let api = FakeTurnApi([ApiError(status: 404, detail: "that reply is gone")])
        await #expect(throws: ApiError(status: 404, detail: "that reply is gone")) { try await follower(api).talk(text: "x", timezone: "UTC") { _ in } }
        #expect(api.polls.value.count == 1 && sleeps.value.isEmpty)
    }

    @Test func transientRule() {
        #expect(TurnFollower.isTransient(ApiError(status: 0, detail: "")))
        #expect(TurnFollower.isTransient(ApiError(status: 408, detail: "")))
        #expect(TurnFollower.isTransient(ApiError(status: 429, detail: "")))
        #expect(TurnFollower.isTransient(ApiError(status: 500, detail: "")))
        #expect(TurnFollower.isTransient(Odd()))
        #expect(!TurnFollower.isTransient(ApiError(status: 400, detail: "")))
        #expect(!TurnFollower.isTransient(ApiError(status: 401, detail: "")))
        #expect(!TurnFollower.isTransient(ApiError(status: 404, detail: "")))
    }

    @Test func cancellingStopsTheTurnOnTheServer() async throws {
        let api = FakeTurnApi([TurnPoll(events: [.thinking], next: 1, done: false)])
        let f = follower(api)
        let task = Task { try await f.talk(text: "x", timezone: "UTC") { _ in } }
        while !api.waiting.value { try await Task.sleep(nanoseconds: 5_000_000) }
        task.cancel()
        await #expect(throws: CancellationError.self) { try await task.value }
        #expect(api.stopped.value == ["t1"])
    }

    @Test func realTurnApiPaths() async throws {
        let t = FakeTransport(.ok(200, #"{"id":"abc"}"#), .ok(200, #"{"events":[{"type":"text","text":"x"}],"next":1,"done":true}"#), .ok(200, #"{"ok":true}"#))
        let api = KeloTurnApi(api: KeloApi(baseUrl: "https://k", token: "t", transport: t))
        #expect(try await api.start(text: "안녕", timezone: "Asia/Seoul") == "abc")
        #expect(t.lastBody == #"{"ai_consent":"check","text":"안녕","timezone":"Asia/Seoul"}"#)
        #expect(try await api.poll(id: "abc", after: 0).done)
        #expect(t.last.url?.absoluteString == "https://k/api/turns/abc?after=0&wait=20")
        try await api.stop(id: "abc")
        #expect(t.last.httpMethod == "DELETE")
    }
}

@Suite struct SSETests {
    @Test func readsDataLinesInAnyPieces() {
        let text = "data: {\"type\":\"thinking\"}\n\n: keep-alive\r\n\r\nevent: x\r\ndata: a\r\ndata:b\r\nid: 9\r\n\r\ndata: tail-without-blank"
        var p = SSEParser()
        var got: [SSEMessage] = []
        for b in Array(text.utf8) { got += p.feed([b]) }   // one byte at a time
        #expect(got == [SSEMessage(data: #"{"type":"thinking"}"#), SSEMessage(event: "x", data: "a\nb", id: "9")])
        #expect(p.finish().isEmpty)
    }

    @Test func crThenLfAcrossChunksIsOneLineEnd() {
        var p = SSEParser()
        #expect(p.feed(Array("data: 1\r".utf8)).isEmpty)
        #expect(p.feed(Array("\n\r\n".utf8)) == [SSEMessage(data: "1")])
    }

    @Test func talkStreamYieldsTurnEvents() async throws {
        let body = "data: {\"type\":\"text\",\"text\":\"안\"}\n\ndata: not json\n\ndata: {\"type\":\"done\",\"input_tokens\":1,\"output_tokens\":2,\"steps\":1}\n\n"
        let t = FakeTransport(.ok(200, body))
        var events: [TurnEvent] = []
        for try await e in KeloApi(baseUrl: "https://k", token: "t", transport: t).talkStream(text: "hi", timezone: "UTC") { events.append(e) }
        #expect(events == [.text("안"), .done(inputTokens: 1, outputTokens: 2, steps: 1)])
        #expect(t.last.url?.path == "/api/talk" && t.last.value(forHTTPHeaderField: "Accept") == "text/event-stream")
    }

    @Test func talkStreamErrorsCarryTheDetail() async {
        let t = FakeTransport(.ok(503, #"{"detail":"no model"}"#))
        await #expect(throws: ApiError(status: 503, detail: "no model")) {
            for try await _ in KeloApi(baseUrl: "https://k", token: "t", transport: t).talkStream(text: "hi", timezone: "UTC") {}
        }
    }
}
