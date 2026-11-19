import Foundation
import AVFoundation

/// The device text-to-speech engine fallback (brief §4.8). One engine per call: created once, released when the call ends.
public protocol SpeechEngine: AnyObject, Sendable {
    func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void)
    /// Like speak; onDone says whether the text was spoken.
    func speakChecked(_ text: String, lang: String, onDone: @escaping @Sendable (Bool) -> Void)
    func stop()
    /// Lets go of the engine for good (a call ended). Not named release: NSObject already has one.
    func shutDown()
}

public extension SpeechEngine {
    func speakChecked(_ text: String, lang: String, onDone: @escaping @Sendable (Bool) -> Void) {
        speak(text, lang: lang) { onDone(true) }
    }
    func shutDown() {}
}

/// AVSpeechSynthesizer implementation of SpeechEngine for iOS.
public final class IOSSpeechEngine: NSObject, SpeechEngine, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let synth = AVSpeechSynthesizer()
    private let lock = NSLock()
    private var pendingDone: (@Sendable (Bool) -> Void)?
    /// The utterance whose end the pending callback waits for; a late end of an older utterance is ignored (2026-11-19).
    private var current: AVSpeechUtterance?

    public override init() {
        super.init()
        synth.delegate = self
    }

    public func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void) {
        speakChecked(text, lang: lang) { _ in onDone() }
    }

    public func speakChecked(_ text: String, lang: String, onDone: @escaping @Sendable (Bool) -> Void) {
        lock.withLock {
            pendingDone = nil
            current = nil
        }
        synth.stopSpeaking(at: .immediate)
        let u = AVSpeechUtterance(string: text)
        u.voice = ReadAloud.voice(for: lang)
        lock.withLock {
            pendingDone = onDone
            current = u
        }
        synth.speak(u)
    }

    public func stop() {
        let done = lock.withLock { () -> (@Sendable (Bool) -> Void)? in
            let d = pendingDone
            pendingDone = nil
            current = nil
            return d
        }
        synth.stopSpeaking(at: .immediate)
        done?(true)
    }

    public func shutDown() {
        stop()
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        finished(utterance)
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        finished(utterance)
    }

    private nonisolated func finished(_ utterance: AVSpeechUtterance) {
        let done = lock.withLock { () -> (@Sendable (Bool) -> Void)? in
            guard utterance === current else { return nil }
            let d = pendingDone
            pendingDone = nil
            current = nil
            return d
        }
        done?(true)
    }
}
