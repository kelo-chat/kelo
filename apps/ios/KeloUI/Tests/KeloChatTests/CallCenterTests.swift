import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

private final class CenterTestAudio: CallAudio, @unchecked Sendable {
    var speakerOn: Bool = true
    var echoCancel: Bool = true
    var hasSpeakerSwitch: Bool = true
    var onInterrupted: (@Sendable () -> Void)? = nil

    func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool { true }
    func stopMic() {}
    func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async {}
    func stopPlayback() {}
    func chime(on: Bool) {}
    func release() {}
}

/// Records how many device voices the center made, and what each one spoke.
private final class RecordingEngine: SpeechEngine, @unchecked Sendable {
    let lock = NSLock()
    var spoken: [String] = []
    var released = false
    func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void) {
        lock.withLock { spoken.append(text) }
        onDone()
    }
    func stop() {}
    func shutDown() { lock.withLock { released = true } }
}

private final class FactoryCounter: @unchecked Sendable {
    let lock = NSLock()
    private var all: [RecordingEngine] = []
    var made: Int { lock.withLock { all.count } }
    var first: RecordingEngine? { lock.withLock { all.first } }
    func next() -> RecordingEngine {
        let e = RecordingEngine()
        lock.withLock { all.append(e) }
        return e
    }
}

/// Records every request path the center reads (the voice models), answers the reads, and fails anything else.
private final class PathLog: @unchecked Sendable {
    private let lock = NSLock()
    private var paths: [String] = []
    func add(_ p: String) { lock.withLock { paths.append(p) } }
    var all: [String] { lock.withLock { paths } }
}

private final class CountingPhoneListener: PhoneListener, @unchecked Sendable {
    func listenOnce(language: String, onStarted: @escaping @Sendable () -> Void, onLevel: @escaping @Sendable (Float) -> Void, onDone: @escaping @Sendable (String?) -> Void) {}
    func cancel() {}
}

@MainActor
@Suite struct CallCenterTests {
    private func makeCenter(
        device: @escaping @Sendable () -> (any SpeechEngine)? = { nil },
        phone: @escaping @Sendable () -> (any PhoneListener)? = { nil },
    ) -> (CallCenter, ChatModel, VoiceModel, SpeechModel) {
        let t = RouteTransport { _, _, _ in (200, "{}") }
        let defaults = UserDefaults(suiteName: "kelo-center-test-\(UUID().uuidString)")!
        let app = AppModel(
            store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
            prefs: Prefs(defaults: defaults),
            transport: t,
            cache: ScreenCache(directory: nil, key: { nil })
        )
        let chat = ChatModel(app: app)
        let stt = VoiceModel(api: { nil }, initialView: VoiceView(state: "ready", runtime: "ready"))
        let speech = SpeechModel(api: { nil }, initialView: SpeechView(state: "ready", languages: ["ko"], runtime: "ready"))

        let center = CallCenter(
            api: { nil },
            stt: stt,
            speech: speech,
            audio: { CenterTestAudio() },
            device: device,
            phoneListener: phone,
            appLang: { "ko" }
        )
        return (center, chat, stt, speech)
    }

    @Test func ringFromButtonCreatesSession() {
        let (center, chat, _, _) = makeCenter()
        let cancel = center.attach(chat: chat)
        defer { cancel.cancel() }

        #expect(center.session == nil)
        center.ringFromButton()

        #expect(center.session != nil)
        #expect(center.session?.fromAgent == false)
        #expect(center.session?.ui.phase == .ringing) // ringOnButton = true -> starts ringing
        center.session?.accept()
        #expect(center.session?.ui.phase == .connecting)
        #expect(center.isMinimized == false)
    }

    @Test func attachCollectsCallOfferAndRings() {
        let (center, chat, _, _) = makeCenter()
        let cancel = center.attach(chat: chat)
        defer { cancel.cancel() }

        #expect(center.session == nil)
        chat.onTurnEvent?(.callOffer(callId: "call_abc_123", topic: "주문 문의"))

        #expect(center.session != nil)
        #expect(center.session?.callId == "call_abc_123")
        #expect(center.session?.topic == "주문 문의")
        #expect(center.session?.fromAgent == true)
        #expect(center.session?.ui.phase == .ringing)
    }

    @Test func secondRingWhileActiveIsIgnored() {
        let (center, chat, _, _) = makeCenter()
        let cancel = center.attach(chat: chat)
        defer { cancel.cancel() }

        center.ring(callId: "call_first", topic: "First", fromAgent: true, chat: chat)
        let firstSession = center.session
        #expect(firstSession?.callId == "call_first")

        // Second ring attempted while first is active
        center.ring(callId: "call_second", topic: "Second", fromAgent: true, chat: chat)
        #expect(center.session === firstSession)
        #expect(center.session?.callId == "call_first")
    }

    @Test func minimizationTogglesStateAndMute() {
        let (center, chat, _, _) = makeCenter()
        center.ring(callId: "call_1", topic: "", fromAgent: false, chat: chat)

        #expect(center.isMinimized == false)
        #expect(center.session?.ui.muted == false)

        center.minimized(true)
        #expect(center.isMinimized == true)
        #expect(center.session?.ui.muted == true)

        center.minimized(false)
        #expect(center.isMinimized == false)
        #expect(center.session?.ui.muted == false)
    }

    @Test func callMarksIdentifiesFirstOfEachRun() {
        let items: [ChatItem] = [
            .msg(.init(key: "m1", mine: true, text: "Normal 1", at: 100, fromCall: false)),
            .msg(.init(key: "m2", mine: false, text: "Normal 2", at: 200, fromCall: false)),
            .msg(.init(key: "m3", mine: true, text: "Call run 1 - first", at: 300, fromCall: true)),
            .msg(.init(key: "m4", mine: false, text: "Call run 1 - second", at: 400, fromCall: true)),
            .msg(.init(key: "m5", mine: true, text: "Normal 3", at: 500, fromCall: false)),
            .msg(.init(key: "m6", mine: true, text: "Call run 2 - first", at: 600, fromCall: true))
        ]

        let marks = ChatPage.callMarks(in: items)
        #expect(marks.count == 2)
        #expect(marks.contains("m3"))
        #expect(marks.contains("m6"))
        #expect(!marks.contains("m4"))
        #expect(!marks.contains("m1"))
        #expect(!marks.contains("m2"))
        #expect(!marks.contains("m5"))
    }

    @Test func ownTurnEventsOnlyAreSpokenWithOneDeviceVoice() async {
        let counter = FactoryCounter()
        let (center, chat, _, _) = makeCenter(device: { counter.next() })
        let link = center.attach(chat: chat)
        defer { link.cancel() }
        center.ringFromButton(chat: chat)
        center.session?.accept()
        try? await Task.sleep(nanoseconds: 100_000_000)
        guard let session = center.session, let engine = counter.first else {
            Issue.record("the call did not start")
            return
        }

        chat.onCallTurnEvent?(nil, .text("입력한 답"))
        chat.onCallTurnEvent?(nil, .done(inputTokens: 1, outputTokens: 1, steps: 1))
        chat.onCallTurnEvent?("other", .text("다른 통화"))
        try? await Task.sleep(nanoseconds: 200_000_000)
        #expect(engine.spoken.isEmpty)

        chat.onCallTurnEvent?(session.callId, .text("이 통화의 답."))
        chat.onCallTurnEvent?(session.callId, .done(inputTokens: 1, outputTokens: 1, steps: 1))
        var attempts = 0
        while engine.spoken.isEmpty && attempts < 100 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
        #expect(engine.spoken == ["이 통화의 답."])

        session.hangUp()
        #expect(counter.made == 1)
        #expect(engine.released)
    }

    @Test func callReadsTheServerModelsAndUsesServerEars() async {
        let paths = PathLog()
        let t = RouteTransport { _, path, _ in
            paths.add(path)
            if path == "/api/voice" || path.hasPrefix("/api/voice?") { return (200, #"{"state":"ready","runtime":"ready"}"#) }
            if path == "/api/voice/speech" { return (200, #"{"state":"ready","runtime":"ready","languages":["ko"]}"#) }
            return (500, "{}")
        }
        let defaults = UserDefaults(suiteName: "kelo-center-models-\(UUID().uuidString)")!
        let app = AppModel(
            store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok")),
            prefs: Prefs(defaults: defaults),
            transport: t,
            cache: ScreenCache(directory: nil, key: { nil })
        )
        let api = app.api
        let chat = ChatModel(app: app)
        let stt = VoiceModel(api: { api })
        let speech = SpeechModel(api: { api })
        let center = CallCenter(
            api: { api },
            stt: stt,
            speech: speech,
            audio: { CenterTestAudio() },
            device: { nil },
            phoneListener: { CountingPhoneListener() },
            appLang: { "ko" }
        )
        let link = center.attach(chat: chat)
        defer { link.cancel() }
        center.ringFromButton(chat: chat)
        center.session?.accept()

        var attempts = 0
        while (center.session?.ui.phase == .preparing || center.session?.ui.phase == .ringing) && attempts < 150 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
        guard let session = center.session else {
            Issue.record("the call ended")
            return
        }
        #expect(session.ui.ears == .server)
        #expect(paths.all.contains { $0 == "/api/voice" || $0.hasPrefix("/api/voice?") })
        #expect(paths.all.contains("/api/voice/speech"))

        chat.onCallTurnEvent?(session.callId, .text("안녕하세요."))
        chat.onCallTurnEvent?(session.callId, .done(inputTokens: 1, outputTokens: 1, steps: 1))
        attempts = 0
        while !paths.all.contains("/api/voice/speak") && attempts < 150 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
        #expect(paths.all.contains("/api/voice/speak"))
        session.hangUp()
    }
}
