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
        endedReason: CallSteps.EndReason? = nil
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
    }
}

/// The chat operations a call session needs (brief §4.9).
@MainActor
public protocol CallChat: AnyObject {
    func sendFromCall(text: String, callId: String) -> Bool
    func followFromCall(turnId: String)
    var busy: Bool { get }
}

extension ChatModel: CallChat {}

/// The live state and coordinator of one in-app call (brief §4.9, §6.3).
@MainActor @Observable
public final class CallSession {
    public let callId: String
    public let topic: String
    public let fromAgent: Bool

    public var ui: CallUi
    public let trail = LevelTrail()

    @ObservationIgnored private let chat: any CallChat
    @ObservationIgnored private let api: @Sendable () -> KeloApi?
    @ObservationIgnored private let audio: CallAudio
    @ObservationIgnored private let stt: VoiceModel
    @ObservationIgnored private let speech: SpeechModel
    @ObservationIgnored private let phoneListener: (any PhoneListener)?
    @ObservationIgnored private let device: @Sendable () -> (any SpeechEngine)?
    @ObservationIgnored private let appLang: @Sendable () -> String
    @ObservationIgnored private let clock: @Sendable () -> Int64
    @ObservationIgnored private let log: @Sendable (String) -> Void

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

    @ObservationIgnored private var clockTask: Task<Void, Never>?
    @ObservationIgnored private var ringTask: Task<Void, Never>?
    @ObservationIgnored private var quietTask: Task<Void, Never>?
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
        device: @escaping @Sendable () -> (any SpeechEngine)?,
        appLang: @escaping @Sendable () -> String,
        clock: @escaping @Sendable () -> Int64 = { Int64(Date().timeIntervalSince1970 * 1000) },
        log: @escaping @Sendable (String) -> Void = { _ in }
    ) {
        self.callId = callId
        self.topic = topic
        self.fromAgent = fromAgent
        self.chat = chat
        self.api = api
        self.audio = audio
        self.stt = stt
        self.speech = speech
        self.phoneListener = phoneListener
        self.device = device
        self.appLang = appLang
        self.clock = clock
        self.log = log

        self.ui = CallUi(
            phase: .ringing,
            speakerOn: audio.speakerOn,
            ears: CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil)
        )

        let speechModel = self.speech
        self.queue = SpeechQueue(
            audio: audio,
            server: { [weak self] text, lang in
                guard let self else { return nil }
                let (client, now) = await MainActor.run { (self.api(), self.clock()) }
                await MainActor.run { self.timings.mark(.firstAudio, atMs: now) }
                guard let client else { return nil }
                return try? await VoiceAPI.speak(client, text: text, lang: lang)
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
            }
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

        audio.chime(on: true)
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

    private func handleQueueEvent(_ ev: SpeechQueue.Event) {
        switch ev {
        case .firstAudio:
            timings.mark(.firstAudio, atMs: clock())
        case .firstPlay:
            timings.mark(.playStart, atMs: clock())
            log(timings.line())
            step(.firstSentence)
            resetQuietTimer()
        case .idle:
            lastPlaybackStopMs = clock()
            step(.queueIdle(replyBusy: chat.busy))
            resetQuietTimer()
        case .deviceSpoke:
            deviceSpokeThisCall = true
            ui.voiceLine = CallVoiceLine.show(voice: speech.view, deviceSpokeThisCall: true, dismissed: dismissedVoiceLine)
        }
    }

    public func accept() {
        ringTask?.cancel()
        audio.chime(on: false)
        step(.accept)
        checkReadiness()
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
        switch ev {
        case .text(let text):
            timings.mark(.firstText, atMs: clock())
            let pieces = cutter.add(text)
            for p in pieces {
                timings.mark(.firstSentence, atMs: clock())
                queue.add(p)
            }
        case .done, .error:
            let rest = cutter.finish()
            for p in rest { queue.add(p) }
        case .paymentStep, .pinNeeded:
            ui.cardWaiting = true
            queue.add("화면에서 확인할 게 있어요.")
        case .unknown(let kind):
            if kind.contains("secret") || kind.contains("shipping") || kind.contains("location") || kind.contains("card") {
                ui.cardWaiting = true
                queue.add("화면에서 확인할 게 있어요.")
            }
        default:
            break
        }
    }

    public func onChatBusyChanged(_ busy: Bool) {
        if !busy && (state.phase == .speaking || state.phase == .waiting) {
            step(.replyEnded)
        }
    }

    private func checkReadiness() {
        let need = CallReadiness.next(
            stt: stt.view,
            voice: speech.view,
            micAllowed: micAllowed,
            phoneRecognizer: phoneListener != nil,
            askedStt: askedStt,
            askedVoice: askedVoice
        )
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

        if next.phase == .connecting && connectStartMs == 0 {
            connectStartMs = clock()
            startClockTask()
        }

        for a in actions {
            switch a {
            case .openMic:
                _ = audio.startMic { [weak self] chunk in
                    Task { @MainActor [weak self] in
                        self?.onMicChunk(chunk)
                    }
                }
            case .closeMic:
                audio.stopMic()
            case .sendAnswered:
                sendAnswered()
            case .transcribe:
                transcribe()
            case .sendText:
                let textToSend: String?
                switch ev {
                case .transcribed(let text): textToSend = text
                case .replyEnded: textToSend = previousState.held
                default: textToSend = next.held ?? previousState.held
                }
                if let textToSend, !textToSend.isEmpty {
                    sendText(textToSend)
                }
            case .holdText:
                break
            case .stopPlayback:
                queue.stop()
                audio.stopPlayback()
            case .end:
                endCall()
            case .none:
                break
            }
        }

        if next.phase == .listening {
            startListening()
        }
    }

    private func startListening() {
        resetQuietTimer()
        let ears = CallEars.pick(stt.view, phoneRecognizer: phoneListener != nil)
        ui.ears = ears
        if ears == .phone, let phoneListener {
            phoneListener.listenOnce(
                language: appLang(),
                onStarted: { [weak self] in
                    Task { @MainActor [weak self] in self?.step(.speechStarted) }
                },
                onLevel: { [weak self] lvl in
                    Task { @MainActor [weak self] in
                        self?.trail.push(lvl)
                    }
                },
                onDone: { [weak self] text in
                    Task { @MainActor [weak self] in self?.step(.transcribed(text ?? "")) }
                }
            )
        }
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

    private func sendAnswered() {
        Task { [weak self] in
            guard let self else { return }
            guard let client = self.api() else { return }
            do {
                let id = try await VoiceAPI.answered(client, callId: self.callId, timezone: TimeZone.current.identifier, topic: self.topic.isEmpty ? nil : self.topic)
                await MainActor.run {
                    if !id.isEmpty {
                        self.chat.followFromCall(turnId: id)
                        self.step(.turnStarted)
                    } else {
                        self.step(.turnStarted)
                        self.step(.replyEnded)
                    }
                }
            } catch {
                await MainActor.run {
                    self.step(.turnStarted)
                    self.step(.replyEnded)
                }
            }
        }
    }

    private func transcribe() {
        let pcm = lastUtterancePcm
        Task { [weak self] in
            guard let self else { return }
            guard let client = self.api() else { return }
            do {
                let text = try await VoiceAPI.transcribe(client, wav: Wav.encode(pcm16le: pcm, sampleRate: 16_000))
                await MainActor.run {
                    self.ui.offline = false
                    self.timings.mark(.sttDone, atMs: self.clock())
                    self.step(.transcribed(text))
                }
            } catch {
                await MainActor.run {
                    self.ui.offline = true
                    self.step(.transcribeFailed(offline: true))
                }
            }
        }
    }

    private func sendText(_ text: String) {
        timings.mark(.turnStarted, atMs: clock())
        _ = chat.sendFromCall(text: text, callId: callId)
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
        phoneListener?.cancel()
        audio.chime(on: false)
        audio.release()
        queue.stop()
    }
}
