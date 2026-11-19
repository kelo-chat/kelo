import Testing
import Foundation
import KeloCore
@testable import KeloShared
@testable import KeloChat

private final class MockChat: CallChat, @unchecked Sendable {
    var busy: Bool = false
    var sentCalls: [(String, String)] = []
    var followedTurns: [String] = []

    func sendFromCall(text: String, callId: String) -> Bool {
        sentCalls.append((text, callId))
        return true
    }

    func followFromCall(turnId: String) {
        followedTurns.append(turnId)
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
            device: { nil },
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
}
