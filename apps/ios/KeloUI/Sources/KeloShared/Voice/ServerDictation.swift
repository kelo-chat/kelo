import Foundation
import KeloCore

// Server voice input, the iPhone side (Android: apps/shared .../voice/ServerDictation.kt; the same rules, written in Swift for this
// target). The server answers GET /api/voice with the view below; the mic follows DictationWay.pick.

/// One speech model as api/voice.py shows it. Attribution fields are data only: no app screen shows them.
public struct VoiceModelView: Codable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let maker: String?
    public let sizeBytes: Int64?
    public let memoryBytes: Int64?
    public let languages: [String]?
    public let licenseName: String?
    public let licenseUrl: String?
    public let sourceUrl: String?
    public let attribution: String?

    enum CodingKeys: String, CodingKey {
        case id, name, maker, languages, attribution
        case sizeBytes = "size_bytes", memoryBytes = "memory_bytes"
        case licenseName = "license_name", licenseUrl = "license_url", sourceUrl = "source_url"
    }
}

/// GET /api/voice: the owner's voice input state on this server.
public struct VoiceView: Codable, Equatable, Sendable {
    public struct Progress: Codable, Equatable, Sendable {
        public let done: Int64
        public let total: Int64
    }

    public let state: String                 // off | offered | downloading | ready | failed | unsupported
    public let declined: Bool?
    public let recommended: VoiceModelView?
    public let model: VoiceModelView?
    public let progress: Progress?
    public let error: String?
    public let models: [VoiceModelView]?
    public let maxSeconds: Int?
    public let runtime: String?              // ready | missing

    enum CodingKeys: String, CodingKey {
        case state, declined, recommended, model, progress, error, models, runtime
        case maxSeconds = "max_seconds"
    }
}

/// Which way the mic works right now (the same table as Android's DictationWay).
public enum DictationWay: Equatable, Sendable {
    case server, phone, offerThenPhone, offerThenNone, downloadingPhone, downloadingNone, none

    /// view nil (an older server) → the phone's recognizer; no phone recognizer → nothing.
    public static func pick(_ view: VoiceView?, phoneRecognizer: Bool) -> DictationWay {
        func phoneOr() -> DictationWay { phoneRecognizer ? .phone : .none }
        guard let view else { return phoneOr() }
        if let runtime = view.runtime, runtime != "ready" { return phoneOr() }
        switch view.state {
        case "ready": return .server
        case "offered": return phoneRecognizer ? .offerThenPhone : .offerThenNone
        case "downloading": return phoneRecognizer ? .downloadingPhone : .downloadingNone
        default: return phoneOr()        // off (declined), failed, unsupported
        }
    }
}

/// What went wrong with a server recognition, for the one line near the composer.
public enum VoiceProblem: Equatable, Sendable {
    case nothingHeard, offline, busy, tooLong, notReady, failed
}

/// httpStatus nil = no connection. nothingHeard is decided by the caller (the text came back empty).
public func voiceProblem(httpStatus: Int?, code: String?) -> VoiceProblem {
    guard let status = httpStatus else { return .offline }
    switch status {
    case 429: return .busy
    case 413: return .tooLong
    case 409: return .notReady
    default: return .failed
    }
}

/// 16-bit little-endian PCM → a WAV file: a 44-byte RIFF header, PCM, mono.
public enum Wav {
    public static func encode(pcm16le: Data, sampleRate: Int = 16_000) -> Data {
        var out = Data()
        func put32(_ v: Int) { withUnsafeBytes(of: UInt32(truncatingIfNeeded: v).littleEndian) { out.append(contentsOf: $0) } }
        func put16(_ v: Int) { withUnsafeBytes(of: UInt16(truncatingIfNeeded: v).littleEndian) { out.append(contentsOf: $0) } }
        out.append(contentsOf: Array("RIFF".utf8)); put32(36 + pcm16le.count)
        out.append(contentsOf: Array("WAVE".utf8))
        out.append(contentsOf: Array("fmt ".utf8)); put32(16)
        put16(1); put16(1)                       // PCM, mono
        put32(sampleRate); put32(sampleRate * 2) // byte rate
        put16(2); put16(16)                      // block align, bits
        out.append(contentsOf: Array("data".utf8)); put32(pcm16le.count)
        out.append(pcm16le)
        return out
    }
}

/// Levels for the sound graph: RMS of 16-bit samples in [from, to) bytes, 0...1 on a square-root curve; push keeps the newest.
public enum SoundBars {
    public static func rms(_ pcm16le: Data, from: Int, to: Int) -> Float {
        let start = max(0, from) & ~1
        let end = min(pcm16le.count, to) & ~1
        guard end > start else { return 0 }
        var sum = 0.0
        var n = 0
        var i = start
        while i + 1 < end {
            let s = Double(Int16(bitPattern: UInt16(pcm16le[i]) | (UInt16(pcm16le[i + 1]) << 8)))
            sum += s * s
            n += 1
            i += 2
        }
        let mean = sqrt(sum / Double(max(n, 1))) / 32_768.0
        return Float(min(1, max(0, sqrt(mean))))
    }

    public static func push(_ bars: [Float], _ level: Float, max count: Int = 48) -> [Float] {
        Array((bars + [min(1, max(0, level))]).suffix(count))
    }
}

/// The recording clock: two minutes at most; the countdown shows from 1:50.
public enum RecordingClock {
    public static let maxMs: Int64 = 120_000
    public static let countdownFromMs: Int64 = 110_000

    /// m:ss, as the sound graph's clock reads.
    public static func clock(_ ms: Int64) -> String {
        let total = max(0, ms / 1000)
        return "\(total / 60):" + String(format: "%02d", total % 60)
    }
}

/// The text the server's answer gives, or nil when it has none (an empty recognition).
public enum VoiceAnswer {
    public static func text(from body: Data) -> String {
        let object = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
        return (object?["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    }
}
