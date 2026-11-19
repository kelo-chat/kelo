import Foundation
import AVFoundation
#if os(iOS)
import UIKit
#endif
import KeloShared

/// The platform audio operations for an in-app call (brief §4.7, §6.3).
public protocol CallAudio: AnyObject, Sendable {
    /// True when the platform cancels Kelo's voice from the microphone (allows barge-in).
    var echoCancel: Bool { get }

    /// Phones true, desktop false.
    var hasSpeakerSwitch: Bool { get }

    /// Whether the loud speaker is on (phones only).
    var speakerOn: Bool { get set }

    /// Starts recording 16 kHz mono PCM16; returns false if no mic or permission denied.
    func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool

    /// Stops microphone recording.
    func stopMic()

    /// Plays mono PCM16 at [sampleRate], reporting window levels for the waveform; returns when played or stopped.
    func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async

    /// Aborts current playback immediately.
    func stopPlayback()

    /// Soft chime ringing sound (C16); respects silent/ringer mode.
    func chime(on: Bool)

    /// Releases audio focus and device resources.
    func release()

    /// Callback invoked when audio focus is lost or session is interrupted by another app or phone call.
    var onInterrupted: (@Sendable () -> Void)? { get set }
}

/// The iPhone's audio pipeline for call mode (brief §6.3).
public final class IOSCallAudio: CallAudio, @unchecked Sendable {
    public let echoCancel: Bool = true
    public let hasSpeakerSwitch: Bool = true

    private let lock = NSLock()
    private var _speakerOn: Bool = true
    public var speakerOn: Bool {
        get { lock.withLock { _speakerOn } }
        set {
            lock.withLock { _speakerOn = newValue }
            updateSessionRoutes()
        }
    }

    public var onInterrupted: (@Sendable () -> Void)?

    private var engine: AVAudioEngine?
    private var playerNode: AVAudioPlayerNode?
    private var isPlaying = false
    private var micRunning = false
    private var chimeTask: Task<Void, Never>?
    private var playContinuation: CheckedContinuation<Void, Never>?

    public init() {
        setupInterruptionObserver()
    }

    private func setupInterruptionObserver() {
        #if os(iOS)
        NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            guard let userInfo = notification.userInfo,
                  let typeValue = userInfo[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }
            if type == .began {
                self?.onInterrupted?()
            }
        }
        #endif
    }

    private func updateSessionRoutes() {
        #if os(iOS)
        do {
            let session = AVAudioSession.sharedInstance()
            var options: AVAudioSession.CategoryOptions = [.allowBluetooth]
            if speakerOn {
                options.insert(.defaultToSpeaker)
            }
            try session.setCategory(.playAndRecord, mode: .voiceChat, options: options)
            try session.setActive(true)
        } catch {
            // Log or ignore route errors
        }
        #endif
    }

    private func ensureEngine() throws -> AVAudioEngine {
        if let existing = engine { return existing }
        let newEngine = AVAudioEngine()
        #if os(iOS)
        try? newEngine.inputNode.setVoiceProcessingEnabled(true)
        #endif
        let player = AVAudioPlayerNode()
        newEngine.attach(player)
        let mainMixer = newEngine.mainMixerNode
        let format = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 1) ?? mainMixer.outputFormat(forBus: 0)
        newEngine.connect(player, to: mainMixer, format: format)
        self.playerNode = player
        self.engine = newEngine
        return newEngine
    }

    public func startMic(onChunk: @escaping @Sendable (Data) -> Void) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        if micRunning { return true }

        #if os(iOS)
        updateSessionRoutes()
        #endif

        do {
            let eng = try ensureEngine()
            let input = eng.inputNode
            let inFormat = input.outputFormat(forBus: 0)
            guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true),
                  let converter = AVAudioConverter(from: inFormat, to: outFormat) else {
                return false
            }

            let ratio = outFormat.sampleRate / inFormat.sampleRate
            input.installTap(onBus: 0, bufferSize: 1_600, format: inFormat) { buffer, _ in
                let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
                guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: capacity) else { return }
                var fed = false
                var error: NSError?
                converter.convert(to: out, error: &error) { _, status in
                    if fed { status.pointee = .noDataNow; return nil }
                    fed = true
                    status.pointee = .haveData
                    return buffer
                }
                guard error == nil, out.frameLength > 0, let channel = out.int16ChannelData else { return }
                let data = Data(bytes: channel[0], count: Int(out.frameLength) * 2)
                onChunk(data)
            }

            if !eng.isRunning {
                eng.prepare()
                try eng.start()
            }
            micRunning = true
            return true
        } catch {
            return false
        }
    }

    public func stopMic() {
        lock.lock()
        defer { lock.unlock() }
        guard micRunning else { return }
        engine?.inputNode.removeTap(onBus: 0)
        micRunning = false
        if !isPlaying {
            engine?.stop()
        }
    }

    public func play(pcm16le: Data, sampleRate: Int, onLevel: @escaping @Sendable (Float) -> Void) async {
        stopPlayback()

        do {
            let eng = try ensureEngine()
            guard let player = playerNode else { return }

            guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(sampleRate), channels: 1, interleaved: false) else { return }
            let frameCount = AVAudioFrameCount(pcm16le.count / 2)
            guard frameCount > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else { return }
            buffer.frameLength = frameCount

            guard let floatData = buffer.floatChannelData?[0] else { return }
            pcm16le.withUnsafeBytes { raw in
                guard let int16Ptr = raw.bindMemory(to: Int16.self).baseAddress else { return }
                for i in 0..<Int(frameCount) {
                    floatData[i] = Float(int16Ptr[i]) / 32768.0
                }
            }

            if !eng.isRunning {
                eng.prepare()
                try eng.start()
            }
            if !player.isPlaying {
                player.play()
            }

            let meter = LevelMeter(sampleRate: sampleRate)
            let chunkSize = max(640, (sampleRate * 40) / 1000 * 2)
            var offset = 0
            while offset < pcm16le.count {
                let end = min(pcm16le.count, offset + chunkSize)
                let chunk = pcm16le.subdata(in: offset..<end)
                let levels = meter.add(chunk)
                for lvl in levels { onLevel(lvl) }
                offset = end
            }

            lock.withLock {
                isPlaying = true
            }

            await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                lock.withLock {
                    self.playContinuation = cont
                }

                player.scheduleBuffer(buffer) { [weak self] in
                    self?.finishPlayback()
                }
            }
        } catch {
            finishPlayback()
        }
    }

    private func finishPlayback() {
        lock.lock()
        isPlaying = false
        let cont = playContinuation
        playContinuation = nil
        lock.unlock()
        cont?.resume()
        onLevel(0)
    }

    private func onLevel(_ lvl: Float) {}

    public func stopPlayback() {
        lock.lock()
        playerNode?.stop()
        isPlaying = false
        let cont = playContinuation
        playContinuation = nil
        lock.unlock()
        cont?.resume()
    }

    public func chime(on: Bool) {
        chimeTask?.cancel()
        chimeTask = nil

        guard on else { return }

        chimeTask = Task { [weak self] in
            let chimePcm = Self.generateChimePcm(16_000)
            while !Task.isCancelled {
                #if os(iOS)
                await MainActor.run {
                    let feedback = UINotificationFeedbackGenerator()
                    feedback.prepare()
                    feedback.notificationOccurred(.warning)
                }
                #endif
                await self?.play(pcm16le: chimePcm, sampleRate: 16_000, onLevel: { _ in })
                try? await Task.sleep(nanoseconds: 1_640_000_000)
            }
        }
    }

    public func release() {
        stopMic()
        stopPlayback()
        chime(on: false)
        engine?.stop()
        engine = nil
        playerNode = nil
    }

    /// Generates 2-note sine chime (660 Hz then 880 Hz, 180 ms each, 20 ms fades, total 360 ms) as 16-bit PCM (brief §6.3).
    public static func generateChimePcm(_ sampleRate: Int = 16_000) -> Data {
        let note1Ms = 180
        let note2Ms = 180
        let fadeMs = 20
        let note1Samples = (sampleRate * note1Ms) / 1000
        let note2Samples = (sampleRate * note2Ms) / 1000
        let fadeSamples = (sampleRate * fadeMs) / 1000
        let totalSamples = note1Samples + note2Samples

        var out = Data(count: totalSamples * 2)
        let amplitude = 18000.0

        out.withUnsafeMutableBytes { raw in
            guard let ptr = raw.bindMemory(to: Int16.self).baseAddress else { return }

            // Note 1: 660 Hz
            for i in 0..<note1Samples {
                let env: Double
                if i < fadeSamples {
                    env = Double(i) / Double(fadeSamples)
                } else if i > note1Samples - fadeSamples {
                    env = Double(note1Samples - 1 - i) / Double(fadeSamples)
                } else {
                    env = 1.0
                }
                let s = Int(sin(2.0 * .pi * 660.0 * Double(i) / Double(sampleRate)) * env * amplitude)
                ptr[i] = Int16(clamping: s)
            }

            // Note 2: 880 Hz
            let offset = note1Samples
            for i in 0..<note2Samples {
                let env: Double
                if i < fadeSamples {
                    env = Double(i) / Double(fadeSamples)
                } else if i > note2Samples - fadeSamples {
                    env = Double(note2Samples - 1 - i) / Double(fadeSamples)
                } else {
                    env = 1.0
                }
                let s = Int(sin(2.0 * .pi * 880.0 * Double(i) / Double(sampleRate)) * env * amplitude)
                ptr[offset + i] = Int16(clamping: s)
            }
        }
        return out
    }
}
