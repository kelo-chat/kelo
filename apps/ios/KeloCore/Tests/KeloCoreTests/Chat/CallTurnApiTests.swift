import Foundation
import Testing
@testable import KeloCore

@Suite struct CallTurnApiTests {
    @Test func callOfferEventParses() throws {
        let json = try JSON.parse("""
            {"type":"call_offer","call_id":"c_12345678","topic":"flight booking"}
        """)
        let ev = TurnEvent.parse(json)
        guard case .callOffer(let callId, let topic) = ev else {
            Issue.record("Expected .callOffer")
            return
        }
        #expect(callId == "c_12345678")
        #expect(topic == "flight booking")
    }

    @Test func storedMessageViaRoundTrips() throws {
        let msg = StoredMessage(id: 42, role: "assistant", content: "hello", createdAt: 100.0, images: [], via: "call")
        let data = try JSONEncoder().encode(msg)
        let decoded = try JSONDecoder().decode(StoredMessage.self, from: data)
        #expect(decoded.via == "call")
        #expect(decoded.id == 42)

        let msgNoVia = StoredMessage(id: 43, role: "user", content: "hi", createdAt: 101.0, images: [])
        let dataNoVia = try JSONEncoder().encode(msgNoVia)
        let decodedNoVia = try JSONDecoder().decode(StoredMessage.self, from: dataNoVia)
        #expect(decodedNoVia.via == "")
    }

    @Test func keloTurnApiPutsCallAndCanCallOnEveryStart() async throws {
        let t = FakeTransport(.ok(200, #"{"id":"turn_1"}"#))
        let api = KeloApi(baseUrl: "https://k.example", token: "tok", transport: t)
        let turnApi = KeloTurnApi(api: api, canCall: { true })
        let id = try await turnApi.start(
            text: "hello",
            timezone: "Asia/Seoul",
            answers: [],
            secrets: nil,
            attachments: [],
            autoText: false,
            call: "call_abc999"
        )
        #expect(id == "turn_1")

        let body = try? JSON.parse(t.lastBody)
        #expect(body?["call"]?.stringValue == "call_abc999")
        #expect(body?["can_call"]?.boolValue == true)

        // When call is nil, "call" is omitted, and can_call is still sent
        let t2 = FakeTransport(.ok(200, #"{"id":"turn_2"}"#))
        let turnApi2 = KeloTurnApi(api: KeloApi(baseUrl: "https://k.example", token: "tok", transport: t2), canCall: { false })
        let id2 = try await turnApi2.start(text: "hi", timezone: "Asia/Seoul")
        #expect(id2 == "turn_2")

        let body2 = try? JSON.parse(t2.lastBody)
        #expect(body2?["call"] == nil)
        #expect(body2?["can_call"]?.boolValue == false)
    }

    @Test func fakeTurnApiWithout7ArgOverrideStillWorksViaDefaultImplementation() async throws {
        final class FakeTurnApi: TurnApi, @unchecked Sendable {
            var called6Arg = false
            func start(text: String, timezone: String) async throws -> String { "t" }
            func start(text: String, timezone: String, answers: [ReplyAnswer]) async throws -> String { "t" }
            func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?) async throws -> String { "t" }
            func start(text: String, timezone: String, answers: [ReplyAnswer], secrets: String?, attachments: [String], autoText: Bool) async throws -> String {
                called6Arg = true
                return "from_6_arg"
            }
            func poll(id: String, after: Int) async throws -> TurnPoll { throw ApiError(status: 500, detail: "") }
            func stop(id: String) async throws {}
        }

        let fake = FakeTurnApi()
        let res = try await fake.start(
            text: "test",
            timezone: "UTC",
            answers: [],
            secrets: nil,
            attachments: [],
            autoText: false,
            call: "call_123"
        )
        #expect(fake.called6Arg == true)
        #expect(res == "from_6_arg")
    }
}
