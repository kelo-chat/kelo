import Testing
import Foundation
@testable import KeloShared

/// The recording bar's rules, the same numbers as Android's RecordingBarTest (apps/shared desktopTest voice/RecordingBarTest.kt).
struct RecordingBarTests {
    private func pcm(seconds: Double, amplitude: Double = 0) -> Data {
        let n = Int(16_000 * seconds)
        var out = Data(capacity: n * 2)
        for i in 0..<n {
            let v = Int16(amplitude * 32_767 * sin(2 * Double.pi * 440 * Double(i) / 16_000))
            out.append(UInt8(truncatingIfNeeded: v))
            out.append(UInt8(truncatingIfNeeded: v >> 8))
        }
        return out
    }

    @Test func showBarOnlyWhileListeningAndNotHeld() {
        #expect(!RecordingBarRules.showBar(nil))
        #expect(!RecordingBarRules.showBar(RecordingBarState(stage: .listening, elapsedMs: 0, endsInSeconds: nil, held: true)))
        #expect(RecordingBarRules.showBar(RecordingBarState(stage: .listening, elapsedMs: 0, endsInSeconds: nil, held: false)))
    }

    @Test func levelMapsDbfsFromMinus55ToMinus15() {
        #expect(RecordingBarRules.level(rms: 0) == 0)
        #expect(RecordingBarRules.level(rms: 0.001) == 0)
        #expect(abs(RecordingBarRules.level(rms: 0.1778) - 1) < 0.0001)
        #expect(abs(RecordingBarRules.level(rms: 0.1 / Float(2).squareRoot()) - 0.8) < 0.05)
    }

    @Test func heightVisibleBarsAndEndsIn() {
        #expect(RecordingBarRules.height(0) == 3)
        #expect(RecordingBarRules.height(1) == 24)
        #expect(RecordingBarRules.visibleBars(widthDp: 200) == 40)
        #expect(RecordingBarRules.endsIn(elapsedMs: 109_999) == nil)
        #expect(RecordingBarRules.endsIn(elapsedMs: 110_000) == 10)
        #expect(RecordingBarRules.endsIn(elapsedMs: 120_000) == 0)
    }

    @Test func sendIsOffWhileTurningToTextOrKeloReplies() {
        #expect(!RecordingBarRules.sendEnabled(.turningToText, busy: false))
        #expect(!RecordingBarRules.sendEnabled(.listening, busy: true))
        #expect(RecordingBarRules.sendEnabled(.listening, busy: false))
    }

    @Test func levelMeterGivesTwelvePerSecondWithCarry() {
        let meter = LevelMeter()
        let audio = pcm(seconds: 1, amplitude: 0.1)
        var count = 0
        for c in 0..<10 { count += meter.add(audio.subdata(in: c * 3_200..<(c + 1) * 3_200)).count }
        #expect(count == 12)
        count += meter.add(pcm(seconds: 0.06, amplitude: 0.1)).count
        #expect(count == 13)
    }

    @Test func oddChunkSizesMatchOneBigChunk() {
        let audio = pcm(seconds: 1, amplitude: 0.05)
        let whole = LevelMeter().add(audio)
        let meter = LevelMeter()
        var pieces: [Float] = []
        var at = 0
        while at < audio.count {
            let end = min(audio.count, at + 333)
            pieces += meter.add(audio.subdata(in: at..<end))
            at = end
        }
        #expect(pieces == whole)
    }

    @Test func levelTrailKeepsNewestAndReleases() {
        let trail = LevelTrail(capacity: 4)
        for i in 1...6 { trail.push(Float(i) / 10, atNanos: Int64(i)) }
        #expect(trail.snapshot().count == 4)
        #expect(abs((trail.snapshot().last ?? 0) - 0.6) < 0.0001)
        let fade = LevelTrail()
        fade.push(1, atNanos: 1)
        fade.push(0, atNanos: 2)
        #expect(abs((fade.snapshot().last ?? 0) - 0.55) < 0.0001)
        let v = fade.version
        fade.clear()
        #expect(fade.version == v + 1)
        #expect(fade.snapshot().isEmpty)
    }

    @Test func phoneStepsSendOnlyAfterSendAndNeverAfterAnError() {
        let start = PhoneDictationSteps.step(PhoneDictationSteps.State(listening: false, after: nil), .start).0
        let sent = PhoneDictationSteps.step(start, .finish(.send))
        #expect(sent.1 == .stopRecognizer)
        #expect(PhoneDictationSteps.step(sent.0, .ended(problem: false)).1 == .sendField)
        #expect(PhoneDictationSteps.step(sent.0, .ended(problem: true)).1 == .none)
        let field = PhoneDictationSteps.step(start, .finish(.field))
        #expect(PhoneDictationSteps.step(field.0, .ended(problem: false)).1 == .none)
        #expect(PhoneDictationSteps.step(start, .discard).1 == .restoreField)
    }

    @Test func downloadRingFollowsTheModelDownload() {
        let progress = VoiceView.Progress(done: 50, total: 100)
        let downloading = VoiceView(state: "downloading", declined: nil, recommended: nil, model: nil, progress: progress, error: nil,
                                    models: nil, maxSeconds: 120, runtime: "ready")
        #expect(VoiceDownloadRing.of(downloading) == VoiceDownloadRing.Ring(percent: 50))
        let noNumber = VoiceView(state: "downloading", declined: nil, recommended: nil, model: nil, progress: nil, error: nil,
                                 models: nil, maxSeconds: 120, runtime: "ready")
        #expect(VoiceDownloadRing.of(noNumber) == VoiceDownloadRing.Ring(percent: nil))
        let failed = VoiceView(state: "failed", declined: nil, recommended: nil, model: nil, progress: nil, error: nil,
                               models: nil, maxSeconds: 120, runtime: "ready")
        #expect(VoiceDownloadRing.of(failed) == nil)
        #expect(VoiceDownloadRing.of(nil) == nil)
    }
}
