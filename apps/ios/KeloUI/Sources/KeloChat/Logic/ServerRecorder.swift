import Foundation
#if os(iOS)
import AVFoundation

/// The iPhone's microphone for server voice input: 16 kHz, mono, 16-bit little endian PCM, kept in memory only. `onChunk` runs on the
/// audio thread (it only counts and measures). `stop` ends the recording and returns all of it.
final class ServerRecorder {
    enum Failure: Error { case noLine }

    private let engine = AVAudioEngine()
    private let lock = NSLock()
    private var kept = Data()
    private(set) var running = false

    static func permitted(_ done: @escaping @Sendable (Bool) -> Void) {
        AVAudioApplication.requestRecordPermission { granted in done(granted) }
    }

    func start(onChunk: @escaping @Sendable (Data) -> Void) throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .measurement, options: [])
        try session.setActive(true)
        let input = engine.inputNode
        let inFormat = input.outputFormat(forBus: 0)
        guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: inFormat, to: outFormat) else { throw Failure.noLine }
        lock.lock(); kept = Data(); lock.unlock()
        let ratio = outFormat.sampleRate / inFormat.sampleRate
        input.installTap(onBus: 0, bufferSize: 1_600, format: inFormat) { [weak self] buffer, _ in
            guard let self else { return }
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
            self.lock.lock(); self.kept.append(data); self.lock.unlock()
            onChunk(data)
        }
        engine.prepare()
        try engine.start()
        running = true
    }

    func stop() -> Data {
        teardown()
        lock.lock(); defer { lock.unlock() }
        let all = kept
        kept = Data()
        return all
    }

    func cancel() {
        teardown()
        lock.lock(); kept = Data(); lock.unlock()
    }

    private func teardown() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
#endif
