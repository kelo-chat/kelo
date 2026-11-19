import Foundation
import KeloCore
import KeloShared

public struct CallUi: Equatable, Sendable {
    public var phase: CallSteps.Phase = .ringing
    public var elapsedMs: Int64 = 0
    public var muted: Bool = false
    public var speakerOn: Bool = true
    public var need: CallReadiness.Need = .nothing
    public var cardWaiting: Bool = false
    public var offline: Bool = false
    public var ears: CallEars.Way = .none
    public var voiceLine: Bool = false
    public var voiceDownloadPercent: Int? = nil
    public var endedReason: CallSteps.EndReason? = nil
    /// The phone's recognizer failed CallRules.phoneErrorsNote times in a row (the screen says so).
    public var earsTrouble: Bool = false
    /// Kelo's voice could not be played as planned (the screen says so).
    public var voiceTrouble: CallVoiceTrouble? = nil

    public init(
        phase: CallSteps.Phase = .ringing,
        elapsedMs: Int64 = 0,
        muted: Bool = false,
        speakerOn: Bool = true,
        need: CallReadiness.Need = .nothing,
        cardWaiting: Bool = false,
        offline: Bool = false,
        ears: CallEars.Way = .none,
        voiceLine: Bool = false,
        voiceDownloadPercent: Int? = nil,
        endedReason: CallSteps.EndReason? = nil,
        earsTrouble: Bool = false,
        voiceTrouble: CallVoiceTrouble? = nil
    ) {
        self.phase = phase
        self.elapsedMs = elapsedMs
        self.muted = muted
        self.speakerOn = speakerOn
        self.need = need
        self.cardWaiting = cardWaiting
        self.offline = offline
        self.ears = ears
        self.voiceLine = voiceLine
        self.voiceDownloadPercent = voiceDownloadPercent
        self.endedReason = endedReason
        self.earsTrouble = earsTrouble
        self.voiceTrouble = voiceTrouble
    }
}

/// One line of the call on the call screen: the owner's words (mine) or Kelo's reply. The same words go to the chat as messages with
/// via=call; this list only shows them for the call's lifetime and is never stored (2026-11-19, owner).
public struct CallLine: Identifiable, Equatable, Sendable {
    public let id: Int
    public let mine: Bool
    public var text: String

    public init(id: Int, mine: Bool, text: String) {
        self.id = id
        self.mine = mine
        self.text = text
    }
}

/// The chat operations a call session needs (brief §4.9).
@MainActor
public protocol CallChat: AnyObject {
    /// false: the chat cannot take the words now (a reply runs, or a question is open); the call holds them.
    /// `amend`: the words replace the call's unanswered words (sent again with more).
    func sendFromCall(text: String, callId: String, amend: Bool) -> Bool
    /// Stops this call's running turn when nothing of its answer has come yet and drops the owner's bubble of it; false otherwise.
    func takeBackFromCall(callId: String) -> Bool
    /// false: the reply could not be followed (the chat is busy).
    func followFromCall(turnId: String, callId: String) -> Bool
    /// The chat cannot take a call's words now.
    var callBusy: Bool { get }
}

extension CallChat {
    func sendFromCall(text: String, callId: String) -> Bool { sendFromCall(text: text, callId: callId, amend: false) }
}

extension ChatModel: CallChat {}

/// The live state and coordinator of one in-app call (brief §4.9, §6.3; fixes of brief call-silent, 2026-11-19).
@MainActor @Observable
public final class CallSession {
    public let callId: String
    public let topic: String
    public let fromAgent: Bool

    public var ui: CallUi
    public let trail = LevelTrail()
    /// This call's lines (the owner's words and Kelo's replies), oldest first.
    public private(set) var lines: [CallLine] = []

    @ObservationIgnored private let chat: any CallChat
    @ObservationIgnored private let api: @Sendable () -> KeloApi?
    @ObservationIgnored private let audio: CallAudio
    @ObservationIgnored private let stt: VoiceModel
    @ObservationIgnored private let speech: SpeechModel
    @ObservationIgnored private let phoneListener: (any PhoneListener)?
    /// The call's one device voice (released when the call ends).
    @ObservationIgnored private let device: (any SpeechEngine)?
    @ObservationIgnored private let appLang: @Sendable () -> String
    @ObservationIgnored private let clock: @Sendable () -> Int64
    @ObservationIgnored private let log: @Sendable (String) -> Void
    @ObservationIgnored private let phoneRetry: @Sendable (Int) -> Int64

    @ObservationIgnored private var state = CallSteps.State(phase: .ringing)
    @ObservationIgnored private let micLevelMeter = LevelMeter(sampleRate: 16_000)
    @ObservationIgnored private let speechGate = SpeechGate()
    @ObservationIgnored private let cutter = SentenceCutter()
    @ObservationIgnored private let timings = CallTimings()

    @ObservationIgnored private var micAllowed = true
    @ObservationIgnored private var askedStt = false
    @ObservationIgnored private var askedVoice = false
    @ObservationIgnored private var deviceSpokeThisCall = false
    @ObservationIgnored private var dismissedVoiceLine = false

    @ObservationIgnored private var connectStartMs: Int64 = 0
    @ObservationIgnored private var lastUtterancePcm = Data()
    @ObservationIgnored private var lastPlaybackStopMs: Int64 = 0
    @ObservationIgnored private var micOpen = false          // the recorder runs (server ears only)
    @ObservationIgnored private var phoneMisses = 0          // phone recognizer tries in a row that heard nothing (back-off)
    @ObservationIgnored private var phoneErrors = 0          // of those, the ones that failed
    @ObservationIgnored private var lastEars: CallEars.Way?
    @ObservationIgnored private var resending = false
    /// The chain of transcriptions: each piece of the owner's speech is turned into text after the one before (owner 2026-11-20).
    @ObservationIgnored private var transcribeChain: Task<Void, Never>?
    @ObservationIgnored private var nextLineId = 0
    @ObservationIgnored private var keloOpen = false

    @ObservationIgnored private var clockTask: Task<Void, Never>?
    @ObservationIgnored private var ringTask: Task<Void, Never>?
    @ObservationIgnored private var quietTask: Task<Void, Never>?
    @ObservationIgnored private var modelsTask: Task<Void, Never>?
    @ObservationIgnored private var phoneTask: Task<Void, Never>?
    @ObservationIgnored private var queue: SpeechQueue!

    public init(
        callId: String,
        topic: String,
        fromAgent: Bool,
        chat: any CallChat,
        api: @escaping @Sendable () -> KeloApi?,
        audio: CallAudio,
        stt: VoiceModel,
        speech: SpeechModel,
        phoneListener: (any PhoneListener)?,
        device: (any SpeechEngine)?,
        appLang: @escaping @Sendable () -> String,
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        log: @escaping @Sendable (String) -> Void = { _ in },
        phoneRetry: @escaping @Sendable (Int) -> Int64 = { CallRules.phoneRetryWait(misses: $0) }
    ) {
        self.callId = callId
        self.topic = topic
        self.fromAgent = fromAgent
        self.chat = chat
        self.api = api
        self.audio = audio
        self.micAllowed = audio.micGranted
        self.stt = stt
        self.speech = speech
        self.phoneListener = phoneListener
        self.device = device
        self.appLang = appLang
        self.clock = clock
        self.log = log
        self.phoneRetry = phoneRetry

        self.ui = CallUi(
            phase: .ringing,
            speakerOn: audio.speakerOn,
            ears: CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil)
        )

        let logLine = log
        let speechModel = self.speech
        let apiClient = api
        let timingsRef = self.timings
        let clockRef = clock
        self.queue = SpeechQueue(
            audio: audio,
            server: { text, lang in
                let client = apiClient()
                timingsRef.mark(.firstAudio, atMs: clockRef())
                guard let client else { return nil }
                do {
                    return try await VoiceAPI.speak(client, text: text, lang: lang)
                } catch {
                    logLine("speak failed: \(CallLog.why(error))")
                    return nil
                }
            },
            device: device,
            serverLang: { lang in
                await MainActor.run {
                    SpeechLang.server(lang, voice: speechModel.view)
                }
            },
            appLang: appLang,
            onLevel: { [weak self] lvl in
                Task { @MainActor [weak self] in
                    self?.trail.push(lvl)
                }
            },
            onEvent: { [weak self] ev in
                Task { @MainActor [weak self] in
                    self?.handleQueueEvent(ev)
                }
            },
            log: logLine
        )

        audio.onInterrupted = { [weak self] in
            if Thread.isMainThread {
                MainActor.assumeIsolated {
                    self?.step(.interrupted)
                }
            } else {
                Task { @MainActor [weak self] in
                    self?.step(.interrupted)
                }
            }
        }

        log("ring call=\(callId.prefix(8)) from_agent=\(fromAgent)")
        audio.chime(on: true)

        // The server's voice models are read for every call (2026-11-19: the call's own model views were never read).
        let sttModel = stt
        let readFrom = clock()
        modelsTask = Task { @MainActor in
            await sttModel.load()
            await speechModel.load()
            let ms = clock() - readFrom
            logLine("models stt=\(Self.sttLine(sttModel.view)) speech=\(Self.speechLine(speechModel.view)) ms=\(ms)")
        }

        ringTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(CallRules.ringMs) * 1_000_000)
            guard !Task.isCancelled else { return }
            await MainActor.run {
                if self?.state.phase == .ringing {
                    self?.audio.chime(on: false)
                    self?.step(.ringTimeout)
                }
            }
        }
    }

    private static func sttLine(_ view: VoiceView?) -> String {
        view.map { "\($0.state)/\($0.runtime)" } ?? "none"
    }

    private static func speechLine(_ view: SpeechView?) -> String {
        view.map { "\($0.state)/\($0.runtime)/langs=\($0.languages.count)" } ?? "none"
    }

    private func handleQueueEvent(_ ev: SpeechQueue.Event) {
        switch ev {
        case .firstAudio:
            timings.mark(.firstAudio, atMs: clock())
        case .firstPlay:
            timings.mark(.playStart, atMs: clock())
            log("first piece playing \(timings.line())")
            step(.firstSentence)
            resetQuietTimer()
        case .idle:
            lastPlaybackStopMs = clock()
            step(.queueIdle(replyBusy: chat.callBusy))
            resetQuietTimer()
        case .deviceSpoke:
            deviceSpokeThisCall = true
            ui.voiceLine = CallVoiceLine.show(voice: speech.view, deviceSpokeThisCall: true, dismissed: dismissedVoiceLine)
        case .voiceFallback:
            if ui.voiceTrouble == nil { ui.voiceTrouble = .deviceVoice }
        case .silent:
            ui.voiceTrouble = .noVoice
        }
    }

    public func accept() {
        ringTask?.cancel()
        audio.chime(on: false)
        step(.accept)
        // the call starts without the microphone: it asks now, and a refusal ends the call with its reason (2026-11-19)
        if !audio.micGranted {
            audio.requestMic { [weak self] granted in
                Task { @MainActor [weak self] in self?.micPermission(granted: granted) }
            }
        }
        // the first call after start: wait (at most modelsWaitMs) for the server's models before choosing ears and voice
        if modelsTask == nil || (stt.view != nil && speech.view != nil) {
            checkReadiness()
        } else {
            let loading = modelsTask
            Task { @MainActor [weak self] in
                await withTaskGroup(of: Void.self) { group in
                    group.addTask { await loading?.value }
                    group.addTask { try? await Task.sleep(nanoseconds: UInt64(CallRules.modelsWaitMs) * 1_000_000) }
                    _ = await group.next()
                    group.cancelAll()
                }
                self?.checkReadiness()
            }
        }
    }

    public func decline() {
        ringTask?.cancel()
        audio.chime(on: false)
        step(.decline)
    }

    public func hangUp() {
        ringTask?.cancel()
        audio.chime(on: false)
        step(.hangUp)
    }

    public func setMuted(_ on: Bool) {
        ui.muted = on
    }

    public func setSpeaker(_ on: Bool) {
        audio.speakerOn = on
        ui.speakerOn = on
    }

    public func micPermission(granted: Bool) {
        if !granted {
            step(.notPossible(.micDenied))
        } else {
            micAllowed = true
            checkReadiness()
        }
    }

    public func answerSttQuestion(ok: Bool) {
        askedStt = true
        if ok {
            stt.choose("sensevoice-small")
        } else {
            stt.decline()
        }
        checkReadiness()
    }

    public func answerVoiceQuestion(ok: Bool) {
        askedVoice = true
        if ok {
            speech.choose()
        } else {
            speech.decline()
        }
        checkReadiness()
    }

    public func getBetterVoice() {
        speech.choose()
        ui.voiceLine = false
    }

    public func dismissVoiceLine() {
        dismissedVoiceLine = true
        ui.voiceLine = false
    }

    public func appBackground() {
        step(.background)
    }

    public func stopVoice() {
        queue.stop()
        audio.stopPlayback()
    }

    public func onTurnEvent(_ ev: TurnEvent) {
        if CallRules.isAnswer(ev) { step(.answered) }
        switch ev {
        case .text(let text):
            addKeloText(text)
            timings.mark(.firstText, atMs: clock())
            let pieces = cutter.add(text)
            for p in pieces {
                timings.mark(.firstSentence, atMs: clock())
                queue.add(p)
            }
        case .done, .error:
            closeKeloLine()
            let rest = cutter.finish()
            for p in rest { queue.add(p) }
        case .paymentStep, .pinNeeded:
            ui.cardWaiting = true
            queue.add(L(R.string.call_card_waiting))
        case .unknown(let kind):
            if kind.contains("secret") || kind.contains("shipping") || kind.contains("location") || kind.contains("card") {
                ui.cardWaiting = true
                queue.add(L(R.string.call_card_waiting))
            }
        default:
            break
        }
    }

    /// A secret or consent question opened during the call's turn: the card line once, then back to listening (2026-11-19).
    public func questionOpened() {
        ui.cardWaiting = true
        queue.add(L(R.string.call_card_waiting))
        step(.questionOpened)
    }

    public func onChatBusyChanged(_ busy: Bool) {
        if !busy && (state.phase == .speaking || state.phase == .waiting || state.phase == .listening || state.phase == .hearing || state.phase == .transcribing) {
            step(.replyEnded)
        }
    }

    private func checkReadiness() {
        guard state.phase == .preparing else { return }
        let need = CallReadiness.next(
            stt: stt.view,
            voice: speech.view,
            micAllowed: micAllowed,
            phoneRecognizer: phoneListener != nil,
            askedStt: askedStt,
            askedVoice: askedVoice
        )
        log("need=\(need) stt=\(Self.sttLine(stt.view)) speech=\(Self.speechLine(speech.view))")
        ui.need = need
        switch need {
        case .micPermission: break
        case .sttQuestion: break
        case .voiceQuestion: break
        case .impossible: step(.notPossible(.noStt))
        case .nothing: step(.prepared)
        }
    }

    private func step(_ ev: CallSteps.Event) {
        let bargeIn = audio.echoCancel && CallRules.bargeInPhones && phoneListener == nil
        let previousState = state
        let (next, actions) = CallSteps.step(state, ev, bargeIn: bargeIn)
        state = next

        ui.phase = next.phase
        ui.endedReason = next.ended
        ui.ears = CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil)
        if next.phase != previousState.phase {
            log("phase \(previousState.phase)->\(next.phase)")
            if next.phase == .ended { log("end reason=\(next.ended.map { "\($0)" } ?? "none")") }
        }

        if next.phase == .connecting && connectStartMs == 0 {
            connectStartMs = clock()
            startClockTask()
        }

        for a in actions {
            switch a {
            case .openMic:
                openEars()
            case .closeMic:
                audio.stopMic()
                micOpen = false
            case .sendAnswered:
                sendAnswered()
            case .transcribe:
                if case .speechEnded(let pcm) = ev { transcribe(pcm: pcm) } else { transcribe(pcm: lastUtterancePcm) }
            case .sendText:
                if let out = next.sent { sendText(out) }
            case .holdText:
                log("words held until the reply ends")
                if !chat.callBusy && !resending {   // the chat is already free: the held words go now
                    resending = true
                    step(.replyEnded)
                    resending = false
                }
            case .takeBack:
                if chat.takeBackFromCall(callId: callId) {
                    log("unanswered words taken back")
                } else {
                    log("take back refused")
                    step(.takeBackRefused)
                }
            case .stopPlayback:
                queue.stop()
                audio.stopPlayback()
            case .end:
                endCall()
            case .none:
                break
            }
        }

        // The phone's recognizer starts on ENTERING listening only (2026-11-19: it restarted after every step: a "ding" loop).
        if next.phase == .listening && previousState.phase != .listening {
            startListening()
        }
        if next.phase != .listening && next.phase != .hearing {
            phoneTask?.cancel()
            phoneTask = nil
        }
    }

    private func startListening() {
        resetQuietTimer()
        let ears = CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil)
        ui.ears = ears
        if ears != lastEars {
            log("ears=\(ears)")
            lastEars = ears
        }
        openEars()
        if ears == .phone { listenByPhone() }
    }

    /// The recorder runs only for the server's ears: the phone's recognizer needs the microphone to itself (C9, 2026-11-19).
    private func openEars() {
        let ears = CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil)
        if ears == .server && !micOpen {
            micOpen = audio.startMic { chunk in
                Task { @MainActor [weak self] in
                    self?.onMicChunk(chunk)
                }
            }
            if !micOpen { log("mic failed to open") }
        } else if ears != .server && micOpen {
            audio.stopMic()
            micOpen = false
        }
    }

    /// Starts the phone's recognizer now, or after the back-off when the last tries heard nothing (CallRules.phoneRetryWait).
    private func listenByPhone() {
        guard let listener = phoneListener else { return }
        phoneTask?.cancel()
        let wait = phoneRetry(phoneMisses)
        phoneTask = Task { @MainActor [weak self] in
            if wait > 0 {
                try? await Task.sleep(nanoseconds: UInt64(wait) * 1_000_000)
            }
            guard !Task.isCancelled, let self, self.state.phase == .listening else { return }
            listener.listenOnce(
                language: self.appLang(),
                onStarted: {
                    Task { @MainActor [weak self] in self?.step(.speechStarted) }
                },
                onLevel: { lvl in
                    Task { @MainActor [weak self] in self?.trail.push(lvl) }
                },
                onDone: { text in
                    Task { @MainActor [weak self] in self?.onPhoneHeard(text) }
                }
            )
        }
    }

    private func onPhoneHeard(_ text: String?) {
        guard state.phase == .listening || state.phase == .hearing else { return }
        let words = text?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if words.isEmpty || ui.muted {
            phoneMisses += 1
            if text == nil { phoneErrors += 1 }
            if phoneErrors >= CallRules.phoneErrorsNote { ui.earsTrouble = true }
            let why = text == nil ? "error" : "nothing heard"
            log("phone ears: \(why) misses=\(phoneMisses) next_in_ms=\(CallRules.phoneRetryWait(misses: phoneMisses))")
            // from HEARING back to listening is an entry (startListening applies the back-off); still listening: listen again here
            if state.phase == .hearing {
                step(.transcribed(""))
            } else {
                listenByPhone()
            }
            return
        }
        phoneMisses = 0
        phoneErrors = 0
        ui.earsTrouble = false
        timings.mark(.sttDone, atMs: clock())
        log("phone ears: heard=true")
        addMineLine(words, join: state.held != nil || state.again != nil)
        step(.transcribed(words))
    }

    private func sendAnswered() {
        Task { @MainActor [weak self] in
            guard let self else { return }
            guard let client = self.api() else {
                self.log("answered: no server")
                self.step(.turnStarted)
                self.step(.replyEnded)
                return
            }
            do {
                let id = try await VoiceAPI.answered(client, callId: self.callId, timezone: TimeZone.current.identifier, topic: self.topic.isEmpty ? nil : self.topic)
                if !id.isEmpty, self.chat.followFromCall(turnId: id, callId: self.callId) {
                    self.log("answered: following the greeting")
                    self.step(.turnStarted)
                } else {
                    self.log(id.isEmpty ? "answered: no greeting" : "answered: chat busy, greeting not followed")
                    self.step(.turnStarted)
                    self.step(.replyEnded)
                }
            } catch {
                self.log("answered failed: \(CallLog.why(error))")
                self.step(.turnStarted)
                self.step(.replyEnded)
            }
        }
    }

    /// Turns one piece of the owner's speech into text. Pieces run one at a time, in the order spoken (owner 2026-11-20).
    private func transcribe(pcm: Data) {
        let previous = transcribeChain
        transcribeChain = Task { [weak self] in
            await previous?.value
            guard let self else { return }
            guard let client = self.api() else {
                await MainActor.run {
                    self.log("transcribe failed: no server")
                    self.ui.offline = true
                    self.step(.transcribeFailed(offline: true))
                }
                return
            }
            do {
                let text = try await VoiceAPI.transcribe(client, wav: Wav.encode(pcm16le: pcm, sampleRate: 16_000))
                await MainActor.run {
                    self.ui.offline = false
                    self.timings.mark(.sttDone, atMs: self.clock())
                    self.log("server ears: heard=\(!text.isEmpty)")
                    let heard = text.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !heard.isEmpty { self.addMineLine(heard, join: self.state.held != nil || self.state.again != nil) }
                    self.step(.transcribed(text))
                }
            } catch {
                await MainActor.run {
                    self.log("transcribe failed: \(CallLog.why(error))")
                    self.ui.offline = true
                    self.step(.transcribeFailed(offline: true))
                }
            }
        }
    }

    /// The owner's line; `join`: the words go on the owner's last line (the same turn, heard before Kelo answered).
    func addMineLine(_ text: String, join: Bool = false) {
        keloOpen = false
        if join, let last = lines.last, last.mine {
            lines[lines.count - 1].text += " " + text
            return
        }
        lines.append(CallLine(id: nextLineId, mine: true, text: text))
        nextLineId += 1
    }

    /// Kelo's reply text as it comes: appended to the open line of Kelo, or a new line.
    func addKeloText(_ delta: String) {
        guard !delta.isEmpty else { return }
        if keloOpen, let last = lines.last, !last.mine {
            lines[lines.count - 1].text += delta
        } else {
            lines.append(CallLine(id: nextLineId, mine: false, text: delta))
            nextLineId += 1
        }
        keloOpen = true
    }

    func closeKeloLine() {
        keloOpen = false
    }

    /// Words typed into the call: shown as the owner's line and sent as a call turn (answered by voice).
    public func sendTyped(_ text: String) {
        let words = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty, state.phase != .ended, state.phase != .ringing else { return }
        log("typed words")
        addMineLine(words, join: state.held != nil || state.again != nil)
        if CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil) == .phone {
            phoneListener?.cancel()
        }
        step(.typed(words))
    }

    private func sendText(_ out: CallSteps.Outgoing) {
        timings.mark(.turnStarted, atMs: clock())
        if chat.sendFromCall(text: out.text, callId: callId, amend: out.amend) {
            log("turn sent")
            return
        }
        // the chat is busy (a typed reply, an open question): keep the words and send them when it is free (2026-11-19)
        log("turn refused: chat busy, words held")
        step(.sendRefused(out.text, amend: out.amend))
        if !chat.callBusy && !resending {
            resending = true
            step(.replyEnded)
            resending = false
        }
    }

    private func startClockTask() {
        clockTask?.cancel()
        clockTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self, self.state.phase != .ended else { break }
                let elapsed = max(0, self.clock() - self.connectStartMs)
                self.ui.elapsedMs = elapsed
            }
        }
    }

    private func resetQuietTimer() {
        quietTask?.cancel()
        quietTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(CallRules.quietEndMs) * 1_000_000)
            guard !Task.isCancelled else { return }
            await MainActor.run {
                if self?.state.phase != .ended {
                    self?.step(.quietTimeout)
                }
            }
        }
    }

    public func release() {
        endCall()
    }

    private func endCall() {
        ringTask?.cancel()
        clockTask?.cancel()
        quietTask?.cancel()
        modelsTask?.cancel()
        phoneTask?.cancel()
        phoneTask = nil
        phoneListener?.cancel()
        audio.chime(on: false)
        audio.release()
        micOpen = false
        queue.stop()
        device?.shutDown()
    }

    private func onMicChunk(_ chunk: Data) {
        guard !ui.muted else { return }
        let now = clock()
        if now - lastPlaybackStopMs < CallRules.echoTailMs { return }

        let levels = micLevelMeter.add(chunk)
        for lvl in levels { trail.push(lvl) }

        speechGate.speaking = state.phase == .speaking
        let gateEvents = speechGate.feed(chunk)
        for gev in gateEvents {
            switch gev {
            case .started:
                timings.mark(.speechEnd, atMs: clock())
                resetQuietTimer()
                step(.speechStarted)
            case .ended(let pcm):
                lastUtterancePcm = pcm
                step(.speechEnded(pcm))
            case .dropped:
                step(.speechDropped)
            }
        }
    }
}
