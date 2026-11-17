import Foundation
import Testing
@testable import KeloCore

/// The data-trail card (which kind of data goes to which recipient; Allow once / Always / Deny) and the recipients always allowed.
@Suite struct RecipientAllowsTests {
    private let card = """
    {"type":"approval_request","approval_id":"a1","name":"send_email","arguments":{},
     "trail":[{"recipient":"lee@else.example","kind":"mail","titles":["Invoice May","Invoice May","Trip plan"]},
              {"recipient":"link","kind":"file","titles":[]},{"recipient":"","kind":"mail"}],
     "can_remember_recipient":true}
    """

    private func event(_ json: String) throws -> TurnEvent.ApprovalRequest {
        guard case .approvalRequest(let a) = TurnEvent.parse(try JSON.parse(json)) else { throw TestFailure() }
        return a
    }
    private struct TestFailure: Error {}

    @Test func theCardReadsKindRecipientAndTitlesAndDropsIncompleteOffers() throws {
        let offers = try event(card).offers
        #expect(offers.count == 2)
        #expect(offers[0] == TrailOffer(recipient: "lee@else.example", kind: "mail", titles: ["Invoice May", "Trip plan"]))
        #expect(offers[1].kind == "file")
        #expect(RecipientRules.canAlways(offers))
    }

    @Test func anOlderServerOrAnotherCardHasNoOffers() throws {
        let plain = try event(#"{"type":"approval_request","approval_id":"a2","name":"send"}"#)
        #expect(plain.offers.isEmpty)
        #expect(!RecipientRules.canAlways(plain.offers))
    }

    @Test func recipientsAreShownWithoutInternalPrefixesAndTheLinkHasItsOwnWords() {
        #expect(RecipientRules.shown(" lee@else.example ") == "lee@else.example")
        #expect(RecipientRules.shown("connector:slack") == "slack")
        #expect(RecipientRules.shown("site:shop.example") == "shop.example")
        #expect(RecipientRules.shown("link") == nil)
        #expect(RecipientRules.shown("") == nil)
        #expect(RecipientRules.knownKind("something-new") == "other")
        #expect(RecipientRules.knownKind("mail") == "mail")
    }

    @Test func theThreeAnswersSendWhatTheServerExpects() {
        #expect(RecipientRules.Answer.once.approve && RecipientRules.Answer.once.remember == nil)
        #expect(RecipientRules.Answer.always.approve && RecipientRules.Answer.always.remember == "recipient")
        #expect(!RecipientRules.Answer.deny.approve && RecipientRules.Answer.deny.remember == nil)
    }

    @Test func theListParsesNewestFirstAndRemovesOne() throws {
        let items = RecipientAllow.parseList(try JSON.parse("""
        {"items":[{"id":1,"recipient":"a@x.example","kind":"mail","created_at":10.5,"approval_id":"p1"},
                  {"id":3,"recipient":"link","kind":"file","created_at":30},{"id":2,"recipient":"slack","kind":"notes","created_at":20},{"recipient":"x"}]}
        """))
        #expect(items.count == 3)
        let ordered = RecipientRules.ordered(items)
        #expect(ordered.map(\.id) == [3, 2, 1])
        #expect(RecipientRules.without(ordered, 2).map(\.id) == [3, 1])
        #expect(items[0].createdAt == 10.5)
    }
}
