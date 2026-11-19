import Foundation
#if os(iOS)
import Speech
import AVFoundation
#endif
import KeloShared

/// The phone's own speech recognizer as the call's fallback ears (brief §4.10, §6.3).
public protocol PhoneListener: AnyObject, Sendable {
    func listenOnce(
        language: String,
        onStarted: @escaping @Sendable () -> Void,
        onLevel: @escaping @Sendable (Float) -> Void,
        onDone: @escaping @Sendable (String?) -> Void
    )
    func cancel()
}

/// iOS implementation using SFSpeechRecognizer.
public final class IOSPhoneListener: PhoneListener, @unchecked Sendable {
    private let lock = NSLock()
    #if os(iOS)
    private var engine: AVAudioEngine?
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var silenceTimer: Task<Void, Never>?
    #endif
    private var active = false
    private var startedFired = false
    private var pendingDone: (@Sendable (String?) -> Void)?

    public init() {}

    public func listenOnce(
        language: String,
        onStarted: @escaping @Sendable () -> Void,
        onLevel: @escaping @Sendable (Float) -> Void,
        onDone: @escaping @Sendable (String?) -> Void
    ) {
        cancel()

        lock.lock()
        active = true
        startedFired = false
        pendingDone = onDone
        lock.unlock()

        #if os(iOS)
        let locale = Locale(identifier: language)
        guard let recognizer = SFSpeechRecognizer(locale: locale) ?? SFSpeechRecognizer(), recognizer.isAvailable else {
            finish(nil)
            return
        }

        do {
            let engine = AVAudioEngine()
            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.addsPunctuation = true

            let input = engine.inputNode
            let inFormat = input.outputFormat(forBus: 0)

            input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buffer, _ in
                request.append(buffer)
                let rms = Self.calculateRms(buffer)
                onLevel(rms)
            }

            engine.prepare()
            try engine.start()

            lock.lock()
            self.engine = engine
            self.request = request
            lock.unlock()

            let task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                guard let self else { return }
                if let result {
                    self.lock.lock()
                    if !self.startedFired {
                        self.startedFired = true
                        onStarted()
                    }
                    self.lock.unlock()

                    self.restartSilenceTimer(finalText: result.bestTranscription.formattedString, afterMs: CallRules.phoneEndSilenceMs)
                    if result.isFinal {
                        self.finish(result.bestTranscription.formattedString)
                    }
                } else if error != nil {
                    self.finish(nil)
                }
            }

            lock.lock()
            self.task = task
            lock.unlock()

            restartSilenceTimer(finalText: "", afterMs: CallRules.phoneNoSpeechMs)
        } catch {
            finish(nil)
        }
        #else
        // Non-iOS / test fallback
        onStarted()
        onDone("")
        #endif
    }

    #if os(iOS)
    /// Finishes with the text heard so far (or "" when nothing was heard) after [afterMs] of silence.
    private func restartSilenceTimer(finalText: String, afterMs: Int64) {
        lock.lock()
        silenceTimer?.cancel()
        silenceTimer = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(afterMs) * 1_000_000)
            guard !Task.isCancelled else { return }
            self?.finish(finalText)
        }
        lock.unlock()
    }

    private static func calculateRms(_ buffer: AVAudioPCMBuffer) -> Float {
        guard let channelData = buffer.floatChannelData?[0] else { return 0 }
        let length = Int(buffer.frameLength)
        guard length > 0 else { return 0 }
        var sum: Float = 0
        for i in 0..<length {
            let s = channelData[i]
            sum += s * s
        }
        let mean = sum / Float(length)
        return min(1, max(0, sqrt(mean)))
    }
    #endif

    private func finish(_ text: String?) {
        end(text, report: true)
    }

    /// A cancel never reports: the old way called onDone, which started listening again at once (a loop, 2026-11-19).
    private func end(_ text: String?, report: Bool) {
        let done: (@Sendable (String?) -> Void)? = lock.withLock {
            guard active else { return nil }
            active = false
            #if os(iOS)
            silenceTimer?.cancel()
            silenceTimer = nil
            engine?.inputNode.removeTap(onBus: 0)
            engine?.stop()
            engine = nil
            request?.endAudio()
            request = nil
            task?.cancel()
            task = nil
            #endif
            let d = pendingDone
            pendingDone = nil
            return d
        }
        if report { done?(text) }
    }

    public func cancel() {
        end(nil, report: false)
    }
}
