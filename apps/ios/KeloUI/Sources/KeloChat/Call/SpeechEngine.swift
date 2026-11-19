import Foundation
import AVFoundation

/// The device text-to-speech engine fallback (brief §4.8).
public protocol SpeechEngine: AnyObject, Sendable {
    func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void)
    func stop()
}

/// AVSpeechSynthesizer implementation of SpeechEngine for iOS.
public final class IOSSpeechEngine: NSObject, SpeechEngine, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let synth = AVSpeechSynthesizer()
    private let lock = NSLock()
    private var pendingDone: (@Sendable () -> Void)?

    public override init() {
        super.init()
        synth.delegate = self
    }

    public func speak(_ text: String, lang: String, onDone: @escaping @Sendable () -> Void) {
        lock.withLock {
            synth.stopSpeaking(at: .immediate)
            pendingDone = onDone
            let u = AVSpeechUtterance(string: text)
            u.voice = ReadAloud.voice(for: lang)
            synth.speak(u)
        }
    }

    public func stop() {
        let done = lock.withLock { () -> (@Sendable () -> Void)? in
            synth.stopSpeaking(at: .immediate)
            let d = pendingDone
            pendingDone = nil
            return d
        }
        done?()
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let done = lock.withLock {
            let d = pendingDone
            pendingDone = nil
            return d
        }
        done?()
    }

    nonisolated public func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        let done = lock.withLock {
            let d = pendingDone
            pendingDone = nil
            return d
        }
        done?()
    }
}
