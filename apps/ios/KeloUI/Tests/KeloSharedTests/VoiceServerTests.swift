import Testing
import Foundation
@testable import KeloShared

/// Server voice input, the rules the iPhone shares with Android (DictationWayTest, WavTest, SoundBarsTest, VoiceProblemTest, and the
/// JSON of GET /api/voice).
struct VoiceServerTests {
    private func view(_ state: String, runtime: String = "ready") -> VoiceView {
        VoiceView(state: state, declined: false, recommended: nil, model: nil, progress: nil, error: nil, models: nil,
                  maxSeconds: 120, runtime: runtime)
    }

    @Test func noViewMeansThePhoneOrNothing() {
        #expect(DictationWay.pick(nil, phoneRecognizer: true) == .phone)
        #expect(DictationWay.pick(nil, phoneRecognizer: false) == .none)
    }

    @Test func readyWithRuntimeIsTheServerWhateverThePhoneHas() {
        #expect(DictationWay.pick(view("ready"), phoneRecognizer: true) == .server)
        #expect(DictationWay.pick(view("ready"), phoneRecognizer: false) == .server)
    }

    @Test func offersAndDownloadsFollowThePhoneRecognizer() {
        #expect(DictationWay.pick(view("offered"), phoneRecognizer: true) == .offerThenPhone)
        #expect(DictationWay.pick(view("offered"), phoneRecognizer: false) == .offerThenNone)
        #expect(DictationWay.pick(view("downloading"), phoneRecognizer: true) == .downloadingPhone)
        #expect(DictationWay.pick(view("downloading"), phoneRecognizer: false) == .downloadingNone)
    }

    @Test func offDeclinedFailedUnsupportedAndMissingRuntimeAreThePhone() {
        for state in ["off", "failed", "unsupported"] {
            #expect(DictationWay.pick(view(state), phoneRecognizer: true) == .phone)
            #expect(DictationWay.pick(view(state), phoneRecognizer: false) == .none)
        }
        #expect(DictationWay.pick(view("ready", runtime: "missing"), phoneRecognizer: true) == .phone)
    }

    @Test func problemsFromStatusCodes() {
        #expect(voiceProblem(httpStatus: nil, code: nil) == .offline)
        #expect(voiceProblem(httpStatus: 429, code: "voice_busy") == .busy)
        #expect(voiceProblem(httpStatus: 413, code: "voice_too_long") == .tooLong)
        #expect(voiceProblem(httpStatus: 409, code: "voice_not_ready") == .notReady)
        #expect(voiceProblem(httpStatus: 504, code: "voice_timeout") == .failed)
    }

    @Test func wavHeaderHasTheLengthsAndTheFormat() {
        let pcm = Data(repeating: 0, count: 3_200)
        let wav = Wav.encode(pcm16le: pcm)
        #expect(wav.count == 44 + 3_200)
        #expect(Array(wav.prefix(4)) == Array("RIFF".utf8))
        #expect(Array(wav[8..<12]) == Array("WAVE".utf8))
        #expect(Array(wav[12..<16]) == Array("fmt ".utf8))
        // 16-bit PCM, mono, 16000 Hz, 32000 bytes per second, 2-byte blocks, 16 bits
        #expect(wav[22] == 1 && wav[23] == 0)
        #expect(Array(wav[24..<28]) == [0x80, 0x3E, 0, 0])
        #expect(Array(wav[28..<32]) == [0x00, 0x7D, 0, 0])
        #expect(wav[34] == 16)
        // the data chunk length and the RIFF size
        #expect(Array(wav[40..<44]) == [0x80, 0x0C, 0, 0])      // 3200 = 0x0C80
        #expect(Array(wav[4..<8]) == [0xA4, 0x0C, 0, 0])        // 36 + 3200 = 0x0CA4
    }

    @Test func soundBarsMeasureAndKeepTheNewest() {
        let silence = Data(repeating: 0, count: 1_600)
        #expect(SoundBars.rms(silence, from: 0, to: silence.count) == 0)
        var loud = Data()
        for _ in 0..<800 { loud.append(contentsOf: [0xFF, 0x7F]) }   // full scale
        #expect(SoundBars.rms(loud, from: 0, to: loud.count) > 0.9)
        var bars: [Float] = []
        for i in 0..<60 { bars = SoundBars.push(bars, Float(i) / 100) }
        #expect(bars.count == 48)
        #expect(abs((bars.last ?? 0) - 0.59) < 0.001)
    }

    @Test func clockAndCountdownUseTwoMinutes() {
        #expect(RecordingClock.clock(0) == "0:00")
        #expect(RecordingClock.clock(12_400) == "0:12")
        #expect(RecordingClock.clock(120_000) == "2:00")
        #expect(RecordingClock.countdownFromMs == 110_000)
        #expect(RecordingClock.maxMs == 120_000)
    }

    @Test func voiceViewDecodesTheServerShape() throws {
        let json = """
        {"state":"downloading","declined":false,"max_seconds":120,"runtime":"ready",
         "recommended":{"id":"sensevoice-small","name":"SenseVoice Small","maker":"FunAudioLLM","size_bytes":239233841,
                        "memory_bytes":800000000,"languages":["ko","ja","zh-Hans"],"license_name":"FunASR Model License",
                        "license_url":"https://example.org/l","source_url":"https://example.org/s","attribution":"x"},
         "model":null,"progress":{"done":10,"total":100},"error":null,"models":[]}
        """
        let v = try JSONDecoder().decode(VoiceView.self, from: Data(json.utf8))
        #expect(v.state == "downloading")
        #expect(v.recommended?.sizeBytes == 239_233_841)
        #expect(v.progress == VoiceView.Progress(done: 10, total: 100))
        #expect(v.maxSeconds == 120)
    }

    @Test func transcriptionAnswerGivesTheTrimmedText() {
        #expect(VoiceAnswer.text(from: Data(#"{"text":" 조금만 생각을 해요. ","lang":"ko","seconds":3.2,"took_ms":830}"#.utf8)) == "조금만 생각을 해요.")
        #expect(VoiceAnswer.text(from: Data(#"{"text":""}"#.utf8)) == "")
        #expect(VoiceAnswer.text(from: Data("not json".utf8)) == "")
    }
}
