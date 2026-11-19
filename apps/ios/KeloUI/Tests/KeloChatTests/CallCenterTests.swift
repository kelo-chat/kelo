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

@MainActor
@Suite struct CallCenterTests {
    private func makeCenter() -> (CallCenter, ChatModel, VoiceModel, SpeechModel) {
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
            device: { nil },
            phoneListener: { nil },
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
}
