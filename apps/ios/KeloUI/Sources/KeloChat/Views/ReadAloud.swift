import SwiftUI
import AVFoundation
import KeloShared

/// "Read aloud" for chat messages (Android chat/ReadAloud.kt): the phone's own speech (AVSpeechSynthesizer) in the message's language.
/// One message speaks at a time; reading a message again stops it; reading another one switches to it. `reading` is the text handed
/// to `toggle` while it is spoken (nil otherwise), which the menu compares to show "읽기 멈추기".
@MainActor @Observable
public final class ReadAloud: NSObject, AVSpeechSynthesizerDelegate {
    public private(set) var reading: String?
    @ObservationIgnored private let synth = AVSpeechSynthesizer()
    @ObservationIgnored private var turn = 0
    @ObservationIgnored private var last: ObjectIdentifier?

    public override init() {
        super.init()
        synth.delegate = self
    }

    public func toggle(_ text: String) {
        if reading == text { stop(); return }
        synth.stopSpeaking(at: .immediate)
        let plain = SpokenText.from(text)
        turn += 1
        guard !plain.isEmpty else { reading = nil; last = nil; return }
        reading = text
        let tag = SpokenText.language(plain, fallback: appLanguageCode())
        let voice = Self.voice(for: tag)
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, options: .duckOthers)
        try? AVAudioSession.sharedInstance().setActive(true)
        #endif
        for piece in SpokenText.chunks(plain) {
            let u = AVSpeechUtterance(string: piece)
            u.voice = voice
            last = ObjectIdentifier(u)
            synth.speak(u)
        }
    }

    public func stop() {
        turn += 1
        reading = nil
        last = nil
        synth.stopSpeaking(at: .immediate)
        release()
    }

    private func release() {
        #if os(iOS)
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        #endif
    }

    /// The voice for a BCP-47 language ("ko", "pt-BR"): the system's own match, else the first installed voice of that language.
    static func voice(for tag: String) -> AVSpeechSynthesisVoice? {
        if let v = AVSpeechSynthesisVoice(language: tag) { return v }
        let base = tag.split(separator: "-").first.map { $0.lowercased() } ?? tag.lowercased()
        return AVSpeechSynthesisVoice.speechVoices().first { $0.language.lowercased().hasPrefix(base) }
    }

    private func finished(_ id: ObjectIdentifier) {
        guard id == last else { return }
        reading = nil
        last = nil
        release()
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let id = ObjectIdentifier(utterance)
        Task { @MainActor in self.finished(id) }
    }
}

private struct ReadAloudKey: EnvironmentKey {
    static let defaultValue: ReadAloud? = nil
}

extension EnvironmentValues {
    /// The chat screen's reader (nil elsewhere, where 소리 내어 읽기 is not offered).
    var readAloud: ReadAloud? {
        get { self[ReadAloudKey.self] }
        set { self[ReadAloudKey.self] = newValue }
    }
}
