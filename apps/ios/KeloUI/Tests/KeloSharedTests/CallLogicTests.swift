import Testing
import Foundation
@testable import KeloShared
import KeloCore

@Suite struct CallStepsTests {
    private func s(_ phase: CallSteps.Phase, held: String? = nil, busy: BooleanLiteralType = false) -> CallSteps.State {
        CallSteps.State(phase: phase, held: held, replyBusy: busy)
    }
    private func run(_ from: CallSteps.State, _ e: CallSteps.Event, bargeIn: Bool = true) -> (CallSteps.State, [CallSteps.Action]) {
        CallSteps.step(from, e, bargeIn: bargeIn)
    }

    @Test func ringingAcceptGoesToPreparingDeclineAndRingTimeoutEndIt() {
        let (s1, a1) = run(s(.ringing), .accept)
        #expect(s1 == s(.preparing))
        #expect(a1.isEmpty)

        let (s2, a2) = run(s(.ringing), .decline)
        #expect(s2 == CallSteps.State(phase: .ended, ended: .declined))
        #expect(a2 == [.end])

        let (s3, a3) = run(s(.ringing), .ringTimeout)
        #expect(s3 == CallSteps.State(phase: .ended, ended: .missed))
        #expect(a3 == [.end])
    }

    @Test func preparedOpensTheMicAndSendsTheAnswer() {
        let (s1, a1) = run(s(.preparing), .prepared)
        #expect(s1 == s(.connecting))
        #expect(a1 == [.openMic, .sendAnswered])
    }

    @Test func notPossibleWhilePreparingEndsWithItsReason() {
        let (s1, a1) = run(s(.preparing), .notPossible(.noStt))
        #expect(s1 == CallSteps.State(phase: .ended, ended: .noStt))
        #expect(a1 == [.end])
    }

    @Test func turnStartedWhileConnectingOrWaitingMarksABusyReply() {
        #expect(run(s(.connecting), .turnStarted).0 == s(.waiting, busy: true))
        #expect(run(s(.waiting), .turnStarted).0 == s(.waiting, busy: true))
    }

    @Test func firstSentenceWhileWaitingStartsSpeaking() {
        #expect(run(s(.waiting, busy: true), .firstSentence).0.phase == .speaking)
    }

    @Test func queueIdleGoesToWaitingWhileTheReplyRunsElseListening() {
        #expect(run(s(.speaking, busy: true), .queueIdle(replyBusy: true)).0.phase == .waiting)
        #expect(run(s(.speaking), .queueIdle(replyBusy: false)).0.phase == .listening)
    }

    @Test func replyEndedWithNothingQueuedListensAgain() {
        #expect(run(s(.waiting, busy: true), .replyEnded).0.phase == .listening)
    }

    @Test func listeningThenSpeechStartsHearingEndTranscribes() {
        let (h, a0) = run(s(.listening), .speechStarted)
        #expect(h.phase == .hearing)
        #expect(a0.isEmpty)
        let (t, a1) = run(h, .speechEnded(Data()))
        #expect(t.phase == .transcribing)
        #expect(a1 == [.transcribe])
    }

    @Test func speechWhileSpeakingIsBargeInOnlyWhenAllowed() {
        let (h1, a1) = run(s(.speaking), .speechStarted, bargeIn: true)
        #expect(h1.phase == .hearing)
        #expect(a1 == [.stopPlayback])

        let (h2, a2) = run(s(.speaking), .speechStarted, bargeIn: false)
        #expect(h2 == s(.speaking))
        #expect(a2.isEmpty)
    }

    @Test func speechWhileWaitingHearsAndKeepsTheReplyBusy() {
        #expect(run(s(.waiting, busy: true), .speechStarted).0 == s(.hearing, busy: true))
    }

    @Test func droppedSpeechGoesBackToWaitingOrListening() {
        #expect(run(s(.hearing, busy: true), .speechDropped).0.phase == .waiting)
        #expect(run(s(.hearing), .speechDropped).0.phase == .listening)
    }

    @Test func emptyTranscriptIsNoise() {
        #expect(run(s(.transcribing), .transcribed("")).0.phase == .listening)
        #expect(run(s(.transcribing, busy: true), .transcribed("")).0.phase == .waiting)
    }

    @Test func textWhileIdleIsSentNow() {
        let (s1, a1) = run(s(.transcribing), .transcribed("hi"))
        #expect(s1.phase == .waiting)
        #expect(a1 == [.sendText])
    }

    @Test func textDuringAReplyIsHeldAndTheNewestReplacesTheOlder() {
        let (h1, a1) = run(s(.transcribing, busy: true), .transcribed("first"))
        #expect(a1 == [.holdText])
        #expect(h1.held == "first")
        let (h2, _) = run(CallSteps.State(phase: .transcribing, held: h1.held, replyBusy: true), .transcribed("second"))
        #expect(h2.held == "second")
    }

    @Test func heldTextIsSentWhenTheReplyEnds() {
        let (after, actions) = run(CallSteps.State(phase: .speaking, held: "later", replyBusy: true), .replyEnded)
        #expect(actions == [.sendText])
        #expect(after.phase == .waiting)
        #expect(after.held == nil)
    }

    @Test func transcribeFailureGoesBackToListening() {
        #expect(run(s(.transcribing), .transcribeFailed(offline: true)).0.phase == .listening)
    }

    @Test func hangUpInAnyPhaseEndsTheCallAndStopsPlayback() {
        for p in CallSteps.Phase.allCases where p != .ended {
            let (next, actions) = run(s(p), .hangUp)
            #expect(next.phase == .ended)
            #expect(next.ended == .hungUp)
            #expect(actions == [.stopPlayback, .closeMic, .end])
        }
    }

    @Test func backgroundInterruptionAndQuietTimeoutEndWithTheirReason() {
        #expect(run(s(.listening), .background).0.ended == .background)
        #expect(run(s(.speaking), .interrupted).0.ended == .interrupted)
        #expect(run(s(.listening), .quietTimeout).0.ended == .quiet)
    }

    @Test func anEndedCallChangesNothing() {
        let ended = CallSteps.State(phase: .ended, ended: .hungUp)
        let (next, actions) = run(ended, .speechStarted)
        #expect(next == ended)
        #expect(actions.isEmpty)
    }
}

@Suite struct CallLogicTests {
    private func stt(_ state: String, runtime: String = "ready") -> VoiceView {
        VoiceView(state: state, runtime: runtime)
    }
    private func voice(_ state: String, runtime: String = "ready") -> SpeechView {
        SpeechView(state: state, languages: ["en", "ko", "pt"], runtime: runtime)
    }

    @Test func serverSpeechLanguageNeedsReadyStateRuntimeAndASupportedPrimarySubtag() {
        #expect(SpeechLang.server("pt-BR", voice: voice("ready")) == "pt")
        #expect(SpeechLang.server("ko", voice: voice("ready")) == "ko")
        #expect(SpeechLang.server("zh-Hans", voice: voice("ready")) == nil)
        #expect(SpeechLang.server("ko", voice: voice("downloading")) == nil)
        #expect(SpeechLang.server("ko", voice: nil) == nil)
        #expect(SpeechLang.server("ko", voice: voice("ready", runtime: "missing")) == nil)
    }

    @Test func clockFormats() {
        #expect(CallRules.clock(elapsedMs: 7_000) == "0:07")
        #expect(CallRules.clock(elapsedMs: 754_000) == "12:34")
        #expect(CallRules.clock(elapsedMs: 3_723_000) == "1:02:03")
        #expect(CallRules.clock(elapsedMs: -5) == "0:00")
    }

    @Test func moodsFollowThePhase() {
        #expect(CallRules.mood(for: .hearing) == .listening)
        #expect(CallRules.mood(for: .waiting) == .thinking)
        #expect(CallRules.mood(for: .speaking) == .reading)
        #expect(CallRules.mood(for: .ringing) == .idle)
    }

    @Test func earsPreferTheServerWhenReadyThenThePhoneThenNobody() {
        #expect(CallEars.pick(stt("ready"), phoneRecognizer: true) == .server)
        #expect(CallEars.pick(stt("downloading"), phoneRecognizer: true) == .phone)
        #expect(CallEars.pick(stt("ready", runtime: "missing"), phoneRecognizer: true) == .phone)
        #expect(CallEars.pick(stt("failed"), phoneRecognizer: false) == .none)
        #expect(CallEars.pick(nil, phoneRecognizer: false) == .none)
    }

    @Test func readinessAsksTheSttQuestionFirstThenTheVoiceQuestionOnce() {
        #expect(CallReadiness.next(stt: stt("ready"), voice: voice("ready"), micAllowed: false, phoneRecognizer: true, askedStt: false, askedVoice: false) == .micPermission)
        #expect(CallReadiness.next(stt: stt("offered"), voice: voice("offered"), micAllowed: true, phoneRecognizer: true, askedStt: false, askedVoice: false) == .sttQuestion)
        #expect(CallReadiness.next(stt: stt("offered"), voice: voice("offered"), micAllowed: true, phoneRecognizer: true, askedStt: true, askedVoice: false) == .voiceQuestion)
        #expect(CallReadiness.next(stt: stt("offered"), voice: voice("offered"), micAllowed: true, phoneRecognizer: true, askedStt: true, askedVoice: true) == .nothing)
    }

    @Test func aDeclinedVoiceIsAskedOnTheFirstCallOnly() {
        #expect(CallReadiness.next(stt: stt("ready"), voice: voice("off"), micAllowed: true, phoneRecognizer: true, askedStt: true, askedVoice: false) == .voiceQuestion)
        #expect(CallReadiness.next(stt: stt("ready"), voice: voice("off"), micAllowed: true, phoneRecognizer: true, askedStt: true, askedVoice: true) == .nothing)
    }

    @Test func desktopWithoutAServerSttCannotTakeACall() {
        #expect(CallReadiness.next(stt: stt("failed"), voice: voice("ready"), micAllowed: true, phoneRecognizer: false, askedStt: true, askedVoice: true) == .impossible)
    }

    @Test func theBetterVoiceLineShowsOnceTheDeviceVoiceSpokeAndTheVoiceIsNotReady() {
        #expect(CallVoiceLine.show(voice: voice("downloading"), deviceSpokeThisCall: true, dismissed: false) == true)
        #expect(CallVoiceLine.show(voice: voice("ready"), deviceSpokeThisCall: true, dismissed: false) == false)
        #expect(CallVoiceLine.show(voice: voice("offered"), deviceSpokeThisCall: false, dismissed: false) == false)
        #expect(CallVoiceLine.show(voice: voice("offered"), deviceSpokeThisCall: true, dismissed: true) == false)
        #expect(CallVoiceLine.show(voice: nil, deviceSpokeThisCall: true, dismissed: false) == false)
    }

    @Test func availabilityIsFalseOnDesktopWithoutServerSttAndTrueOnAPhoneWithARecognizer() {
        #expect(CallAvailability.of(stt: stt("failed"), micAvailable: true, phoneRecognizer: false) == false)
        #expect(CallAvailability.of(stt: stt("failed"), micAvailable: true, phoneRecognizer: true) == true)
        #expect(CallAvailability.of(stt: stt("offered"), micAvailable: true, phoneRecognizer: false) == true)
        #expect(CallAvailability.of(stt: stt("ready"), micAvailable: false, phoneRecognizer: true) == false)
    }
}

@Suite struct SentenceCutterTests {
    private func all(_ deltas: String...) -> [String] {
        let c = SentenceCutter()
        var out: [String] = []
        for d in deltas { out += c.add(d) }
        out += c.finish()
        return out
    }

    @Test func sentencesEndAtKoreanEnglishAndJapaneseMarks() {
        #expect(all("안녕하세요. 반가워요! Hi there.") == ["안녕하세요.", "반가워요!", "Hi there."])
        #expect(all("今日は晴れです。明日は雨です。") == ["今日は晴れです。", "明日は雨です。"])
    }

    @Test func decimalsAndAbbreviationsAreNotCut() {
        #expect(all("It takes 3.5 hours. U.S. is big.") == ["It takes 3.5 hours.", "U.S. is big."])
    }

    @Test func theFirstPieceIsCutAtACommaAfter80Characters() {
        let text = String(repeating: "a", count: 30) + ", " + String(repeating: "b", count: 70) + ", then more words follow here without any end"
        let pieces = all(text)
        #expect(pieces[0].hasPrefix(String(repeating: "a", count: 30) + ","))
        #expect(pieces[0].hasSuffix(","))
        #expect(pieces[0].count > 80)
    }

    @Test func aLongRunIsCutAtASpaceWithinTheMax() {
        let text = String(repeating: "word ", count: 100).trimmingCharacters(in: .whitespaces)
        let pieces = all(text)
        #expect(pieces.count > 1)
        #expect(pieces.allSatisfy { $0.count <= 300 })
        #expect(pieces.joined(separator: " ") == text)
    }

    @Test func codeFencesAreDropped() {
        let pieces = all("Look: \n```kt\nval x = 1.\n```\nDone.")
        #expect(pieces == ["Look:", "Done."])
    }

    @Test func markdownMarksAreSpokenPlain() {
        #expect(all("Some **bold** and a [link](https://d.io).") == ["Some bold and a link."])
    }

    @Test func finishFlushesTheRest() {
        #expect(all("no end here") == ["no end here"])
    }

    @Test func deltasSplitMidWordGiveTheSamePiecesAsOneString() {
        let text = "Hello there. The meeting is at 3.5 hours. Bye."
        let whole = all(text)
        let split = all("Hel", "lo the", "re. The meeting is at 3", ".5 hours", ". Bye", ".")
        #expect(whole == split)
    }
}

@Suite struct SpeechGateTests {
    private func tone(ms: Int, freq: Double = 200.0, db: Float = -20.0) -> Data {
        let samples = ms * 16
        var out = Data(count: samples * 2)
        let rmsTarget = 32768.0 * pow(10.0, Double(db) / 20.0)
        let amp = rmsTarget * sqrt(2.0)
        out.withUnsafeMutableBytes { buf in
            let ptr = buf.bindMemory(to: Int16.self)
            for i in 0..<samples {
                let v = Int16(clamping: Int(amp * sin(2.0 * .pi * freq * Double(i) / 16000.0)))
                ptr[i] = v
            }
        }
        return out
    }

    private func silence(ms: Int, db: Float = -70.0) -> Data {
        let samples = ms * 16
        var out = Data(count: samples * 2)
        let rmsTarget = 32768.0 * pow(10.0, Double(db) / 20.0)
        let amp = max(1, Int(rmsTarget * sqrt(3.0)))
        out.withUnsafeMutableBytes { buf in
            let ptr = buf.bindMemory(to: Int16.self)
            var rng: UInt64 = 42
            for i in 0..<samples {
                rng = rng &* 6364136223846793005 &+ 1
                let v = Int16((Int(rng >> 33) % (amp * 2 + 1)) - amp)
                ptr[i] = v
            }
        }
        return out
    }

    private func noise(ms: Int, db: Float = -45.0) -> Data {
        let samples = ms * 16
        var out = Data(count: samples * 2)
        let rmsTarget = 32768.0 * pow(10.0, Double(db) / 20.0)
        let amp = max(1, Int(rmsTarget * sqrt(3.0)))
        out.withUnsafeMutableBytes { buf in
            let ptr = buf.bindMemory(to: Int16.self)
            var rng: UInt64 = 1234
            for i in 0..<samples {
                rng = rng &* 6364136223846793005 &+ 1
                let v = Int16((Int(rng >> 33) % (amp * 2 + 1)) - amp)
                ptr[i] = v
            }
        }
        return out
    }

    @Test func aToneOf300MsThen700MsOfSilenceEmitsStartedThenEndedWithPreroll() {
        let gate = SpeechGate()
        var pcm = silence(ms: 400)
        pcm.append(tone(ms: 300))
        pcm.append(silence(ms: 800))
        let events = gate.feed(pcm)
        #expect(events.count == 2)
        #expect(events[0] == .started)
        guard case .ended(let data) = events[1] else {
            Issue.record("Expected .ended event")
            return
        }
        #expect(data.count >= 600 * 32)
    }

    @Test func oneHundredFiftyMsOfToneProducesNothing() {
        let gate = SpeechGate()
        var pcm = silence(ms: 300)
        pcm.append(tone(ms: 150))
        pcm.append(silence(ms: 300))
        let events = gate.feed(pcm)
        #expect(events.isEmpty)
    }

    @Test func twoHundredFiftyMsOfToneAndSilenceIsDroppedUnderMinSpeechMs() {
        let gate = SpeechGate()
        var pcm = silence(ms: 300)
        pcm.append(tone(ms: 250))
        pcm.append(silence(ms: 800))
        let events = gate.feed(pcm)
        #expect(events.count == 2)
        #expect(events[0] == .started)
        #expect(events[1] == .dropped)
    }

    @Test func steadyNoiseAtMinus45NeverStartsAndFloorFollowsIt() {
        let gate = SpeechGate()
        let pcm = noise(ms: 2000, db: -45.0)
        let events = gate.feed(pcm)
        #expect(events.isEmpty)
        #expect(gate.floorDb >= -50.0)
    }

    @Test func speakingTrueNeedsPlus18DbAnd300Ms() {
        let gate = SpeechGate()
        gate.speaking = true
        var pcmShort = silence(ms: 300)
        pcmShort.append(tone(ms: 250, db: -20.0))
        let ev1 = gate.feed(pcmShort)
        #expect(ev1.isEmpty)

        let ev2 = gate.feed(tone(ms: 100, db: -20.0))
        #expect(ev2 == [.started])
    }

    @Test func sixtyOneSecondsOfToneEndsAt60S() {
        let gate = SpeechGate()
        var events: [SpeechGate.Out] = []
        let chunk1s = tone(ms: 1000)
        for _ in 0..<61 {
            events.append(contentsOf: gate.feed(chunk1s))
        }
        #expect(events.contains(.started))
        let ended = events.compactMap { ev -> Data? in
            if case .ended(let d) = ev { return d }
            return nil
        }
        #expect(ended.count == 1)
        #expect(ended[0].count == 60 * 16000 * 2)
    }

    @Test func oddChunkSizesGiveTheSameResultAs640() {
        let gate1 = SpeechGate()
        let gate2 = SpeechGate()
        var pcm = silence(ms: 400)
        pcm.append(tone(ms: 300))
        pcm.append(silence(ms: 800))

        var ev1: [SpeechGate.Out] = []
        var i = 0
        while i < pcm.count {
            let chunk = pcm.subdata(in: i..<min(i + 640, pcm.count))
            ev1.append(contentsOf: gate1.feed(chunk))
            i += 640
        }

        var ev2: [SpeechGate.Out] = []
        var j = 0
        while j < pcm.count {
            let chunk = pcm.subdata(in: j..<min(j + 333, pcm.count))
            ev2.append(contentsOf: gate2.feed(chunk))
            j += 333
        }

        #expect(ev1.count == ev2.count)
        #expect(ev1[0] == ev2[0])
        guard case .ended(let d1) = ev1[1], case .ended(let d2) = ev2[1] else {
            Issue.record("Expected .ended")
            return
        }
        #expect(d1.count == d2.count)
    }
}

@Suite struct CallTimingsTests {
    @Test func fullTurnLineFormatMatchesBriefSpecification() {
        let t = CallTimings()
        t.mark(.speechEnd, atMs: 1000)
        t.mark(.sttDone, atMs: 1420)
        t.mark(.turnStarted, atMs: 1480)
        t.mark(.firstText, atMs: 3310)
        t.mark(.firstSentence, atMs: 3620)
        t.mark(.firstAudio, atMs: 4140)
        t.mark(.playStart, atMs: 4180)
        #expect(t.line() == "speech_end→stt=420 stt→turn=60 turn→text=1830 text→sentence=310 sentence→audio=520 audio→play=40 total=3180")
    }

    @Test func missingMarksAreOmittedGracefully() {
        let t = CallTimings()
        t.mark(.speechEnd, atMs: 1000)
        t.mark(.sttDone, atMs: 1420)
        t.mark(.playStart, atMs: 4180)
        #expect(t.line() == "speech_end→stt=420 total=3180")
    }

    @Test func emptyTimingsReturnsEmptyLine() {
        let t = CallTimings()
        #expect(t.line() == "")
    }
}

@Suite struct WavDecodeTests {
    @Test func roundTripWithWavEncode() {
        var pcm = Data(count: 3200)
        for i in 0..<3200 { pcm[i] = UInt8(i % 256) }
        let encoded = Wav.encode(pcm16le: pcm, sampleRate: 44100)
        let decoded = Wav.decode(encoded)
        #expect(decoded != nil)
        #expect(decoded?.sampleRate == 44100)
        #expect(decoded?.pcm16le == pcm)
    }

    @Test func stereoReturnsNil() {
        let pcm = Data(count: 3200)
        var wav = Wav.encode(pcm16le: pcm, sampleRate: 16000)
        // In standard 44-byte WAV, numChannels is at byte offset 22 (16-bit LE)
        wav[22] = 2 // stereo
        wav[23] = 0
        #expect(Wav.decode(wav) == nil)
    }

    @Test func truncatedOrInvalidWavReturnsNil() {
        #expect(Wav.decode(Data(count: 10)) == nil)
        #expect(Wav.decode(Data("NOT_A_WAV_FILE_HEADER_AT_ALL_EXTRA_BYTES_12345678".utf8)) == nil)
    }
}
