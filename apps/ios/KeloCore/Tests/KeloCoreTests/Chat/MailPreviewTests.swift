import Foundation
import Testing
@testable import KeloCore

/// Port of the Android MailPreviewTest (desktopTest): a designed mail's card part, the link rules, the preview the server sends, and
/// the store that fetches it once per card.
@Suite struct MailPreviewTests {
    /// A real preview() answer from the server, shortened: the document and the numbers (blocks are drawn by the desktop app only).
    private let sample = #"""
        {"html":"<!doctype html><html><head><meta charset=\"utf-8\"></head><body><h1 style=\"color: #123456\">Sale</h1><p>Bye</p></body></html>","blocks":[{"kind":"heading","level":1,"ordinal":0,"depth":0,"align":"start","bg":"","spans":[{"text":"Sale","b":true,"i":false,"u":false,"s":false,"color":"#123456","href":""}]}],"cut":false,"pictures":0,"links":1,"bytes":200}
        """#

    @Test func theMailPartOfAnApprovalRequestIsParsed() throws {
        let e = try JSON.parse(#"{"type":"approval_request","approval_id":"ap1","name":"email_send","arguments":{"html":"<p>raw</p>"},"mail":{"designed":true,"preview":"/api/approvals/ap1/mail-preview","pictures":1,"links":2}}"#)
        guard case .approvalRequest(let a) = TurnEvent.parse(e) else { Issue.record("not an approval request"); return }
        #expect(a.mail == MailDesign(previewPath: "/api/approvals/ap1/mail-preview", pictures: 1, links: 2))
    }

    @Test func anApprovalWithHtmlArgumentsButNoMailPartHasNoPreview() throws {
        let e = try JSON.parse(#"{"type":"approval_request","approval_id":"ap2","name":"email_send","arguments":{"html":"<p>raw</p>"}}"#)
        guard case .approvalRequest(let a) = TurnEvent.parse(e) else { Issue.record("not an approval request"); return }
        #expect(a.mail == nil)   // the apps never render arguments.html
    }

    @Test func mailDesignRejectsAnythingThatIsNotAServerPreviewPath() throws {
        #expect(MailDesign.parse(try JSON.parse(#"{"designed":false,"preview":"/api/approvals/a/mail-preview"}"#)) == nil)
        #expect(MailDesign.parse(try JSON.parse(#"{"designed":true}"#)) == nil)
        #expect(MailDesign.parse(try JSON.parse(#"{"designed":true,"preview":"https://x/"}"#)) == nil)
        #expect(MailDesign.parse(nil) == nil)
        #expect(MailDesign.parse(try JSON.parse(#"{"designed":true,"preview":"/api/approvals/a/mail-preview"}"#))
            == MailDesign(previewPath: "/api/approvals/a/mail-preview", pictures: 0, links: 0))
    }

    @Test func linkToOpenKeepsOnlyWebAndMailAddresses() {
        for ok in ["https://a.example/x", "http://a", "mailto:kim@example.com"] {
            #expect(MailPreviewRules.linkToOpen(ok) == ok)
        }
        let bad = ["javascript:alert(1)", "JAVASCRIPT:x", " javascript:x", "data:text/html,x", "file:///x", "intent://x#Intent;end",
                   "about:blank", "content://x", "kelo://x", "", "https:", "jav\tascript:x"]
        for url in bad {
            #expect(MailPreviewRules.linkToOpen(url) == nil, "\(url) must not open")
        }
        #expect(MailPreviewRules.linkToOpen(nil) == nil)
    }

    @Test func theConfirmShowsTheHostOrTheAddress() {
        #expect(MailPreviewRules.linkLabel("https://a.example/x?y=1") == "a.example")
        #expect(MailPreviewRules.linkLabel("mailto:kim@example.com") == "kim@example.com")
    }

    @Test func theServersPreviewParses() throws {
        let p = try #require(MailPreviewRules.parse(try JSON.parse(sample)))
        #expect(p.html.hasPrefix("<!doctype html>"))
        #expect(p.pictures == 0 && p.links == 1 && !p.cut)
        #expect(MailPreviewRules.parse(try JSON.parse(#"{"blocks":[]}"#)) == nil)   // no html: not a preview
    }

    @Test func theStoreFetchesOnceKeepsTheAnswerAndRetriesAfterAFailure() async throws {
        let t = FakeTransport(.ok(200, sample), .ok(500, "{}"), .ok(200, sample))
        let api = KeloApi(baseUrl: "https://k.example", token: "t", transport: t, acceptLanguage: { "" })
        let store = MailPreviewStore()
        let first = await store.load("ap1", path: "/api/approvals/ap1/mail-preview", api: api)
        guard case .ready = first else { Issue.record("expected ready, got \(first)"); return }
        _ = await store.load("ap1", path: "/api/approvals/ap1/mail-preview", api: api)   // already ready: no second call
        #expect(t.requests.value.count == 1)
        let failed = await store.load("ap2", path: "/api/approvals/ap2/mail-preview", api: api)
        #expect(failed == .failed)
        _ = await store.load("ap2", path: "/api/approvals/ap2/mail-preview", api: api)   // a failure may be retried
        #expect(t.requests.value.count == 3)
        #expect(await store.cached("ap2") != .failed)
    }

    @Test func aGoneOrTooBigPreviewIsSaidSo() async {
        let t = FakeTransport(.ok(404, #"{"detail":"no such pending mail"}"#), .ok(413, #"{"detail":{"code":"too_big","message":"big"}}"#))
        let api = KeloApi(baseUrl: "https://k.example", token: "t", transport: t, acceptLanguage: { "" })
        let store = MailPreviewStore()
        #expect(await store.load("gone", path: "/api/approvals/gone/mail-preview", api: api) == .gone)
        #expect(await store.load("big", path: "/api/approvals/big/mail-preview", api: api) == .tooBig)
    }

    @Test func theStoreKeepsEightPreviews() async {
        let t = FakeTransport()
        t.answers.value = Array(repeating: .ok(200, sample), count: 10)   // 9 loads, then the evicted one again
        let api = KeloApi(baseUrl: "https://k.example", token: "t", transport: t, acceptLanguage: { "" })
        let store = MailPreviewStore()
        for i in 0..<9 { _ = await store.load("ap\(i)", path: "/api/approvals/ap\(i)/mail-preview", api: api) }
        #expect(t.requests.value.count == 9)
        _ = await store.load("ap8", path: "/api/approvals/ap8/mail-preview", api: api)   // the newest is still kept
        #expect(t.requests.value.count == 9)
        _ = await store.load("ap0", path: "/api/approvals/ap0/mail-preview", api: api)   // the oldest was dropped: fetched again
        #expect(t.requests.value.count == 10)
    }
}
