import Testing
import Foundation
import KeloCore
@testable import KeloShared
@testable import KeloChat

private final class MockChat: CallChat, @unchecked Sendable {
    var callBusy: Bool = false
    var sentCalls: [(String, String)] = []
    var amends: [Bool] = []
    var followedTurns: [String] = []

    func sendFromCall(text: String, callId: String, amend: Bool) -> Bool {
        guard !callBusy else { return false }
        sentCalls.append((text, callId))
        amends.append(amend)
        return true
    }
    func takeBackFromCall(callId: String) -> Bool { false }

    func followFromCall(turnId: String, callId: String) -> Bool {
        followedTurns.append(turnId)
        return true
    }
}

private final class MockPhoneListener: PhoneListener, @unchecked Sendable {
    var listening = false
    var cancelled = false

    func listenOnce(language: String, onStarted: @escaping @Sendable () -> Void, onLevel: @escaping @Sendable (Float) -> Void, onDone: @escaping @Sendable (String?) -> Void) {
        listening = true
        onStarted()
        onLevel(0.8)
        onDone("말씀드렸어요")
    }

    func cancel() {
        cancelled = true
        listening = false
    }
}

private final class CountingPhoneListener: PhoneListener, @unchecked Sendable {
    var starts = 0
    var cancels = 0
    var lastDone: (@Sendable (String?) -> Void)? = nil

    func listenOnce(language: String, onStarted: @escaping @Sendable () -> Void, onLevel: @escaping @Sendable (Float) -> Void, onDone: @escaping @Sendable (String?) -> Void) {
        starts += 1
        lastDone = onDone
    }

    func cancel() { cancels += 1 }
}

/// Records what the device voice was asked to say.
private final class SpokenLog: SpeechEngine, @unchecked Sendable {
    private let lock = NSLock()
    private var all: [String] = []
    var spoken: [String] { lock.withLock { all } }
    func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void) {
        lock.withLock { all.append(text) }
        onDone()
    }
    func stop() {}
}

/// A fake microphone permission port: the answer to the question is set by the test.
private final class PermissionAudio: CallAudio, @unchecked Sendable {
    var speakerOn: Bool = true
    var echoCancel: Bool = true
    var hasSpeakerSwitch: Bool = true
    var onInterrupted: (@Sendable () -> Void)? = nil
    var micGranted: Bool = false
    var answer: Bool = true
    var asked = 0

    func requestMic(_ done: @escaping @Sendable (Bool) -> Void) {
        asked += 1
        done(answer)
    }
    func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool { true }
    func stopMic() {}
    func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async {}
    func stopPlayback() {}
    func chime(on: Bool) {}
    func release() {}
}

private final class SessionTestAudio: CallAudio, @unchecked Sendable {
    var speakerOn: Bool = true
    var echoCancel: Bool = true
    var hasSpeakerSwitch: Bool = true
    var onInterrupted: (@Sendable () -> Void)? = nil

    var chimeState: Bool = false
    var micStarted: Bool = false
    var micStopped: Bool = false
    var released: Bool = false

    func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool {
        micStarted = true
        return true
    }

    func stopMic() {
        micStopped = true
    }

    func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async {}
    func stopPlayback() {}

    func chime(on: Bool) {
        chimeState = on
    }

    func release() {
        released = true
    }
}

@MainActor
@Suite struct CallSessionTests {
    private func makeSession(
        fromAgent: Bool = true,
        sttState: String = "ready"
    ) -> (CallSession, SessionTestAudio, MockChat, VoiceModel, SpeechModel) {
        let audio = SessionTestAudio()
        let chat = MockChat()
        let stt = VoiceModel(api: { nil }, initialView: VoiceView(state: sttState, runtime: "ready"))
        let speech = SpeechModel(api: { nil }, initialView: SpeechView(state: "ready", languages: ["ko"], runtime: "ready"))

        let session = CallSession(
            callId: "test_call_123",
            topic: "테스트 통화",
            fromAgent: fromAgent,
            chat: chat,
            api: { nil },
            audio: audio,
            stt: stt,
            speech: speech,
            phoneListener: MockPhoneListener(),
            device: nil,
            appLang: { "ko" },
            clock: { 1_000_000 }
        )
        return (session, audio, chat, stt, speech)
    }

    @Test func incomingCallInitialStateAndChime() {
        let (session, audio, _, _, _) = makeSession()
        #expect(session.ui.phase == .ringing)
        #expect(session.callId == "test_call_123")
        #expect(session.topic == "테스트 통화")
        #expect(session.fromAgent == true)
        #expect(audio.chimeState == true)
    }

    @Test func acceptTransitionsToConnectingAndStopsChime() {
        let (session, audio, _, _, _) = makeSession()
        session.accept()
        #expect(audio.chimeState == false)
        // With stt ready, readiness is .nothing, so it steps to .prepared -> .connecting
        #expect(session.ui.phase == .connecting)
        #expect(audio.micStarted == true)
    }

    @Test func declineEndsCall() {
        let (session, audio, _, _, _) = makeSession()
        session.decline()
        #expect(session.ui.phase == .ended)
        #expect(session.ui.endedReason == .declined)
        #expect(audio.chimeState == false)
    }

    @Test func hangUpEndsCall() {
        let (session, audio, _, _, _) = makeSession()
        session.accept()
        session.hangUp()
        #expect(session.ui.phase == .ended)
        #expect(session.ui.endedReason == .hungUp)
        #expect(audio.released == true)
    }

    @Test func backgroundEndsCall() {
        let (session, audio, _, _, _) = makeSession()
        session.accept()
        session.appBackground()
        #expect(session.ui.phase == .ended)
        #expect(session.ui.endedReason == .background)
        #expect(audio.released == true)
    }

    @Test func audioInterruptionEndsCall() {
        let (session, audio, _, _, _) = makeSession()
        session.accept()
        audio.onInterrupted?()
        #expect(session.ui.phase == .ended)
        #expect(session.ui.endedReason == .interrupted)
    }

    @Test func muteAndSpeakerControls() {
        let (session, audio, _, _, _) = makeSession()
        #expect(session.ui.muted == false)
        session.setMuted(true)
        #expect(session.ui.muted == true)

        #expect(session.ui.speakerOn == true)
        session.setSpeaker(false)
        #expect(session.ui.speakerOn == false)
        #expect(audio.speakerOn == false)
    }

    @Test func paymentStepSetsCardWaiting() {
        let (session, _, _, _, _) = makeSession()
        session.accept()
        #expect(session.ui.cardWaiting == false)

        session.onTurnEvent(.paymentStep(PaymentStep(stepId: "ps_1", kind: "otp", state: "waiting")))
        #expect(session.ui.cardWaiting == true)
    }

    private func makePhoneSession(chat: MockChat, listener: CountingPhoneListener, audio: SessionTestAudio) -> CallSession {
        let stt = VoiceModel(api: { nil }, initialView: nil)
        let speech = SpeechModel(api: { nil }, initialView: nil)
        return CallSession(
            callId: "phone_call_1",
            topic: "",
            fromAgent: true,
            chat: chat,
            api: { nil },
            audio: audio,
            stt: stt,
            speech: speech,
            phoneListener: listener,
            device: nil,
            appLang: { "ko" },
            clock: { 1_000_000 },
            phoneRetry: { $0 == 0 ? 0 : 50 }
        )
    }

    @Test func phoneEarsListenOnEntryAndBackOff() async {
        let chat = MockChat()
        let listener = CountingPhoneListener()
        let audio = SessionTestAudio()
        let session = makePhoneSession(chat: chat, listener: listener, audio: audio)
        session.accept()
        await poll { session.ui.phase == .listening && listener.starts == 1 }
        #expect(session.ui.phase == .listening)
        #expect(listener.starts == 1)
        #expect(audio.micStarted == false)

        listener.lastDone?("")
        try? await Task.sleep(nanoseconds: 20_000_000)
        #expect(listener.starts == 1)
        try? await Task.sleep(nanoseconds: 150_000_000)
        #expect(listener.starts == 2)

        listener.lastDone?("내일 일정")
        await poll { chat.sentCalls.count == 1 }
        #expect(chat.sentCalls.first?.0 == "내일 일정")
        session.hangUp()
    }

    @Test func heldWordsAreSentWhenTheChatIsFree() async {
        let chat = MockChat()
        let listener = CountingPhoneListener()
        let audio = SessionTestAudio()
        let session = makePhoneSession(chat: chat, listener: listener, audio: audio)
        session.accept()
        await poll { session.ui.phase == .listening && listener.starts == 1 }

        chat.callBusy = true
        listener.lastDone?("메일 확인해줘")
        await poll { session.ui.phase == .waiting }
        #expect(chat.sentCalls.isEmpty)

        chat.callBusy = false
        session.onChatBusyChanged(false)
        #expect(chat.sentCalls.count == 1)
        #expect(chat.sentCalls.first?.0 == "메일 확인해줘")
        session.hangUp()
    }

    /// Waits (at most about 3 s) until [condition] holds on the main actor.
    private func poll(_ condition: () -> Bool) async {
        var attempts = 0
        while !condition() && attempts < 150 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
    }

    @Test func callLinesShowWordsAndReplyAndTypedWordsGoAsACallTurn() async {
        let chat = MockChat()
        let listener = CountingPhoneListener()
        let audio = SessionTestAudio()
        let session = makePhoneSession(chat: chat, listener: listener, audio: audio)
        session.accept()
        await poll { session.ui.phase == .listening && listener.starts == 1 }

        listener.lastDone?("오늘 날씨 어때?")
        await poll { chat.sentCalls.count == 1 }
        session.onTurnEvent(.text("맑고 "))
        session.onTurnEvent(.text("따뜻해요."))
        session.onTurnEvent(.done(inputTokens: 1, outputTokens: 1, steps: 1))
        #expect(session.lines.map(\.mine) == [true, false])
        #expect(session.lines.map(\.text) == ["오늘 날씨 어때?", "맑고 따뜻해요."])

        await poll { session.ui.phase == .listening }
        session.sendTyped("  내일은?  ")
        #expect(chat.sentCalls.last?.0 == "내일은?")
        #expect(session.lines.last?.mine == true)
        #expect(session.lines.last?.text == "내일은?")
        session.hangUp()
    }

    @Test func cardLineIsSpokenInTheAppLanguage() async {
        let previous = Strings.shared.code
        defer { Strings.shared.use(previous) }
        Strings.shared.use("en")
        let engine = SpokenLog()
        let chat = MockChat()
        let stt = VoiceModel(api: { nil }, initialView: VoiceView(state: "ready", runtime: "ready"))
        let session = CallSession(
            callId: "card_call_1", topic: "", fromAgent: true, chat: chat, api: { nil }, audio: SessionTestAudio(),
            stt: stt, speech: SpeechModel(api: { nil }, initialView: nil), phoneListener: nil, device: engine,
            appLang: { "en" }, clock: { 1_000_000 }
        )
        session.onTurnEvent(.paymentStep(PaymentStep(stepId: "ps_2", kind: "otp", state: "waiting")))
        await poll { !engine.spoken.isEmpty }
        #expect(engine.spoken == ["There's something to check on the screen."])
        session.hangUp()
    }

    private func permissionSession(_ audio: PermissionAudio) -> CallSession {
        CallSession(
            callId: "perm_call_1", topic: "", fromAgent: false, chat: MockChat(), api: { nil }, audio: audio,
            stt: VoiceModel(api: { nil }, initialView: VoiceView(state: "ready", runtime: "ready")),
            speech: SpeechModel(api: { nil }, initialView: nil), phoneListener: nil, device: nil,
            appLang: { "ko" }, clock: { 1_000_000 }
        )
    }

    @Test func aCallWithoutTheMicrophoneAsksForItAndEndsWhenRefused() async {
        let audio = PermissionAudio()
        audio.answer = false
        let session = permissionSession(audio)
        session.accept()
        try? await Task.sleep(nanoseconds: 50_000_000)
        #expect(audio.asked == 1)
        #expect(session.ui.phase == .ended)
        #expect(session.ui.endedReason == .micDenied)
    }

    @Test func aGrantedMicrophoneLetsTheCallGoOn() async {
        let audio = PermissionAudio()
        audio.answer = true
        let session = permissionSession(audio)
        session.accept()
        try? await Task.sleep(nanoseconds: 50_000_000)
        #expect(audio.asked == 1)
        #expect(session.ui.endedReason == nil)
        #expect(session.ui.phase != .preparing && session.ui.phase != .ended)
        session.hangUp()
    }
}
