import Testing
import Foundation
import KeloCore
@testable import KeloConnectors

@Suite struct BetaRulesTests {
    @Test func onlyGmailAndGoogleCalendarAsk() {
        #expect(BetaRules.product("gmail") == "gmail")
        #expect(BetaRules.product("google_calendar") == "calendar")
        #expect(BetaRules.product("imap_email") == nil)
    }

    @Test func whatTheScreenShows() {
        #expect(BetaRules.shown(nil) == .nothing)
        #expect(BetaRules.shown(GoogleBetaState(canRequest: true)) == .request)
        #expect(BetaRules.shown(GoogleBetaState(canRequest: false)) == .nothing)
        #expect(BetaRules.shown(GoogleBetaState(status: "requested", email: "a@b.com", canRequest: true)) == .requested)
        #expect(BetaRules.shown(GoogleBetaState(status: "declined", canRequest: true)) == .declined)
        #expect(BetaRules.shown(GoogleBetaState(status: "approved", open: true)) == .opened)
    }

    @Test func theEmailIsCheckedLikeTheServer() {
        #expect(BetaRules.email("  a.b+c@gmail.com ") == "a.b+c@gmail.com")
        for bad in ["", "a@b", "a b@c.com", "@c.com", "a@-c.com", String(repeating: "x", count: 65) + "@c.com"] {
            #expect(BetaRules.email(bad) == nil)
        }
    }

    @Test func readsTheServerAnswer() throws {
        let s = try JSONDecoder().decode(GoogleBetaState.self, from: Data(
            #"{"status":"requested","email":"a@b.com","products":["gmail"],"open":false,"can_request":true}"#.utf8))
        #expect(s.status == "requested" && s.email == "a@b.com" && s.products == ["gmail"] && s.canRequest)
    }
}
