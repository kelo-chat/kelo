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

    @Test func aQuestionThatStopsTheTurnGoesBackToListeningAndWordsAreHeld() {
        let (waiting, actions) = run(s(.waiting, busy: true), .questionOpened)
        #expect(waiting.phase == .listening)
        #expect(waiting.replyBusy)
        #expect(actions.isEmpty)
        let (heard, held) = run(waiting, .transcribed("hello"))
        #expect(heard.phase == .waiting)
        #expect(held == [.holdText])
    }

    @Test func theQuestionClosingWhileListeningFreesTheCall() {
        let (freed, actions) = run(s(.listening, busy: true), .replyEnded)
        #expect(freed.phase == .listening)
        #expect(!freed.replyBusy)
        #expect(actions.isEmpty)
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

    @Test func textDuringAReplyIsHeldAndJoinedToTheEarlier() {
        let (h1, a1) = run(s(.transcribing, busy: true), .transcribed("first"))
        #expect(a1 == [.holdText])
        #expect(h1.held == "first")
        let (h2, _) = run(CallSteps.State(phase: .transcribing, held: h1.held, replyBusy: true), .transcribed("second"))
        #expect(h2.held == "first second")
    }

    @Test func speechThatGoesOnWhileTheWordsAreTurnedIntoTextJoinsThemIntoOneTurn() {   // owner 2026-11-20
        var st = CallSteps.State(phase: .listening)
        func go(_ e: CallSteps.Event) -> [CallSteps.Action] { let (n, a) = CallSteps.step(st, e, bargeIn: true); st = n; return a }
        _ = go(.speechStarted)
        #expect(go(.speechEnded(Data())) == [.transcribe])
        _ = go(.speechStarted); #expect(st.phase == .hearing)
        #expect(go(.transcribed("나 목소리")).isEmpty); #expect(st.held == "나 목소리")
        #expect(go(.speechEnded(Data())) == [.transcribe])
        #expect(go(.transcribed("너무 끊겨")) == [.sendText])
        #expect(st.sent == CallSteps.Outgoing(text: "나 목소리 너무 끊겨", amend: false))
        #expect(st.unanswered == "나 목소리 너무 끊겨"); #expect(st.held == nil)
    }

    @Test func aCoughAfterTheFirstWordsStillSendsThem() {
        let heard = run(CallSteps.State(phase: .transcribing, inFlight: 1), .speechStarted).0
        let gathered = run(heard, .transcribed("hi")).0
        let (sent, a) = run(gathered, .speechDropped)
        #expect(a == [.sendText]); #expect(sent.sent == CallSteps.Outgoing(text: "hi", amend: false))
    }

    @Test func talkingAgainBeforeKeloAnswersTakesTheWordsBackAndSendsThemAgainWithTheRest() {
        let (taking, a1) = run(CallSteps.State(phase: .waiting, unanswered: "나 목소리"), .speechStarted)
        #expect(a1 == [.takeBack]); #expect(taking.again == "나 목소리"); #expect(taking.unanswered == nil)
        let freed = run(CallSteps.State(phase: taking.phase, held: nil, replyBusy: true, again: "나 목소리"), .replyEnded).0
        #expect(freed.replyBusy == false); #expect(freed.again == "나 목소리")
        let ending = run(freed, .speechEnded(Data())).0
        let (done, a2) = run(ending, .transcribed("너무 끊겨"))
        #expect(a2 == [.sendText]); #expect(done.sent == CallSteps.Outgoing(text: "나 목소리 너무 끊겨", amend: true))
        #expect(run(CallSteps.State(phase: .hearing, again: "나 목소리"), .speechDropped).0.sent == CallSteps.Outgoing(text: "나 목소리", amend: true))
        #expect(run(CallSteps.State(phase: .hearing, again: "x"), .takeBackRefused).0.again == nil)
    }

    @Test func onceKeloAnsweredNewWordsWaitForTheNextTurn() {
        let answered = run(CallSteps.State(phase: .waiting, unanswered: "a"), .answered).0
        #expect(answered.unanswered == nil)
        #expect(run(answered, .speechStarted).1.isEmpty)
    }

    @Test func aReplyThatEndsWhileTheOwnerTalksFreesTheirWords() {   // they were held forever before
        let (freed, a1) = run(CallSteps.State(phase: .hearing, replyBusy: true), .replyEnded)
        #expect(freed.replyBusy == false); #expect(a1.isEmpty)
        let ending = run(freed, .speechEnded(Data())).0
        #expect(run(ending, .transcribed("x")).1 == [.sendText])
    }

    @Test func refusedAmendWordsAreHeldAsAnAmend() {
        let (s1, a1) = run(CallSteps.State(phase: .waiting), .sendRefused("a b", amend: true))
        #expect(a1 == [.holdText]); #expect(s1.again == "a b")
        #expect(run(s1, .replyEnded).0.sent == CallSteps.Outgoing(text: "a b", amend: true))
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

    @Test func sendRefusedHoldsWordsUntilTheChatIsFree() {
        let (s1, a1) = run(s(.waiting, busy: true), .sendRefused("hi"))
        #expect(s1 == CallSteps.State(phase: .waiting, held: "hi", replyBusy: true))
        #expect(a1 == [.holdText])

        let (s2, a2) = run(s1, .replyEnded)
        #expect(s2.phase == .waiting)
        #expect(s2.held == nil)
        #expect(a2 == [.sendText])
    }

    @Test func speakingReplyEndedKeepsSpeakingUntilTheQueueIsIdle() {
        #expect(run(s(.speaking, busy: true), .replyEnded).0.phase == .speaking)
    }

    @Test func phoneRetryWaitsDoubleAndStopAtEight() {
        let waits = [0, 1, 2, 3, 4, 5, 40].map { CallRules.phoneRetryWait(misses: $0) }
        #expect(waits == [0, 1000, 2000, 4000, 8000, 8000, 8000])
    }

    @Test func typedWordsGoNowOrWait() {
        let (s1, a1) = run(s(.listening), .typed("hi"))
        #expect(s1.phase == .waiting)
        #expect(a1 == [.sendText])
        let (s2, a2) = run(s(.speaking), .typed("more"))
        #expect(s2 == CallSteps.State(phase: .speaking, held: "more", replyBusy: true))
        #expect(a2 == [.holdText])
        let (s3, a3) = run(s2, .queueIdle(replyBusy: false))
        #expect(s3.phase == .waiting)
        #expect(s3.held == nil)
        #expect(a3 == [.sendText])
        #expect(run(s(.ringing), .typed("x")).1.isEmpty)
    }

    @Test func soundLinePoints() {
        let flat = [Float](repeating: 0, count: 64)
        let quiet = CallSoundLineRules.points(levels: flat, n: 0, count: 32, timeSec: 0.5, synthetic: false)
        #expect(quiet.allSatisfy { $0 == 0 })
        let loud = [Float](repeating: 1, count: 64)
        let moving = CallSoundLineRules.points(levels: loud, n: 64, count: 32, timeSec: 0.5, synthetic: false)
        #expect(moving.count == 32)
        #expect(moving.contains { abs($0) > 0.3 })
        #expect(moving.allSatisfy { $0 >= -1 && $0 <= 1 })
        let device = CallSoundLineRules.points(levels: flat, n: 0, count: 32, timeSec: 0.5, synthetic: true)
        #expect(device.contains { $0 != 0 })
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

    /// The same text one character at a time.
    private func streamed(_ text: String) -> [String] {
        let c = SentenceCutter()
        var out: [String] = []
        for ch in text { out += c.add(String(ch)) }
        out += c.finish()
        return out
    }

    /// The pieces the cutter gives after each delta, then after the finish.
    private func steps(_ deltas: String...) -> [[String]] {
        let c = SentenceCutter()
        var out: [[String]] = []
        for d in deltas { out.append(c.add(d)) }
        out.append(c.finish())
        return out
    }

    @Test func theFirstPieceGoesOutAtTheFirstClauseBreakOfAShortPhrase() {   // owner 2026-11-20: start speaking sooner
        #expect(steps("좋은 질문이", "에요, 내일 오전 ", "회의는 10시에 시작해요. 끝나는 시간은 ", "정해지지 않았어요.")
            == [[], ["좋은 질문이에요,"], ["내일 오전 회의는 10시에 시작해요."], [], ["끝나는 시간은 정해지지 않았어요."]])
        #expect(all("Good question, the meeting starts at ten.") == ["Good question,", "the meeting starts at ten."])
    }

    @Test func oneWordIsNeverTheFirstPiece() {   // "Well," is one word: the opening runs on to the word cut at 32 characters
        #expect(all("Well, I think the meeting is at ten tomorrow morning in the big room.")
            == ["Well, I think the meeting is at", "ten tomorrow morning in the big room."])
    }

    @Test func withNoBreakSoonTheFirstPieceEndsAtAWordAfterAFewWords() {
        let got = steps("Kelo is a self hosted proactive agent that lives on your own server and ", "keeps working when you are away.")
        #expect(got[0] == ["Kelo is a self hosted proactive"])
        #expect(got[1] + got[2] == ["agent that lives on your own server and keeps working when you are away."])
    }

    @Test func aShortWholeSentenceGoesAtOnceAndTheNextPhraseFollowsQuickly() {
        #expect(all("네. 지금 확인해 볼게요, 잠시만요.") == ["네.", "지금 확인해 볼게요,", "잠시만요."])
    }

    @Test func aLongSentenceLaterInTheReplyIsCutAtAClauseBreak() {
        #expect(all("Here is the plan. First we check the calendar for tomorrow morning, then we book the room on the second floor, and finally we send the invite to everyone.")
            == ["Here is the plan.", "First we check the calendar for tomorrow morning,",
                "then we book the room on the second floor, and finally we send the invite to everyone."])
    }

    @Test func numbersTimesAndAbbreviationsAreNeverCut() {
        #expect(all("It costs 1,000 won in total, which is fine at 10:30.") == ["It costs 1,000 won in total,", "which is fine at 10:30."])
    }

    @Test func japaneseWithoutSpacesWaitsForItsMark() {
        #expect(all("はい、明日の会議は十時からです。") == ["はい、明日の会議は十時からです。"])
    }

    @Test func aWordCutNeverLandsInsideAWordOrALink() {
        let pieces = all("Open the page called [the monthly budget overview](https://d.io/b) and check the totals.")
        #expect(pieces.first == "Open the page called")   // not "…called [the": never inside an open link
        #expect(pieces.allSatisfy { !$0.contains("[") && !$0.contains("](") })
    }

    @Test func theNewRulesGiveTheSamePiecesStreamedOneCharacterAtATime() {
        for text in ["좋은 질문이에요, 내일 오전 회의는 10시에 시작해요. 끝나는 시간은 정해지지 않았어요.",
                     "Kelo is a self hosted proactive agent that lives on your own server and keeps working when you are away.",
                     "Here is the plan. First we check the calendar for tomorrow morning, then we book the room on the second floor, and finally we send the invite to everyone."] {
            #expect(all(text) == streamed(text))
        }
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
        #expect(all("A **bold** [link](https://d.io).") == ["A bold link."])
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

    @Test func aToneOf300MsThenAClearPauseEmitsStartedThenEndedWithPreroll() {
        let gate = SpeechGate()
        var pcm = silence(ms: 400)
        pcm.append(tone(ms: 300))
        pcm.append(silence(ms: 1100))
        let events = gate.feed(pcm)
        #expect(events.count == 2)
        #expect(events[0] == .started)
        guard case .ended(let data) = events[1] else {
            Issue.record("Expected .ended event")
            return
        }
        #expect(data.count >= 600 * 32)
    }

    private func cat(_ parts: Data...) -> Data { parts.reduce(into: Data()) { $0.append($1) } }

    @Test func aPauseOf800MsInsideASentenceDoesNotEndIt() {   // owner 2026-11-20: "나 목소리 너무 끊겨"
        let out = SpeechGate().feed(cat(silence(ms: 400), tone(ms: 600), silence(ms: 800), tone(ms: 700), silence(ms: 1400)))
        #expect(out.count == 2)
        #expect(out.first == .started)
        if out.count == 2, case .ended(let pcm) = out[1] { #expect(pcm.count >= (600 + 800 + 700) * 32) } else { #expect(Bool(false), "the turn ends once") }
    }

    @Test func theTurnEndsAfterAClearPauseOfOneSecond() {
        let gate = SpeechGate()
        #expect(gate.feed(cat(silence(ms: 400), tone(ms: 800), silence(ms: 950))) == [.started])
        let end = gate.feed(silence(ms: 100))
        #expect(end.count == 1)
        if end.count == 1, case .ended = end[0] {} else { #expect(Bool(false), "ends after 1 s of quiet") }
    }

    @Test func afterALongPauseInsideTheTurnTheEndWaitsLonger() {
        let gate = SpeechGate()
        #expect(gate.feed(cat(silence(ms: 400), tone(ms: 600), silence(ms: 900), tone(ms: 600), silence(ms: 1300))) == [.started])
        let end = gate.feed(silence(ms: 120))   // 900 + 500 = 1400 ms
        #expect(end.count == 1)
        if end.count == 1, case .ended = end[0] {} else { #expect(Bool(false), "ends after the longer wait") }
    }

    @Test func theEndNeverWaitsMoreThan1800Ms() {
        let gate = SpeechGate()
        let pcm = cat(silence(ms: 400), tone(ms: 600), silence(ms: 900), tone(ms: 600), silence(ms: 1300), tone(ms: 600), silence(ms: 1700),
                      tone(ms: 600), silence(ms: 1780))
        #expect(gate.feed(pcm) == [.started])
        let end = gate.feed(silence(ms: 20))
        #expect(end.count == 1)
        if end.count == 1, case .ended = end[0] {} else { #expect(Bool(false), "never longer than 1.8 s") }
    }

    @Test func aSoftEndingAfterLoudSpeechIsStillSpeech() {
        let out = SpeechGate().feed(cat(silence(ms: 400), tone(ms: 600, db: -20), tone(ms: 1100, db: -54), silence(ms: 1100)))
        #expect(out.count == 2)
        if out.count == 2, case .ended(let pcm) = out[1] { #expect(pcm.count >= (400 + 1100) * 32) } else { #expect(Bool(false), "one turn") }
    }

    @Test func singleClicksInTheQuietDoNotKeepTheTurnOpen() {
        var pcm = cat(silence(ms: 400), tone(ms: 600))
        for _ in 0..<6 { pcm.append(silence(ms: 180)); pcm.append(tone(ms: 20)) }
        let out = SpeechGate().feed(cat(pcm, silence(ms: 1000)))
        #expect(out.count == 2)
        if out.count == 2, case .ended(let p) = out[1] { #expect(p.count <= (300 + 400 + 1000 + 40) * 32) } else { #expect(Bool(false), "one turn") }
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
        pcm.append(silence(ms: 1100))
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
        pcm.append(silence(ms: 1100))

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
