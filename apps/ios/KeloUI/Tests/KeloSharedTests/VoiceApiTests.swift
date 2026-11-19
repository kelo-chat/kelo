import Testing
import Foundation
import KeloCore
@testable import KeloShared

private final class MockTransport: HTTPTransport, @unchecked Sendable {
    private let lock = NSLock()
    private var responses: [(Int, Data)]
    private(set) var recordedRequests: [URLRequest] = []

    init(_ responses: [(Int, Data)] = []) {
        self.responses = responses
    }

    convenience init(_ status: Int, _ body: String) {
        self.init([(status, Data(body.utf8))])
    }

    func push(_ status: Int, _ body: String) {
        lock.withLock {
            responses.append((status, Data(body.utf8)))
        }
    }

    func send(_ request: URLRequest) async throws -> (Int, Data) {
        lock.withLock {
            recordedRequests.append(request)
            if responses.isEmpty {
                return (200, Data("{}".utf8))
            }
            return responses.removeFirst()
        }
    }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (code, data) = try await send(request)
        return (code, AsyncThrowingStream { c in for b in data { c.yield(b) }; c.finish() })
    }
}

@Suite struct VoiceApiTests {
    private func makeApi(_ t: HTTPTransport) -> KeloApi {
        KeloApi(baseUrl: "https://kelo.test", token: "test-token", transport: t)
    }

    @Test func speechViewReturnsParsedViewOrNilOn404() async throws {
        let json = """
        {"state":"ready","declined":false,"runtime":"ready","model":{"id":"supertonic-3","name":"Supertonic 3","size_bytes":1000},"voice":0,"speed":1.0,"voices":[]}
        """
        let t1 = MockTransport(200, json)
        let view = try await VoiceAPI.speechView(makeApi(t1))
        #expect(view?.state == "ready")
        #expect(view?.model?.id == "supertonic-3")
        #expect(t1.recordedRequests.first?.url?.path == "/api/voice/speech")

        let t2 = MockTransport(404, #"{"detail":"Not Found"}"#)
        let viewNil = try await VoiceAPI.speechView(makeApi(t2))
        #expect(viewNil == nil)
    }

    @Test func speechChooseDeclineRemoveAndPrefs() async throws {
        let respJson = """
        {"state":"ready","declined":false,"runtime":"ready","model":{"id":"supertonic-3","name":"Supertonic 3"},"voice":3,"speed":1.2,"voices":[]}
        """
        let t = MockTransport(200, respJson)
        let api = makeApi(t)

        let chooseRes = try await VoiceAPI.speechChoose(api)
        #expect(chooseRes.state == "ready")
        #expect(t.recordedRequests.last?.httpMethod == "POST")
        #expect(t.recordedRequests.last?.url?.path == "/api/voice/speech/model")

        t.push(200, respJson)
        _ = try await VoiceAPI.speechDecline(api)
        #expect(t.recordedRequests.last?.httpMethod == "POST")
        #expect(t.recordedRequests.last?.url?.path == "/api/voice/speech/decline")

        t.push(200, respJson)
        _ = try await VoiceAPI.speechRemove(api)
        #expect(t.recordedRequests.last?.httpMethod == "DELETE")
        #expect(t.recordedRequests.last?.url?.path == "/api/voice/speech/model")

        t.push(200, respJson)
        _ = try await VoiceAPI.speechPrefs(api, voice: 3, speed: 1.2)
        #expect(t.recordedRequests.last?.httpMethod == "PUT")
        #expect(t.recordedRequests.last?.url?.path == "/api/voice/speech/prefs")
        let sentBody = String(decoding: t.recordedRequests.last?.httpBody ?? Data(), as: UTF8.self)
        #expect(sentBody.contains(#""voice":3"#))
        #expect(sentBody.contains(#""speed":1.2"#))
    }

    @Test func speakReturnsDataOrThrowsSpeakException() async throws {
        let pcmData = Data([0x01, 0x02, 0x03, 0x04])
        let t = MockTransport([(200, pcmData)])
        let api = makeApi(t)

        let wav = try await VoiceAPI.speak(api, text: "안녕하세요", lang: "ko", voice: 2, speed: 1.0)
        #expect(wav == pcmData)
        #expect(t.recordedRequests.last?.url?.path == "/api/voice/speak")
        let sent = String(decoding: t.recordedRequests.last?.httpBody ?? Data(), as: UTF8.self)
        #expect(sent.contains(#""text":"안녕하세요""#))
        #expect(sent.contains(#""lang":"ko""#))
        #expect(sent.contains(#""voice":2"#))

        // Speak error mappings
        t.push(409, #"{"detail":"not ready","code":"voice_speech_not_ready"}"#)
        do {
            _ = try await VoiceAPI.speak(api, text: "hi", lang: "en")
            Issue.record("Expected SpeakException")
        } catch let e as SpeakException {
            #expect(e.problem == .notReady)
        }

        t.push(400, #"{"detail":"lang unsupported","code":"voice_speech_lang"}"#)
        do {
            _ = try await VoiceAPI.speak(api, text: "hi", lang: "fr")
            Issue.record("Expected SpeakException")
        } catch let e as SpeakException {
            #expect(e.problem == .lang)
        }

        t.push(429, #"{"detail":"server busy","code":"voice_busy"}"#)
        do {
            _ = try await VoiceAPI.speak(api, text: "hi", lang: "en")
            Issue.record("Expected SpeakException")
        } catch let e as SpeakException {
            #expect(e.problem == .busy)
        }
    }

    @Test func answeredStartsTurnAndReturnsId() async throws {
        let t = MockTransport(200, #"{"id":"turn_call_123"}"#)
        let api = makeApi(t)

        let turnId = try await VoiceAPI.answered(api, callId: "c_99887766", timezone: "Asia/Seoul", topic: "Book hotel")
        #expect(turnId == "turn_call_123")
        #expect(t.recordedRequests.last?.httpMethod == "POST")
        #expect(t.recordedRequests.last?.url?.path == "/api/calls/c_99887766/answered")
        let sent = String(decoding: t.recordedRequests.last?.httpBody ?? Data(), as: UTF8.self)
        #expect(sent.contains(#""timezone":"Asia/Seoul""#))
        #expect(sent.contains(#""topic":"Book hotel""#))
    }

    @MainActor
    @Test func speechModelRefreshAndChoose() async throws {
        let respJson = """
        {"state":"ready","declined":false,"runtime":"ready","model":{"id":"supertonic-3","name":"Supertonic 3"},"voice":0,"speed":1.0,"voices":[]}
        """
        let t = MockTransport(200, respJson)
        let api = makeApi(t)
        let model = SpeechModel(api: { api })
        #expect(model.view == nil)

        model.refresh()
        var attempts = 0
        while model.view == nil && attempts < 50 {
            try await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
        #expect(model.view?.state == "ready")
        #expect(model.view?.model?.id == "supertonic-3")
        #expect(model.error == nil)
    }

    @MainActor
    @Test func voiceModelRefreshAndInsert() async throws {
        let respJson = """
        {"state":"ready","declined":false,"runtime":"ready","model":{"id":"sensevoice","name":"SenseVoice Small"},"models":[]}
        """
        let t = MockTransport(200, respJson)
        let api = makeApi(t)
        let model = VoiceModel(api: { api }, lang: { "ko" })
        #expect(model.view == nil)

        model.refresh()
        var attempts2 = 0
        while model.view == nil && attempts2 < 50 {
            try await Task.sleep(nanoseconds: 20_000_000)
            attempts2 += 1
        }
        #expect(model.view?.state == "ready")
        #expect(model.error == nil)

        // VoiceInsert tests
        let m1 = VoiceInsert.merge(anchor: .init(before: "안녕", after: " 세상"), spoken: "하세요")
        #expect(m1.text == "안녕 하세요 세상")
        #expect(m1.cursor == 6)

        let m2 = VoiceInsert.merge(anchor: .init(before: "Hello ", after: "!"), spoken: "world")
        #expect(m2.text == "Hello world!")
        #expect(m2.cursor == 11)

        let mEmpty = VoiceInsert.merge(anchor: .init(before: "abc", after: "def"), spoken: "   ")
        #expect(mEmpty.text == "abcdef")
        #expect(mEmpty.cursor == 3)
    }
}
