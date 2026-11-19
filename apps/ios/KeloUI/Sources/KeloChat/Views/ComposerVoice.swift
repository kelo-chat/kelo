import SwiftUI
import KeloShared
import KeloCore
#if os(iOS)

/// One recording at a time, as the owner speaks in the composer (Android: voice/AndroidComposerVoice.kt and ComposerVoice.kt). It holds
/// both ways (the Kelo server's recording and the phone's own recognizer), the speech model's view and the field the words go into.
/// The Composer keeps it in `@State`, so the bar (which replaces the mic row) does not stop a recording. The words of the phone
/// recognizer stream into the field, and the bar's ↑ sends them through the Composer's target.
@MainActor
@Observable
final class ComposerVoiceModel {
    /// The field the words go into, set by the Composer: read, write, and send (the send button's way).
    struct Target {
        var get: () -> String
        var set: (String) -> Void
        var send: (String) -> Void
    }

    /// One phone recording: `dropped` once ✕ or a slide off the button took it, so words that still arrive are ignored.
    final class PhoneRun { var dropped = false }

    private(set) var view: VoiceView?
    let server = ServerDictationModel()
    /// The phone's recognizer (SFSpeechRecognizer / SpeechAnalyzer).
    let speech = SpeechInput()
    /// The phone recognizer's steps (the same rules as Android's PhoneDictationSteps).
    private(set) var phoneSteps = PhoneDictationSteps.State(listening: false, after: nil)
    /// The levels of the phone recognizer, every 80 ms.
    let phoneTrail = LevelTrail()
    /// The phone recognizer's problem: an alert on the mic (nil = nothing to show).
    var phoneProblem: String?
    /// The microphone or the speech permission was refused: an alert offers the settings.
    var phoneDenied = false
    /// A hold-to-talk finger is down: the bar is not drawn while it is (the mic stays under the finger).
    var held = false

    @ObservationIgnored var target: Target?
    @ObservationIgnored private var app: AppModel?
    @ObservationIgnored private var anchor: SpeechMerge.Anchor?
    @ObservationIgnored private var run = PhoneRun()
    @ObservationIgnored private var phoneStartedAt: Date?
    @ObservationIgnored private var sampler: Task<Void, Never>?

    init() {
        server.onText = { [weak self] words, after in self?.deliver(words, after: after) }
    }

    /// The Composer appears: the app gives the server and the activity (the header's character listens).
    func attach(app: AppModel) {
        self.app = app
        server.api = { [weak app] in app?.api }
    }

    /// What the composer draws: the server's bar, else the phone's. nil while nothing records.
    var bar: RecordingBarState? {
        if let s = server.bar { return s }
        guard phoneSteps.listening else { return nil }
        let elapsed = phoneStartedAt.map { Int64(Date().timeIntervalSince($0) * 1000) } ?? 0
        return RecordingBarState(stage: .listening, elapsedMs: max(0, elapsed), endsInSeconds: nil, held: held)
    }

    /// True when the bar replaces the composer's row (a recording runs and no finger holds the mic).
    var showsBar: Bool { RecordingBarRules.showBar(bar) }

    /// The levels the waveform draws: the phone's while it listens, else the server's.
    var trail: LevelTrail { server.phase == .idle && phoneSteps.listening ? phoneTrail : server.trail }

    /// The speech model's view: the way and the offer (refreshed while it downloads, every 2 s).
    func refresh() async {
        while !Task.isCancelled {
            guard let api = app?.api else { view = nil; return }
            if let fresh = try? await VoiceAPI.view(api, lang: appLanguageCode()) { view = fresh }
            guard view?.state == "downloading" else { return }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
    }

    /// The first tap after the offer: 확인 (choose) or 취소 (decline).
    func answer(choose: Bool) {
        guard let api = app?.api, let recommended = view?.recommended else { return }
        Task {
            if choose {
                view = try? await VoiceAPI.choose(api, id: recommended.id)
            } else {
                view = try? await VoiceAPI.decline(api)
            }
        }
    }

    // MARK: the words

    /// A recording starts: the words go at the end of what is typed (iOS's field has no caret to read).
    private func begin() {
        anchor = target.map { SpeechMerge.anchor($0.get()) }
    }

    /// The words of a recording, merged where the recording began. `.send` also sends them (the send button's way).
    func deliver(_ words: String, after: AfterSpeech) {
        guard let target else { return }
        let a = anchor ?? SpeechMerge.anchor(target.get())
        let merged = SpeechMerge.merge(a, spoken: words)
        target.set(merged)
        if after == .send { target.send(merged) }
    }

    // MARK: finishing and dropping

    /// ■ (the words go into the field) or ↑ (sent), whichever way is recording.
    func finish(_ after: AfterSpeech) {
        if server.recording {
            server.finish(after)
            return
        }
        let (next, action) = PhoneDictationSteps.step(phoneSteps, .finish(after))
        phoneSteps = next
        if action == .stopRecognizer { speech.stop() }
    }

    /// ✕, or a slide off the held mic: the recording is dropped and the field goes back to what it was.
    func discard() {
        server.discard()
        let (next, action) = PhoneDictationSteps.step(phoneSteps, .discard)
        phoneSteps = next
        run.dropped = true
        if action == .restoreField, let a = anchor { target?.set(SpeechMerge.merge(a, spoken: "")) }
        anchor = nil
        speech.stop()
        stopSampler()
    }

    /// The Composer leaves the screen: a recording is dropped, as before.
    func discardAll() { discard() }

    // MARK: the phone's recognizer

    /// Asks for the microphone and the speech permission, then starts; `afterAsk` runs once the answer is in (true = started).
    func phoneStart(afterAsk: @escaping (Bool) -> Void) {
        Task {
            switch await SpeechInput.authorize() {
            case .ok:
                startPhone()
                afterAsk(true)
            case .denied:
                phoneDenied = true
                afterAsk(false)
            case .unavailable:
                phoneProblem = L(R.string.voice_unavailable)
                afterAsk(false)
            }
        }
    }

    private func startPhone() {
        guard !speech.listening else { return }
        begin()
        let thisRun = PhoneRun()
        run = thisRun
        phoneSteps = PhoneDictationSteps.step(phoneSteps, .start).0
        phoneStartedAt = Date()
        phoneTrail.clear()
        speech.start(language: appLanguageCode(), onWords: { [weak self] words in
            guard !thisRun.dropped else { return }
            self?.deliver(words, after: .field)
        }, onEnd: { [weak self] problem in
            self?.phoneEnded(problem: thisRun.dropped ? nil : problem)
        })
        app?.activity.listening = speech.listening
        startSampler()
    }

    /// The recognizer stopped by itself or with an error: ↑ sends what is in the field, a problem is shown and nothing is sent.
    private func phoneEnded(problem: String?) {
        let (next, action) = PhoneDictationSteps.step(phoneSteps, .ended(problem: problem != nil))
        phoneSteps = next
        if action == .sendField, let target { target.send(target.get()) }
        anchor = nil
        stopSampler()
        phoneTrail.clear()
        phoneProblem = problem
        app?.activity.listening = false
    }

    private func startSampler() {
        sampler?.cancel()
        sampler = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 80_000_000)
                guard let self, !Task.isCancelled else { return }
                if self.speech.listening {
                    self.phoneTrail.push(Float(self.speech.level), atNanos: Int64(DispatchTime.now().uptimeNanoseconds))
                }
            }
        }
    }

    private func stopSampler() {
        sampler?.cancel()
        sampler = nil
    }
}
#endif
