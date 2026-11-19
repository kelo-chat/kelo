import Testing
import Foundation
import KeloCore
@testable import KeloShared
@testable import KeloChat

private final class MockCallAudio: CallAudio, @unchecked Sendable {
    var speakerOn: Bool = true
    var echoCancel: Bool = true
    var hasSpeakerSwitch: Bool = true
    var onInterrupted: (@Sendable () -> Void)? = nil

    var playedPcm: [Data] = []
    var stoppedPlayback = false

    func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool { true }
    func stopMic() {}
    func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async {
        playedPcm.append(pcm16le)
        onLevel(0.5)
    }
    func stopPlayback() { stoppedPlayback = true }
    func chime(on: Bool) {}
    func release() {}
}

private final class MockSpeechEngine: SpeechEngine, @unchecked Sendable {
    var spoken: [(String, String)] = []
    func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void) {
        spoken.append((text, lang))
        onDone()
    }
    func stop() {}
}

@Suite struct SpeechQueueTests {
    @Test func prefetchAndPlaybackServerAudio() async throws {
        let audio = MockCallAudio()
        let device = MockSpeechEngine()

        var events: [SpeechQueue.Event] = []
        let queue = SpeechQueue(
            audio: audio,
            server: { text, lang in
                // Return valid 16kHz mono 16-bit WAV
                let pcm = Data(repeating: 0, count: 320)
                return Wav.encode(pcm16le: pcm, sampleRate: 16_000)
            },
            device: device,
            serverLang: { _ in "ko" },
            appLang: { "ko" },
            onLevel: { _ in },
            onEvent: { ev in events.append(ev) }
        )

        queue.add("안녕하세요.")
        queue.add("반갑습니다.")

        // Wait for playback to finish
        var attempts = 0
        while queue.playing && attempts < 50 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }

        #expect(audio.playedPcm.count == 2)
        #expect(events.contains(.firstAudio))
        #expect(events.contains(.firstPlay))
        #expect(events.contains(.idle))
    }

    @Test func fallbackToDeviceEngineWhenNoServerLang() async throws {
        let audio = MockCallAudio()
        let device = MockSpeechEngine()

        var events: [SpeechQueue.Event] = []
        let queue = SpeechQueue(
            audio: audio,
            server: { _, _ in nil },
            device: device,
            serverLang: { _ in nil }, // No server language -> device fallback
            appLang: { "ko" },
            onLevel: { _ in },
            onEvent: { ev in events.append(ev) }
        )

        queue.add("기기 음성으로 말합니다.")

        var attempts = 0
        while queue.playing && attempts < 50 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }

        #expect(audio.playedPcm.isEmpty)
        #expect(device.spoken.count == 1)
        #expect(device.spoken.first?.0 == "기기 음성으로 말합니다.")
        #expect(events.contains(.deviceSpoke))
    }

    @Test func stopCancelsPendingAndStopsAudio() {
        let audio = MockCallAudio()
        let device = MockSpeechEngine()

        let queue = SpeechQueue(
            audio: audio,
            server: { _, _ in nil },
            device: device,
            serverLang: { _ in "ko" },
            appLang: { "ko" },
            onLevel: { _ in },
            onEvent: { _ in }
        )

        queue.add("첫 번째 문장입니다.")
        queue.add("두 번째 문장입니다.")
        queue.stop()

        #expect(audio.stoppedPlayback == true)
        #expect(queue.playing == false)
    }

    @Test func onePieceWithASlowFetchIsPlayed() async throws {
        let audio = MockCallAudio()
        let device = MockSpeechEngine()
        var events: [SpeechQueue.Event] = []
        let queue = SpeechQueue(
            audio: audio,
            server: { _, _ in
                try? await Task.sleep(nanoseconds: 50_000_000)
                return Wav.encode(pcm16le: Data(repeating: 0, count: 320), sampleRate: 16_000)
            },
            device: device,
            serverLang: { _ in "ko" },
            appLang: { "ko" },
            onLevel: { _ in },
            onEvent: { ev in events.append(ev) }
        )
        queue.add("안녕하세요.")
        var attempts = 0
        while queue.playing && attempts < 150 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
        #expect(audio.playedPcm.count == 1)
        #expect(events.contains(.firstPlay))
        #expect(events.last == .idle)
    }

    @Test func stopThenAddPlaysOnlyTheNewPiece() async throws {
        let audio = MockCallAudio()
        let device = MockSpeechEngine()
        var events: [SpeechQueue.Event] = []
        let queue = SpeechQueue(
            audio: audio,
            server: { _, _ in
                try? await Task.sleep(nanoseconds: 50_000_000)
                return Wav.encode(pcm16le: Data(repeating: 0, count: 320), sampleRate: 16_000)
            },
            device: device,
            serverLang: { _ in "ko" },
            appLang: { "ko" },
            onLevel: { _ in },
            onEvent: { ev in events.append(ev) }
        )
        queue.add("지난 문장입니다.")
        try? await Task.sleep(nanoseconds: 10_000_000)
        queue.stop()
        queue.add("새 문장입니다.")
        var attempts = 0
        while queue.playing && attempts < 150 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            attempts += 1
        }
        #expect(audio.playedPcm.count == 1)
        #expect(events.filter { $0 == .idle }.count == 1)
    }
}
