import Foundation
import Testing
@testable import KeloCore

/// The message an approval card sends can be fixed before Allow (Android ApprovalEditTest / PastApprovalsTest). The rules are the
/// server's: same limits, same "nothing changed = no edit", a deny never carries the text, a payment is never editable.
@MainActor @Suite struct ApprovalEditTests {
    private func api(_ t: FakeTransport) -> KeloApi { KeloApi(baseUrl: "https://k.example", token: "tok", transport: t, acceptLanguage: { "" }) }
    private func event(_ json: String) throws -> TurnEvent.ApprovalRequest {
        guard case .approvalRequest(let ev) = TurnEvent.parse(try JSON.parse(json)) else { throw TestError.notApproval }
        return ev
    }
    enum TestError: Error { case notApproval }

    private let mail = EditableMessage(field: "body", text: "Hello team", max: nil)
    private let text = EditableMessage(field: "text", text: "On my way", max: 10)

    // ---- the card's object

    @Test func theEditableObjectIsReadWithItsLimit() throws {
        let e = try #require(EditableMessage.parse(try JSON.parse(#"{"field":"text","text":"On my way","max":1600}"#)))
        #expect(e == EditableMessage(field: "text", text: "On my way", max: 1600))
        #expect(EditableMessage.parse(try JSON.parse(#"{"field":"body","text":"x","max":null}"#))?.max == nil)
    }

    @Test func aMissingFieldOrTextMeansNotEditable() throws {
        #expect(EditableMessage.parse(nil) == nil)
        #expect(EditableMessage.parse(try JSON.parse(#"{"text":"x"}"#)) == nil)
        #expect(EditableMessage.parse(try JSON.parse(#"{"field":" ","text":"x"}"#)) == nil)
        #expect(EditableMessage.parse(try JSON.parse(#"{"field":"body"}"#)) == nil)
    }

    @Test func theApprovalRequestCarriesEditableButNeverForAPayment() throws {
        let mailCard = try event(#"{"type":"approval_request","approval_id":"a1","name":"email_send","label":"Send an email","asked_at":1,"editable":{"field":"body","text":"Hi","max":null}}"#)
        #expect(mailCard.editable == EditableMessage(field: "body", text: "Hi", max: nil))
        let paid = try event(#"{"type":"approval_request","approval_id":"a2","name":"order_pay","payment":{},"editable":{"field":"body","text":"Hi"}}"#)
        #expect(paid.editable == nil)
        let plain = try event(#"{"type":"approval_request","approval_id":"a3","name":"browser_click"}"#)
        #expect(plain.editable == nil)
    }

    // ---- the sheet's rules

    @Test func aBlankMessageIsRefused() {
        #expect(ApprovalEditRules.problem("", max: nil) == .empty)
        #expect(ApprovalEditRules.problem("  \n ", max: nil) == .empty)
        #expect(ApprovalEditRules.problem("ok", max: nil) == nil)
    }

    @Test func aMessageOverTheLimitIsRefusedAndCountsCodePoints() {
        #expect(ApprovalEditRules.problem("0123456789", max: 10) == nil)
        #expect(ApprovalEditRules.problem("01234567890", max: 10) == .tooLong)
        // the limit is in characters as the server counts them (code points): a flag is 2, a Hangul syllable is 1
        #expect(ApprovalEditRules.problem("🇰🇷🇰🇷🇰🇷🇰🇷🇰🇷", max: 10) == nil)
        #expect(ApprovalEditRules.problem("   0123456789   ", max: 10) == nil, "surrounding spaces are not counted")
        #expect(ApprovalEditRules.problem("anything at all", max: nil) == nil, "no limit: only blank is refused")
    }

    @Test func theCounterShowsOnlyWhenThereIsALimit() {
        #expect(ApprovalEditRules.counter("abc", max: 10)?.count == 3)
        #expect(ApprovalEditRules.counter("abc", max: 10)?.max == 10)
        #expect(ApprovalEditRules.counter("abc", max: nil) == nil)
    }

    // ---- what the edit sends

    @Test func anUnchangedOrMissingTextIsNoEdit() {
        #expect(ApprovalEditRules.toSend(mail, draft: nil) == nil)
        #expect(ApprovalEditRules.toSend(mail, draft: "Hello team") == nil)
        #expect(ApprovalEditRules.toSend(mail, draft: "  Hello team \n") == nil, "the same words with spaces around are no change")
        #expect(ApprovalEditRules.toSend(nil, draft: "x") == nil)
    }

    @Test func aChangedTextIsSentTrimmed() {
        #expect(ApprovalEditRules.toSend(mail, draft: "  Hello all  ") == "Hello all")
        #expect(ApprovalEditRules.shown(mail, draft: nil) == "Hello team")
        #expect(ApprovalEditRules.shown(mail, draft: "Hello all") == "Hello all")
    }

    @Test func theMessageIsNotRepeatedInTheFactLines() {
        let lines = [ApprovalLine(label: "To", value: "team@example.com"), ApprovalLine(label: "Body", value: "Hello team", full: true)]
        #expect(ApprovalEditRules.withoutMessage(lines, mail).map(\.label) == ["To"])
    }

    @Test func theDraftsGoOnlyWithAnAllowAndAreUsedUp() {
        let id = "draft-\(UUID().uuidString)"
        #expect(ApprovalDrafts.edited(id, mail) == nil)
        ApprovalDrafts.set(id, "Hello all")
        #expect(ApprovalDrafts.get(id) == "Hello all")
        #expect(ApprovalDrafts.edited(id, mail) == ["body": .string("Hello all")])
        ApprovalDrafts.clear(id)
        #expect(ApprovalDrafts.get(id) == nil)
        #expect(ApprovalDrafts.edited(id, mail) == nil)
    }

    @Test func anAllowSendsTheFixedTextAndADenyDoesNot() async throws {
        let t = FakeTransport(.ok(200, #"{"ok":true}"#), .ok(200, #"{"ok":true}"#))
        let client = api(t)
        _ = try await client.answerApproval("a1", approve: true, edited: ["text": .string("On the way")])
        let allow = try JSON.parse(t.lastBody)
        #expect(allow["approve"]?.boolValue == true)
        #expect(allow["edited"]?.objectValue?["text"]?.stringValue == "On the way")
        #expect(allow["via"] == nil, "the app's own answer says nothing about its channel (the server keeps app)")

        _ = try await client.answerApproval("a1", approve: false, edited: ["text": .string("On the way")], via: "notification")
        let deny = try JSON.parse(t.lastBody)
        #expect(deny["edited"] == nil, "a deny drops the edit")
        #expect(deny["via"]?.stringValue == "notification")
    }

    // ---- the past approvals record

    @Test func aHistoryRecordKeepsWhatWhereHowAndEdited() throws {
        let json = #"{"id":"h3","tool":"email_send","label":"Sending an email","site":"","detail":"team@example.com","decision":"denied","asked_at":1,"unattended":false,"can_remember":false,"kind":"send","where":"team@example.com","via":"notification","edited":true,"result":"deny"}"#
        let r = try JSONDecoder().decode(ApprovalRecord.self, from: Data(json.utf8))
        #expect(r.kind == "send")
        #expect(r.place == "team@example.com")
        #expect(r.via == "notification")
        #expect(r.edited)
        #expect(r.result == "deny")
    }

    @Test func anOlderHistoryRecordHasTheNewFieldsEmpty() throws {
        let json = #"{"id":"h1","tool":"browser_click","label":"x","site":"shop.example","detail":"","decision":"allowed_once","asked_at":1,"unattended":false,"can_remember":true}"#
        let r = try JSONDecoder().decode(ApprovalRecord.self, from: Data(json.utf8))
        #expect(r.kind == "" && r.place == "" && r.via == "" && r.result == "")
        #expect(r.edited == false)
    }
}
