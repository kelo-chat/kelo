import Foundation
import Testing
@testable import KeloCore

/// Forgotten topics (list, Allow again, the forget card's plan list) and the payments approved in advance (lists, switch, the
/// order_preapprove card, the payment_auto note). Mirrors Android ForgetTest / PaymentCoverTest.
@Suite struct ForgetCoverTests {
    private func api(_ t: FakeTransport) -> KeloApi { KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "" }) }
    private func event(_ json: String) throws -> TurnEvent { TurnEvent.parse(try JSON.parse(json)) }

    // ---- forgotten topics

    @Test func theTopicsComeNewestFirstWithTheirCounts() async throws {
        let t = FakeTransport(.ok(200, """
        {"items":[{"id":"a","label":"Old topic","created_at":100,"removed":{"notes":2,"memory":3,"weird":4,"skills":0}},
                  {"id":"b","label":"New topic","created_at":300,"removed":{}},{"label":"no id"}]}
        """))
        let list = try await api(t).forgottenTopics()
        #expect(list.map(\.id) == ["b", "a"])
        #expect(t.last.url?.path == "/api/forget")
        let parts = ForgottenRules.parts(list[1].removed)
        #expect(parts.map(\.category) == ["memory", "notes"])
        #expect(parts.map(\.count) == [3, 2])
        #expect(ForgottenRules.parts(list[0].removed).isEmpty)
        #expect(ForgottenRules.without(list, "b").map(\.id) == ["a"])
    }

    @Test func allowAgainSendsDeleteAndAGoneTopicCountsAsDone() async throws {
        let t = FakeTransport(.ok(200, #"{"ok":true}"#), .ok(404, #"{"detail":"no such forgotten topic"}"#), .ok(500, "{}"))
        try await api(t).allowForgottenAgain("topic one")
        #expect(t.last.httpMethod == "DELETE")
        #expect(t.last.url?.absoluteString.hasSuffix("/api/forget/topic%20one") == true)
        try await api(t).allowForgottenAgain("gone")   // 404: already gone
        await #expect(throws: ApiError.self) { try await api(t).allowForgottenAgain("x") }
    }

    @Test func thePlanListIsCutToSixtyCharactersOnOneLine() async throws {
        let long = String(repeating: "word ", count: 30)
        #expect(ForgottenRules.preview("  two\n lines\there ") == "two lines here")
        let cut = ForgottenRules.preview(long)
        #expect(cut.count <= ForgottenRules.previewChars)
        #expect(cut.hasSuffix("…"))
        let t = FakeTransport(.ok(200, #"{"id":"p","label":"x","items":[{"category":"notes","show":"Buy milk"},{"category":"memory","show":"  "}]}"#),
                              .ok(404, #"{"detail":"no such plan"}"#))
        let plan = try #require(await api(t).forgetPlan("p"))
        #expect(t.last.url?.path == "/api/forget/plans/p")
        #expect(ForgottenRules.lines(plan) { $0.capitalized } == ["Notes · Buy milk"])
        #expect(try await api(t).forgetPlan("p") == nil)
    }

    @Test func theForgetCardKnowsItsPlanFromTheArguments() throws {
        guard case .approvalRequest(let a) = try event(#"{"type":"approval_request","approval_id":"x","name":"forget_confirm","arguments":{"plan_id":" 7a "}}"#)
        else { throw TestError() }
        #expect(a.forgetPlan == "7a")
        guard case .approvalRequest(let other) = try event(#"{"type":"approval_request","approval_id":"y","name":"send_email","arguments":{"plan_id":"7"}}"#)
        else { throw TestError() }
        #expect(other.forgetPlan.isEmpty)
    }

    // ---- payments approved in advance

    private let orders = """
    {"enabled":true,"items":[
      {"id":"late","owner_kind":"task","owner_name":"","intent":"","items":[{"description":"running shoes, size 42","qty":2},{"description":""}],
       "max_total":120.5,"currency":"usd","expires_at":2000},
      {"id":"soon","owner_kind":"schedule","owner_name":"Weekly shop","intent":"Groceries","items":[],"max_total":80,"currency":"EUR","expires_at":1000},
      {"id":"never","owner_kind":"task","intent":"Gift","max_total":10,"currency":"KRW","expires_at":0},{"intent":"no id"}]}
    """

    @Test func theCoverListsLoadOrderedAndTheSwitchRidesAlong() async throws {
        let t = FakeTransport(.ok(200, orders),
                              .ok(200, #"{"enabled":false,"items":[{"id":"al1","schedule_id":3,"schedule_name":"Weekly shop","merchant_origin":"https://shop.example/","payment_label":"Visa 4242","currency":"EUR"}]}"#),
                              .ok(200, #"{"enabled":false}"#))
        let cover = try #require(await api(t).paymentCover())
        #expect(t.requests.value.map { $0.url?.path ?? "" } == ["/api/permissions/preapprovals", "/api/permissions/pay-allows", "/api/permissions/payment-cover"])
        #expect(cover.enabled == false)
        #expect(cover.orders.map(\.id) == ["soon", "late", "never"])   // the one that ends first on top, no end last
        #expect(cover.orders[0].forSchedule && cover.orders[0].ownerName == "Weekly shop")
        #expect(cover.orders[1].currency == "USD" && cover.orders[1].maxTotal == 120.5 && cover.orders[1].expiresAt == 2000)
        #expect(PaymentCoverRules.what(intent: cover.orders[1].intent, items: cover.orders[1].items) == "2 × running shoes, size 42")
        #expect(PaymentCoverRules.what(intent: " Groceries ", items: []) == "Groceries")
        #expect(cover.allows.count == 1 && cover.allows[0].shop == "shop.example" && cover.allows[0].paymentLabel == "Visa 4242")
        #expect(!cover.isEmpty)
        #expect(PaymentCoverRules.withoutOrder(cover, "late").orders.map(\.id) == ["soon", "never"])
        #expect(PaymentCoverRules.withoutAllow(cover, "al1").allows.isEmpty)
        #expect(PaymentCover(enabled: true).isEmpty)
    }

    @Test func aServerWithoutTheRoutesHidesTheSection() async throws {
        let t = FakeTransport(.ok(404, #"{"detail":"Not Found"}"#))
        #expect(try await api(t).paymentCover() == nil)
        let down = FakeTransport(.ok(500, "{}"))
        await #expect(throws: ApiError.self) { _ = try await api(down).paymentCover() }
    }

    @Test func removingAndTheSwitchUseTheRightCallsAndAGoneRowIsDone() async throws {
        let t = FakeTransport(.ok(200, #"{"ok":true}"#), .ok(404, #"{"detail":"gone"}"#), .ok(200, #"{"ok":true}"#), .ok(200, #"{"enabled":true}"#),
                              .ok(422, #"{"detail":"enabled must be true or false"}"#))
        try await api(t).removePreapproval("p1")
        #expect(t.last.httpMethod == "DELETE" && t.last.url?.path == "/api/permissions/preapprovals/p1")
        try await api(t).removePreapproval("p1")   // 404: already gone
        try await api(t).removePayAllow("a 1")
        #expect(t.last.httpMethod == "DELETE" && t.last.url?.absoluteString.hasSuffix("/api/permissions/pay-allows/a%201") == true)
        #expect(try await api(t).setPaymentCover(true) == true)
        #expect(t.last.httpMethod == "PUT" && t.last.url?.path == "/api/permissions/payment-cover")
        #expect(t.lastBody.contains("\"enabled\":true"))
        await #expect(throws: ApiError.self) { _ = try await api(t).setPaymentCover(false) }
    }

    // ---- the cards

    @Test func theOrderCardCarriesWhatTheCapAndUntilWhen() throws {
        guard case .approvalRequest(let a) = try event("""
        {"type":"approval_request","approval_id":"o1","name":"order_preapprove","arguments":{},"preapproval":{
          "intent":"Two pairs of running shoes","items":[{"description":"Shoes, size 42","qty":2},{"description":" "}],
          "requirements":["waterproof"," "],"shipping":"Home","merchant_policy":"any big shop","delegated_choices":["colour"],
          "max_total":150.0,"currency":"usd","max_total_text":"$150.00","expires_at":1893456000,"owner_kind":"schedule"}}
        """) else { throw TestError() }
        let p = try #require(a.preapproval)
        #expect(p.intent == "Two pairs of running shoes" && p.currency == "USD" && p.maxTotalText == "$150.00")
        #expect(p.expiresAt == 1893456000 && p.forSchedule)
        #expect(p.requirements == ["waterproof"] && p.delegatedChoices == ["colour"] && p.shipping == "Home" && p.merchantPolicy == "any big shop")
        #expect(PaymentCoverRules.whatLines(p) == ["Two pairs of running shoes", "2 × Shoes, size 42"])
    }

    @Test func aCardWithNothingReadableIsAnOrdinaryCard() throws {
        for extra in [#"{"max_total":5}"#, #"{"intent":"x","max_total":0}"#, #"{"intent":"x"}"#, "[]", "null"] {
            guard case .approvalRequest(let a) = try event(#"{"type":"approval_request","approval_id":"o","name":"order_preapprove","preapproval":"#
                                                           + extra + "}") else { throw TestError() }
            #expect(a.preapproval == nil)
        }
        guard case .approvalRequest(let plain) = try event(#"{"type":"approval_request","approval_id":"o","name":"send"}"#) else { throw TestError() }
        #expect(plain.preapproval == nil)
    }

    @Test func thePaymentNoticeSaysItWasCoveredInAdvance() throws {
        guard case .paymentAuto(let p) = try event(#"{"type":"payment_auto","id":"1","payment":{"site":"shop.example","amount_text":"$9.99"},"preapproval":{"kind":"preapproval","via":"checkout_review"}}"#)
        else { throw TestError() }
        #expect(p.cover == PreapprovalCover(kind: "preapproval", via: "checkout_review") && p.cover?.bySchedule == false)
        guard case .paymentAuto(let s) = try event(#"{"type":"payment_auto","id":"2","payment":{"site":"shop.example"},"preapproval":{"kind":"schedule_allow","via":"x"}}"#)
        else { throw TestError() }
        #expect(s.cover?.bySchedule == true)
        guard case .paymentAuto(let old) = try event(#"{"type":"payment_auto","id":"3","payment":{"site":"shop.example"}}"#) else { throw TestError() }
        #expect(old.cover == nil)
        #expect(PreapprovalCover.parse(try JSON.parse(#"{"kind":" "}"#)) == nil)
    }

    @Test func theCapReadsAsTheServersTextElseNumberAndCurrency() {
        #expect(PaymentCoverRules.capText(PreapprovalAsk(maxTotal: 150, currency: "USD", maxTotalText: "$150.00")) == "$150.00")
        #expect(PaymentCoverRules.capText(PreapprovalAsk(maxTotal: 150, currency: "USD")) == "150 USD")
        #expect(PaymentCoverRules.capText(PreapprovalAsk(maxTotal: 12.5)) == "12.50")
    }

    private struct TestError: Error {}
}
