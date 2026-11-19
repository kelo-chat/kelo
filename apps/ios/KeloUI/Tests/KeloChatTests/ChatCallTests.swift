import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

@MainActor
@Suite struct ChatCallTests {
    @Test func chatMessageViaParsesAndEncodes() throws {
        let json = """
        {"id":100,"role":"assistant","content":"hello from call","created_at":123.45,"via":"call"}
        """
        let msg = try JSONDecoder().decode(ChatMessage.self, from: Data(json.utf8))
        #expect(msg.via == "call")

        let encoded = try JSONEncoder().encode(msg)
        let redecoded = try JSONDecoder().decode(ChatMessage.self, from: encoded)
        #expect(redecoded.via == "call")

        let jsonNoVia = """
        {"id":101,"role":"user","content":"normal message","created_at":124.0}
        """
        let msgNoVia = try JSONDecoder().decode(ChatMessage.self, from: Data(jsonNoVia.utf8))
        #expect(msgNoVia.via == "")
    }

    @Test func chatReducerItemSetsFromCall() {
        let callMsg = ChatMessage(id: 1, role: "user", content: "spoken words", via: "call")
        let itemCall = ChatReducer.item(callMsg)
        guard case .msg(let mCall) = itemCall else { Issue.record("expected .msg"); return }
        #expect(mCall.fromCall == true)

        let normalMsg = ChatMessage(id: 2, role: "user", content: "typed words", via: "")
        let itemNormal = ChatReducer.item(normalMsg)
        guard case .msg(let mNormal) = itemNormal else { Issue.record("expected .msg"); return }
        #expect(mNormal.fromCall == false)
    }

    @Test func sendFromCallAddsBubbleWithFromCallAndStartsTurn() async throws {
        var recordedBody = ""
        let t = RouteTransport { method, path, body in
            if path == "/api/turns" {
                recordedBody = body.map { String(decoding: $0, as: UTF8.self) } ?? ""
                return (200, #"{"id":"turn_call_1"}"#)
            }
            if path.starts(with: "/api/turns/turn_call_1") {
                return (200, #"{"events":[{"type":"done"}],"next":1,"done":true}"#)
            }
            return (200, "{}")
        }
        let defaults = UserDefaults(suiteName: "kelo-call-test-\(UUID().uuidString)")!
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
                           prefs: Prefs(defaults: defaults), transport: t, cache: ScreenCache(directory: nil, key: { nil }))
        let model = ChatModel(app: app)

        var receivedEvent: TurnEvent? = nil
        model.onTurnEvent = { ev in
            receivedEvent = ev
        }

        let sent = model.sendFromCall(text: "Where is my order?", callId: "c_11223344")
        #expect(sent == true)

        // The user's bubble in items should have fromCall == true
        let bubble = model.items.first?.asMsg
        #expect(bubble != nil)
        #expect(bubble?.fromCall == true)
        #expect(bubble?.text == "Where is my order?")

        await model.waitForTurn()
        #expect(recordedBody.contains(#""call":"c_11223344""#))
        #expect(receivedEvent != nil)
    }
}
