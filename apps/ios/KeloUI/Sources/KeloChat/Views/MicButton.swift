import SwiftUI
import KeloShared
#if os(iOS)
import Speech
import AVFoundation
#endif

#if os(iOS)
/// The mic, left of 보내기 (Android voice/MicButton.kt): spoken words go into the field, from the Kelo server's speech model or from the
/// phone's own recognition (on the device when it can). While it listens the composer is the recording bar (ComposerVoice.swift), so the
/// mic shows only when nothing records, and while a finger holds it (the hold keeps the composer). Needs NSMicrophoneUsageDescription
/// and NSSpeechRecognitionUsageDescription in the app's Info.plist. On a Mac build it is not shown.
struct MicButton: View {
    let voice: ComposerVoiceModel

    var body: some View {
        VoiceMicSwitch(voice: voice)
    }
}

/// Picks the way from the server's voice view (DictationWay, the same table as Android): the Kelo server's speech model, or the
/// phone's own recognizer. The first tap while the model is only offered asks 확인 / 취소 once (the tap goes on with the phone's
/// recognizer; 취소 keeps it and the offer stays in My → 음성 입력).
private struct VoiceMicSwitch: View {
    let voice: ComposerVoiceModel
    @State private var asking = false

    var body: some View {
        let way = DictationWay.pick(voice.view, phoneRecognizer: true)
        Group {
            if let ring = VoiceDownloadRing.of(voice.view) {
                // the speech model downloads: the mic is a circle with the progress (a spinner when the server gives no number)
                VoiceDownloadRingView(percent: ring.percent, label: L(R.string.voice_server_downloading, voice.view?.progress.map { Int(($0.done * 100) / max($0.total, 1)) } ?? 0))
            } else if way == .server {
                ServerMicButton(voice: voice)
            } else {
                IOSMicButton(voice: voice)
                    .simultaneousGesture(TapGesture().onEnded {
                        if way == .offerThenPhone { asking = true }
                    })
            }
        }
        .task { await voice.refresh() }
        .alert(L(R.string.voice_denied_title), isPresented: Binding(get: { voice.phoneDenied || voice.server.denied }, set: { if !$0 { voice.phoneDenied = false; voice.server.denied = false } })) {
            Button(L(R.string.open_settings)) { DeviceInfo.openAppSettings() }
            Button(L(R.string.close), role: .cancel) {}
        } message: { Text(L(R.string.voice_denied_body)) }
        .alert(voice.phoneProblem ?? "", isPresented: Binding(get: { voice.phoneProblem != nil }, set: { if !$0 { voice.phoneProblem = nil } })) {
            Button(L(R.string.close), role: .cancel) {}
        }
        .alert(offerTitle, isPresented: $asking) {
            Button(L(R.string.ok)) { voice.answer(choose: true) }
            Button(L(R.string.cancel), role: .cancel) { voice.answer(choose: false) }
        }
    }

    private var offerTitle: String {
        L(R.string.voice_server_offer_body, voice.view?.recommended?.name ?? "")
    }
}

/// The phone's own recognizer as the mic: a tap starts, a hold dictates while the finger is down (HoldToTalk). Its state lives in the
/// composer's voice model, so the bar keeps going when this button leaves the screen.
private struct IOSMicButton: View {
    let voice: ComposerVoiceModel
    @Environment(\.kelo) private var c
    @State private var phase: HoldToTalk.Phase = .idle
    @State private var holdTimer: Task<Void, Never>?

    private static let size: CGFloat = 40

    var body: some View {
        let speech = voice.speech
        ZStack {
            if speech.listening {
                Circle().stroke((phase == .holdingAway ? c.dangerInk : c.accent).opacity(0.35), lineWidth: 3).scaleEffect(1 + speech.level * 0.35)
            }
            KeloIconView(speech.listening ? .stop : .mic, size: 22, tint: phase == .holdingAway ? c.dangerInk : (speech.listening ? c.accent : c.muted))
        }
        .frame(width: Self.size, height: Self.size)
        .contentShape(Rectangle())
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { drag in
                    if phase == .idle && holdTimer == nil {
                        apply(.down(listening: speech.listening))
                        holdTimer = Task {
                            try? await Task.sleep(nanoseconds: UInt64(HoldToTalk.holdMs) * 1_000_000)
                            guard !Task.isCancelled else { return }
                            apply(.holdElapsed)
                        }
                    }
                    let on = HoldToTalk.onButton(x: drag.location.x, y: drag.location.y, width: Self.size, height: Self.size, slack: 24)
                    apply(.move(onButton: on))
                }
                .onEnded { _ in
                    holdTimer?.cancel(); holdTimer = nil
                    apply(.up)
                }
        )
        // VoiceOver's double tap is the tap; its custom action does the same (the hold needs a finger that stays down)
        .accessibilityElement()
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(L(speech.listening ? R.string.voice_stop : R.string.voice_start))
        .accessibilityValue(speech.listening ? L(R.string.voice_listening) : "")
        .accessibilityHint(L(R.string.voice_hold_label))
        .accessibilityAction { tap() }
        .overlay(alignment: .topTrailing) {
            if phase.isHeld {
                Text(L(R.string.voice_hold_hint))
                    .font(KeloFont.small)
                    .foregroundStyle(phase == .holdingAway ? c.dangerInk : c.ink)
                    .padding(.horizontal, 14).padding(.vertical, 10)
                    .background(RoundedRectangle(cornerRadius: 14).fill(phase == .holdingAway ? c.dangerSoft : c.card))
                    .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
                    .fixedSize()
                    .offset(y: -(Self.size + 14))
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
            }
        }
        .onDisappear {
            // the mic left the screen under a held finger: the hold ends and what was said goes into the field
            holdTimer?.cancel(); holdTimer = nil
            if phase.isHeld { voice.held = false; voice.finish(.field); phase = .idle }
        }
    }

    private func apply(_ event: HoldToTalk.Event) {
        let step = HoldToTalk.step(phase, event)
        phase = step.phase
        switch step.action {
        case .tap: tap()
        case .start:
            voice.held = true
            begin()
        case .finish:
            voice.held = false
            voice.finish(.field)
        case .cancel:
            voice.held = false
            voice.discard()
        case .none: break
        }
    }

    private func tap() {
        if voice.speech.listening { voice.finish(.field); return }
        begin()
    }

    private func begin() {
        voice.phoneStart { started in
            // the permission question may have taken the finger away: listening then goes on as after a tap
            if !started { voice.held = false }
            if !started || !phase.isHeld { phase = .idle }
        }
    }
}

/// SFSpeechRecognizer + AVAudioEngine, with partial results; ends by itself after a pause, or with `stop`.
@MainActor @Observable
final class SpeechInput {
    enum Access { case ok, denied, unavailable }

    private(set) var listening = false
    /// 0...1, how loud it hears right now
    private(set) var level: Double = 0
    @ObservationIgnored private var engine: AVAudioEngine?
    @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var task: SFSpeechRecognitionTask?
    @ObservationIgnored private var analyzerBox: AnyObject?          // the SpeechAnalyzer (iOS 26+), kept untyped for older iOS
    @ObservationIgnored private var feed: AnyObject?                 // its AsyncStream<AnalyzerInput>.Continuation, in a box
    @ObservationIgnored private var results: Task<Void, Never>?
    @ObservationIgnored private var silence: Task<Void, Never>?
    /// Words came in this time (an error after words is just the end).
    @ObservationIgnored private var heard = false

    static func authorize() async -> Access {
        let speech = await withCheckedContinuation { c in SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0) } }
        guard speech == .authorized else { return speech == .restricted ? .unavailable : .denied }
        let mic = await AVAudioApplication.requestRecordPermission()
        return mic ? .ok : .denied
    }

    func start(language: String, onWords: @escaping (String) -> Void, onEnd: @escaping (String?) -> Void) {
        guard !listening else { return }
        listening = true                       // the button and the header's character show listening at once
        if #available(iOS 26.0, *) {
            Task {
                if await startModern(language: language, onWords: onWords, onEnd: onEnd) { return }
                guard listening else { onEnd(nil); return }   // tapped off while the newer recognizer was being tried
                listening = false
                startLegacy(language: language, onWords: onWords, onEnd: onEnd)
            }
        } else {
            listening = false
            startLegacy(language: language, onWords: onWords, onEnd: onEnd)
        }
    }

    /// Apple's newer recognizer (iOS 26+). False when it cannot run now (no model for the language yet: its download is asked for in
    /// the background, so the next time uses it), and the older recognizer takes over.
    @available(iOS 26.0, *)
    private func startModern(language: String, onWords: @escaping (String) -> Void, onEnd: @escaping (String?) -> Void) async -> Bool {
        guard SpeechTranscriber.isAvailable,
              let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: language)) else { return false }
        let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [.volatileResults], attributeOptions: [])
        do {
            if let install = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                Task.detached { try? await install.downloadAndInstall() }
                return false
            }
        } catch { return false }
        guard let format = await SpeechAnalyzer.bestAvailableAudioFormat(compatibleWith: [transcriber]) else { return false }
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let (stream, feed) = AsyncStream<AnalyzerInput>.makeStream()
        let newEngine = AVAudioEngine()
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            let input = newEngine.inputNode
            let natural = input.outputFormat(forBus: 0)
            guard let converter = AVAudioConverter(from: natural, to: format) else { throw CancellationError() }
            input.installTap(onBus: 0, bufferSize: 4096, format: natural) { [weak self] buffer, _ in
                if let out = Self.convert(buffer, with: converter, to: format) { feed.yield(AnalyzerInput(buffer: out)) }
                let rms = Self.rms(buffer)
                Task { @MainActor in self?.level = rms }
            }
            newEngine.prepare()
            try newEngine.start()
            try await analyzer.start(inputSequence: stream)
            if !listening {   // the button was tapped again while the recognizer was starting: nothing is kept running
                feed.finish()
                await analyzer.cancelAndFinishNow()
                newEngine.inputNode.removeTap(onBus: 0)
                newEngine.stop()
                try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
                onEnd(nil)
                return true
            }
            self.engine = newEngine
            self.feed = ModernFeed(feed)
            self.analyzerBox = analyzer
            heard = false
            results = Task { [weak self] in
                var done = ""
                do {
                    for try await r in transcriber.results {
                        let piece = String(r.text.characters)
                        let shown = SpeechJoin.join(done, piece)
                        if r.isFinal { done = shown }
                        await MainActor.run {
                            self?.heard = true
                            onWords(shown)
                            self?.restartSilenceTimer()
                        }
                    }
                    await MainActor.run { self?.endModern(); onEnd(nil) }
                } catch {
                    await MainActor.run {
                        let heard = self?.heard ?? false
                        self?.endModern()
                        onEnd(Self.message(for: error, heard: heard))
                    }
                }
            }
            restartSilenceTimer()
            return true
        } catch {
            feed.finish()
            await analyzer.cancelAndFinishNow()
            newEngine.inputNode.removeTap(onBus: 0)
            newEngine.stop()
            return false
        }
    }

    /// Stops the microphone and lets the recognizer finish what it has heard; the results loop then ends and calls onEnd once.
    private func finishModernInput() {
        silence?.cancel()
        engine?.stop()
        engine?.inputNode.removeTap(onBus: 0)
        if #available(iOS 26.0, *) {
            (feed as? ModernFeed)?.finish()
            let analyzer = analyzerBox as? SpeechAnalyzer
            feed = nil
            Task { try? await analyzer?.finalizeAndFinishThroughEndOfInput() }
        }
        feed = nil
    }

    private func endModern() {
        results = nil; analyzerBox = nil; feed = nil; engine = nil
        listening = false
        level = 0
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func startLegacy(language: String, onWords: @escaping (String) -> Void, onEnd: @escaping (String?) -> Void) {
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: language)) ?? SFSpeechRecognizer(), recognizer.isAvailable else {
            onEnd(L(R.string.voice_unavailable)); return
        }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            let engine = AVAudioEngine()
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.addsPunctuation = true
            let input = engine.inputNode
            input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { [weak self] buffer, _ in
                request.append(buffer)
                let rms = Self.rms(buffer)
                Task { @MainActor in self?.level = rms }
            }
            engine.prepare()
            try engine.start()
            self.engine = engine
            self.request = request
            listening = true
            heard = false
            task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor in
                    guard let self else { return }
                    if let result {
                        self.heard = true
                        onWords(result.bestTranscription.formattedString)
                        self.restartSilenceTimer()
                        if result.isFinal { self.stop(); onEnd(nil) }
                    } else if let error, self.listening {
                        let heard = self.heard
                        self.stop()
                        onEnd(Self.message(for: error, heard: heard))
                    }
                }
            }
            restartSilenceTimer()
        } catch {
            stop()
            onEnd(L(R.string.voice_dictation_failed))
        }
    }

    /// The one line the owner is told when the recognizer ends with an error (nil: nothing to say, it was just the end).
    @MainActor static func message(for error: Error, heard: Bool) -> String? {
        let e = error as NSError
        switch SpeechFailure.classify(domain: e.domain, code: e.code, heard: heard) {
        case .none: return nil
        case .noMatch: return L(R.string.voice_no_match)
        case .offline: return L(R.string.voice_dictation_offline)
        case .failed: return L(R.string.voice_dictation_failed)
        }
    }

    /// Ends after a pause in speaking (as Android's recognizer does).
    private func restartSilenceTimer() {
        silence?.cancel()
        silence = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            guard !Task.isCancelled else { return }
            guard let self else { return }
            if self.feed != nil { self.finishModernInput() } else { self.request?.endAudio() }
        }
    }

    func stop() {
        silence?.cancel()
        if feed != nil { finishModernInput() }
        engine?.stop()
        engine?.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        task?.finish()
        engine = nil; request = nil; task = nil
        listening = false
        level = 0
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    nonisolated static func rms(_ buffer: AVAudioPCMBuffer) -> Double {
        guard let ch = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<Int(buffer.frameLength) { sum += ch[i] * ch[i] }
        let rms = sqrt(sum / Float(buffer.frameLength))
        return Double(min(max(rms * 12, 0), 1))
    }

    /// One microphone buffer in the recognizer's format (sample rate / channels differ from the microphone's).
    nonisolated static func convert(_ buffer: AVAudioPCMBuffer, with converter: AVAudioConverter, to format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 16)
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        return error == nil && out.frameLength > 0 ? out : nil
    }
}

/// The analyzer's input continuation, boxed so `SpeechInput` can hold it on iOS 17.
@available(iOS 26.0, *)
private final class ModernFeed {
    let continuation: AsyncStream<AnalyzerInput>.Continuation
    init(_ c: AsyncStream<AnalyzerInput>.Continuation) { continuation = c }
    func finish() { continuation.finish() }
}
#endif
