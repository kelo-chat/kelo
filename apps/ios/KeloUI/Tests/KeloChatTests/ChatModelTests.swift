import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// A scripted server that answers by method and path; every request is kept. Nothing touches the network.
final class RouteTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [URLRequest] = []
    let route: @Sendable (String, String, Data?) -> (Int, String)

    init(_ route: @escaping @Sendable (_ method: String, _ path: String, _ body: Data?) -> (Int, String)) { self.route = route }

    var requests: [URLRequest] { lock.withLock { log } }

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        lock.withLock { log.append(request) }
        let url = request.url!
        let path = url.path + (url.query.map { "?" + $0 } ?? "")
        let (code, body) = route(request.httpMethod ?? "GET", path, request.httpBody)
        return (code, Data(body.utf8))
    }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try await send(request)
        return (code, AsyncThrowingStream { c in for b in data { c.yield(b) }; c.finish() })
    }
}

final class FakeUploader: ChatUploader, @unchecked Sendable {
    private let lock = NSLock()
    private var names: [String] = []
    var uploaded: [String] { lock.withLock { names } }

    func upload(api: KeloApi, name: String, source: UploadSource, type: String?,
                progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String {
        // like the real uploader against an old server: one request carries up to 99 MB
        if case .stream(let size, _) = source, size > 99_000_000 { throw UploadTooLarge(limit: 99_000_000) }
        lock.withLock { names.append(name) }
        progress(10, 10)
        return "uploads/" + name
    }
}

@MainActor
private func makeApp(_ route: @escaping @Sendable (String, String, Data?) -> (Int, String)) -> (AppModel, RouteTransport) {
    let t = RouteTransport(route)
    let defaults = UserDefaults(suiteName: "kelo-chat-test-\(UUID().uuidString)")!
    let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")), prefs: Prefs(defaults: defaults),
                       transport: t, cache: ScreenCache(directory: nil, key: { nil }))
    return (app, t)
}

private func until(_ cond: @MainActor () -> Bool) async {
    for _ in 0..<200 {
        if await cond() { return }
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
}

@Suite(.serialized) struct ChatModelTests {
    @MainActor @Test func aReplyIsFollowedToTheEnd() async {
        let (app, t) = makeApp { method, path, _ in
            switch (method, path) {
            case ("GET", "/api/profile"): return (200, #"{"agent_name": "Kelo", "provider": "anthropic", "model": "m"}"#)
            case ("POST", "/api/turns"): return (200, #"{"id": "t1"}"#)
            case ("GET", let p) where p.hasPrefix("/api/turns/t1"):
                return (200, #"{"events": [{"type": "thinking"}, {"type": "tool_started", "id": "1", "name": "web_search", "label": "Searching"}, {"type": "tool_result", "id": "1", "name": "web_search", "output": "ok"}, {"type": "text", "text": "Hel"}, {"type": "text", "text": "lo"}, {"type": "done"}], "next": 6, "done": true}"#)
            default: return (200, "[]")
            }
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        var events: [ChatEvent] = []
        m.onEvent = { events.append($0) }
        m.setInput("  hi there ")
        #expect(m.canSend)
        m.send()
        #expect(m.busy)
        #expect(m.input == "")
        #expect(app.activity.following)
        await m.waitForTurn()
        #expect(!m.busy)
        #expect(m.phase == nil)
        #expect(m.items.first?.asMsg?.text == "hi there")
        #expect(m.items.last?.asMsg?.text == "Hello")
        #expect(m.items.contains { if case .tool(let c) = $0 { return c.state == .ok && c.shown == "Searching" } else { return false } })
        #expect(!app.activity.following)
        #expect(app.activity.doneAt != nil)
        #expect(events.contains(.sent) && events.contains(.replyDone))
        let turn = t.requests.first { $0.url?.path == "/api/turns" && $0.httpMethod == "POST" }
        let sent = String(decoding: turn?.httpBody ?? Data(), as: UTF8.self)
        #expect(sent.contains("hi there"))
        #expect(turn?.value(forHTTPHeaderField: "Authorization") == "Bearer tok")
    }

    @MainActor @Test func noModelIsSaidInWords() async {
        let (app, _) = makeApp { method, path, _ in
            if method == "POST" && path == "/api/turns" { return (400, #"{"detail": {"code": "model_required", "message": "no model chosen"}}"#) }
            return (200, path == "/api/profile" ? "{}" : "[]")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.send("hello")
        await m.waitForTurn()
        #expect(m.items.last == .error(key: m.items.last!.key, .pickModel("no model chosen")))
        #expect(!m.busy)
    }

    /// Only the server's "no model chosen" code shows the plain pick-a-model words; any other 400 says its own (Android).
    @MainActor @Test func another400SaysItsOwnWords() async {
        let (app, _) = makeApp { method, path, _ in
            if method == "POST" && path == "/api/turns" { return (400, #"{"detail": "that file is too big"}"#) }
            return (200, path == "/api/profile" ? "{}" : "[]")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.send("hello")
        await m.waitForTurn()
        #expect(m.items.last == .error(key: m.items.last!.key, .message("that file is too big")))
    }

    @MainActor @Test func aFileOverWhatTheRouteTakesNeverGoesUp() async {
        let (app, _) = makeApp { _, path, _ in (200, path == "/api/profile" ? "{}" : "[]") }
        let up = FakeUploader()
        let m = ChatModel(app: app, uploader: up)
        m.attach(image: false, label: "big.mov") {
            PreparedUpload(name: "big.mov", source: .stream(size: 150_000_000, open: { InputStream(data: Data()) }), type: "video/quicktime", length: 150_000_000)
        }
        m.attach(image: false, label: "a.pdf") {
            PreparedUpload(name: "a.pdf", source: .data(Data([1, 2])), type: "application/pdf", length: 2)
        }
        await until { m.attachments.allSatisfy { $0.state != .uploading } }
        #expect(m.attachments.first?.state == .failed)
        #expect(m.attachments.first?.error == .tooLarge(99_000_000))
        #expect(m.attachments.last?.state == .done)
        #expect(m.attachments.last?.stored == "a.pdf")
        #expect(up.uploaded == ["a.pdf"])
        // the message names the attachment that went up
        m.removeAttachment(m.attachments.first!.id)
        #expect(m.canSend)
    }

    @MainActor @Test func approvalsAreAnsweredOnce() async {
        let (app, t) = makeApp { _, path, _ in (200, path == "/api/profile" ? "{}" : "{}") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.apply(.approvalRequest(.init(approvalId: "a1", name: "calendar_add", canRememberSchedule: true)))
        m.approve("a1", ok: true, rememberSchedule: true)
        m.approve("a1", ok: false)   // already answered: nothing more
        await until { t.requests.contains { $0.url?.path == "/api/approvals/a1" } }
        let posts = t.requests.filter { $0.url?.path == "/api/approvals/a1" }
        #expect(posts.count == 1)
        let body = String(decoding: posts.first?.httpBody ?? Data(), as: UTF8.self)
        #expect(body.contains("\"approve\":true") && body.contains("schedule"))
        #expect(m.items.first?.asApproval?.answer == true)
    }

    @MainActor @Test func alwaysForThisRecipientSendsRememberRecipientOnlyWhenTheCardOfferedIt() async {
        let (app, t) = makeApp { _, _, _ in (200, "{}") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.apply(.approvalRequest(.init(approvalId: "r1", name: "send_email", offers: [TrailOffer(recipient: "lee@else.example", kind: "mail", titles: ["Trip plan"])])))
        #expect(m.items.first?.asApproval?.offers.count == 1)
        m.approve("r1", ok: true, rememberRecipient: true)
        m.apply(.approvalRequest(.init(approvalId: "r2", name: "send_email")))   // no offers: the flag is ignored
        m.approve("r2", ok: true, rememberRecipient: true)
        await until { t.requests.contains { $0.url?.path == "/api/approvals/r1" } && t.requests.contains { $0.url?.path == "/api/approvals/r2" } }
        let b1 = String(decoding: t.requests.first { $0.url?.path == "/api/approvals/r1" }?.httpBody ?? Data(), as: UTF8.self)
        let b2 = String(decoding: t.requests.first { $0.url?.path == "/api/approvals/r2" }?.httpBody ?? Data(), as: UTF8.self)
        #expect(b1.contains("\"approve\":true") && b1.contains("\"remember\":\"recipient\""))
        #expect(!b2.contains("remember"))
    }

    @MainActor @Test func aPaymentAllowIsSignedAndAnOwnerWhoBacksOutSendsNothing() async {
        let (app, t) = makeApp { _, _, _ in (200, "{}") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        let keep = ApprovalHooks.signer
        defer { ApprovalHooks.signer = keep }
        let defaults = UserDefaults(suiteName: "approval-chat-\(UUID().uuidString)")!
        // the owner says no to the check: nothing goes to the server, the card stays open
        ApprovalHooks.signer = SoftwareApprovalSigner(defaults: defaults, allow: { _ in false })
        m.apply(.approvalRequest(.init(approvalId: "p1", name: "confirm_purchase", issuedAt: 1789000000, signable: true)))
        m.approve("p1", ok: true)
        try? await Task.sleep(nanoseconds: 200_000_000)
        let sent = t.requests.filter { $0.url?.path == "/api/approvals/p1" }
        #expect(sent.isEmpty)
        #expect(m.items.first?.asApproval?.open == true)
        // the owner agrees: the Allow carries a signature that verifies, and the card is answered
        let signer = SoftwareApprovalSigner(defaults: defaults)
        ApprovalHooks.signer = signer
        m.approve("p1", ok: true)
        await until { t.requests.contains { $0.url?.path == "/api/approvals/p1" } }
        let body = String(decoding: t.requests.first { $0.url?.path == "/api/approvals/p1" }?.httpBody ?? Data(), as: UTF8.self)
        let json = try? JSON.parse(Data(body.utf8))
        let sig = json?["sig"]?.stringValue ?? ""
        #expect(ApprovalSignRules.verify(publicKey: signer.publicKey()!, message: ApprovalSignRules.message(approvalId: "p1", approve: true, issuedAt: 1789000000), sig: sig))
        await until { m.items.first?.asApproval?.answer == true }
        #expect(m.items.first?.asApproval?.answer == true)
    }

    @MainActor @Test func aDenyAndAnUnsignedCardAreNeverSigned() async {
        let (app, t) = makeApp { _, _, _ in (200, "{}") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        let keep = ApprovalHooks.signer
        defer { ApprovalHooks.signer = keep }
        ApprovalHooks.signer = SoftwareApprovalSigner(defaults: UserDefaults(suiteName: "approval-chat-\(UUID().uuidString)")!)
        m.apply(.approvalRequest(.init(approvalId: "p2", name: "confirm_purchase", issuedAt: 5, signable: true)))
        m.approve("p2", ok: false)
        m.apply(.approvalRequest(.init(approvalId: "p3", name: "calendar_add")))
        m.approve("p3", ok: true)
        await until { t.requests.filter { $0.url?.path.hasPrefix("/api/approvals/p") == true }.count == 2 }
        for r in t.requests where r.url?.path.hasPrefix("/api/approvals/p") == true {
            #expect(!String(decoding: r.httpBody ?? Data(), as: UTF8.self).contains("\"sig\""))
        }
    }

    @MainActor @Test func withoutAWalletACardCannotBeSentFromThisPhone() async {
        let (app, _) = makeApp { _, path, _ in (200, path == "/api/profile" ? "{}" : "{}") }
        let m = ChatModel(app: app, uploader: FakeUploader())
        let card = CardBrief(id: "c", label: "Visa", brand: "visa", last4: "1")
        m.apply(.approvalRequest(.init(approvalId: "p", name: "confirm_purchase",
                                       payment: PaymentInfo(site: "s", summary: "", amount: 1, currency: "KRW", amountText: "1원"))))
        _ = card
        m.approveWithCard("p", cardId: "c")
        await until { m.items.first?.asApproval?.send != .working }
        #expect(m.items.first?.asApproval?.send == .failed(.notOnPhone))
        #expect(m.items.first?.asApproval?.open == true)   // the question stays open: say no, or try again
    }

    @MainActor @Test func historyIsKeptForTheNextOpen() async {
        let (app, _) = makeApp { _, path, _ in
            if path.hasPrefix("/api/messages") { return (200, #"[{"id": 1, "role": "user", "content": "a"}, {"id": 2, "role": "assistant", "content": "b"}]"#) }
            return (200, path == "/api/profile" ? "{}" : "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await until { m.loaded }
        #expect(m.items.map(\.key) == ["m1", "m2"])
        let again = ChatModel(app: app, uploader: FakeUploader())
        #expect(again.loaded && again.items.count == 2)   // shown at once from the cache
        m.setInput("draft text")
        m.saveDraftNow()
        #expect(ChatModel(app: app, uploader: FakeUploader()).input == "draft text")
    }

    @MainActor @Test func clearingTheChatHistoryEmptiesTheRowsAndTheKeptChat() async {
        final class Flag: @unchecked Sendable { private let lock = NSLock(); private var on = false
            var value: Bool { get { lock.withLock { on } } set { lock.withLock { on = newValue } } } }
        let cleared = Flag()
        let (app, _) = makeApp { _, path, _ in
            if path.hasPrefix("/api/messages") {
                return (200, cleared.value ? "[]" : #"[{"id": 1, "role": "user", "content": "a"}, {"id": 2, "role": "assistant", "content": "b"}]"#)
            }
            return (200, "{}")
        }
        let m = ChatModel(app: app, uploader: FakeUploader())
        m.start()
        await until { m.items.count == 2 }
        cleared.value = true          // the server cleared it (마이 → 대화 기록 삭제)
        app.historyWasCleared()
        m.forgetHistory()
        #expect(m.items.isEmpty && !m.hasOlder)
        #expect(app.historyCleared == 1)
        #expect(ChatModel(app: app, uploader: FakeUploader()).items.isEmpty)   // the next open shows nothing old
        await until { m.loaded }
        #expect(m.items.isEmpty)
    }
}
