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

        let sent = model.sendFromCall(text: "Where is my order?", callId: "c_11223344", amend: false)
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

    @Test func callTurnEventsCarryTheCallId() async {
        let t = RouteTransport { _, path, _ in
            if path == "/api/turns" { return (200, #"{"id":"turn_call_2"}"#) }
            if path.starts(with: "/api/turns/turn_call_2") {
                return (200, #"{"events":[{"type":"done"}],"next":1,"done":true}"#)
            }
            return (200, "{}")
        }
        let defaults = UserDefaults(suiteName: "kelo-call-ids-\(UUID().uuidString)")!
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
                           prefs: Prefs(defaults: defaults), transport: t, cache: ScreenCache(directory: nil, key: { nil }))
        let model = ChatModel(app: app)
        var ids: [String?] = []
        model.onCallTurnEvent = { id, _ in ids.append(id) }

        #expect(model.sendFromCall(text: "call words", callId: "c_1", amend: false) == true)
        await model.waitForTurn()
        #expect(!ids.isEmpty)
        #expect(ids.allSatisfy { $0 == "c_1" })

        let before = ids.count
        model.send("typed")
        await model.waitForTurn()
        #expect(ids.count > before)
        #expect(ids[before...].allSatisfy { $0 == nil })
    }

    @Test func aCallTurnNothingAnsweredCanBeTakenBack() async throws {   // owner 2026-11-20
        let t = RouteTransport { _, path, _ in
            if path == "/api/turns" { return (200, #"{"id":"turn_quiet"}"#) }
            if path.starts(with: "/api/turns/turn_quiet") { return (200, #"{"events":[],"next":0,"done":false}"#) }
            return (200, "{}")
        }
        let defaults = UserDefaults(suiteName: "kelo-call-takeback-\(UUID().uuidString)")!
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
                           prefs: Prefs(defaults: defaults), transport: t, cache: ScreenCache(directory: nil, key: { nil }))
        let model = ChatModel(app: app)
        #expect(model.sendFromCall(text: "나 목소리", callId: "c_7", amend: false) == true)
        try await Task.sleep(nanoseconds: 100_000_000)
        #expect(model.takeBackFromCall(callId: "c_7") == true)
        #expect(model.items.allSatisfy { $0.asMsg?.text != "나 목소리" })
        await model.waitForTurn()
        #expect(model.busy == false)
    }

    @Test func anAnsweredTurnIsNotTakenBack() async throws {
        let t = RouteTransport { _, path, _ in
            if path == "/api/turns" { return (200, #"{"id":"turn_answer"}"#) }
            if path.starts(with: "/api/turns/turn_answer") { return (200, #"{"events":[],"next":0,"done":false}"#) }
            return (200, "{}")
        }
        let defaults = UserDefaults(suiteName: "kelo-call-answered-\(UUID().uuidString)")!
        let app = AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
                           prefs: Prefs(defaults: defaults), transport: t, cache: ScreenCache(directory: nil, key: { nil }))
        let model = ChatModel(app: app)
        #expect(model.sendFromCall(text: "hi", callId: "c_8", amend: false) == true)
        try await Task.sleep(nanoseconds: 100_000_000)
        model.apply(.text("네"))
        #expect(model.takeBackFromCall(callId: "c_8") == false)
        model.stop()
        await model.waitForTurn()
    }
}
