import Foundation
import Testing
@testable import KeloCore

/// 마이 → 에이전트 → 말투: the page's chosen tone follows the server, whether the agent changed it in the chat or the page did
/// (Android ToneRulesTest.kt).
struct ToneRulesTests {
    @Test func anOlderServerOrAnUnknownValueReadsAsPolite() throws {
        let older = try JSON.decode(Profile.self, from: try JSON.parse(#"{"agent_name":"Kelo"}"#))
        #expect(older.tone == "polite")
        #expect(ToneRules.selected(older) == ToneRules.polite)
        #expect(ToneRules.selected(Profile(tone: "")) == ToneRules.polite)
        #expect(ToneRules.selected(Profile(tone: "loud")) == ToneRules.polite)
        #expect(ToneRules.selected(Profile(tone: "friendly")) == ToneRules.friendly)
        // a null or mistyped tone also reads as polite
        #expect(try JSON.decode(Profile.self, from: try JSON.parse(#"{"tone":null}"#)).tone == "polite")
        #expect(try JSON.decode(AgentStatus.self, from: try JSON.parse(#"{"busy":false}"#)).tone == nil)
    }

    @Test func theStatusPollOnlyChangesTheProfileWhenTheToneDiffers() {
        let polite = Profile(agentName: "Kelo", tone: "polite")
        #expect(ToneRules.synced(polite, polled: nil) == nil)   // an older server says nothing
        #expect(ToneRules.synced(polite, polled: "polite") == nil)
        #expect(ToneRules.synced(polite, polled: "shouty") == nil)
        var friendly = polite
        friendly.tone = "friendly"
        #expect(ToneRules.synced(polite, polled: "friendly") == friendly)
        // an empty tone (older server) reads as polite, so "polite" from the poll changes nothing either
        #expect(ToneRules.synced(Profile(tone: ""), polled: "polite") == nil)
    }

    @Test func theChatChangesItThePageShowsItAndTappingPoliteSwitchesBack() async throws {
        var shown = Profile(agentName: "Kelo", tone: "polite")
        // "반말로 해 줘" in the chat: set_tone saved friendly; the next status poll carries it
        let status = try JSON.decode(AgentStatus.self, from: try JSON.parse(#"{"busy":false,"agent_name":"Kelo","tone":"friendly"}"#))
        if let next = ToneRules.synced(shown, polled: status.tone) { shown = next }
        #expect(ToneRules.selected(shown) == ToneRules.friendly)
        // the page opens: GET /api/profile says the same
        let t = FakeTransport(.ok(200, #"{"agent_name":"Kelo","tone":"friendly","avatar_version":0}"#),
                              .ok(200, #"{"agent_name":"Kelo","tone":"polite","avatar_version":0}"#))
        let api = KeloApi(baseUrl: "https://k.example", token: "tok", transport: t)
        shown = try await ToneRules.reload(api)
        #expect(t.last.httpMethod == "GET")
        #expect(t.last.url?.path == "/api/profile")
        #expect(ToneRules.selected(shown) == ToneRules.friendly)
        // 공손 tapped: PUT {"tone":"polite"}, the saved profile shows
        shown = try await ToneRules.save(api, tone: ToneRules.polite)
        #expect(t.last.httpMethod == "PUT")
        #expect(t.last.url?.path == "/api/profile")
        #expect(t.lastBody == #"{"tone":"polite"}"#)
        #expect(ToneRules.selected(shown) == ToneRules.polite)
        #expect(ToneRules.synced(shown, polled: "polite") == nil)
    }
}
