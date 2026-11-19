import Foundation
import Observation

// The recording bar's pure rules, the iPhone mirror of apps/shared/.../voice/RecordingBar.kt (the same numbers and the same
// behaviour; the Kotlin file is the reference when they differ).

/// Where the words go when the owner ends a recording: the field (■, a pause, the 2-minute cap) or sent at once (↑).
public enum AfterSpeech: Equatable, Sendable { case field, send }

/// What the composer draws while the owner speaks.
public struct RecordingBarState: Equatable, Sendable {
    public enum Stage: Equatable, Sendable { case listening, turningToText }
    public let stage: Stage
    public let elapsedMs: Int64
    /// Non-nil from 1:50 (server path only).
    public let endsInSeconds: Int?
    /// A hold-to-talk finger is down: keep the normal composer (the mic stays under the finger).
    public let held: Bool

    public init(stage: Stage, elapsedMs: Int64, endsInSeconds: Int?, held: Bool) {
        self.stage = stage
        self.elapsedMs = elapsedMs
        self.endsInSeconds = endsInSeconds
        self.held = held
    }
}

public enum RecordingBarRules {
    /// One level per 80 ms of audio.
    public static let windowMs: Int64 = 80
    public static let floorDb: Float = -55
    public static let ceilDb: Float = -15
    /// A loud bar fades over the next few bars instead of dropping at once.
    public static let release: Float = 0.55
    public static let dotDp: Float = 3
    public static let maxDp: Float = 24
    public static let gapDp: Float = 2

    public static func showBar(_ s: RecordingBarState?) -> Bool { s != nil && s?.held == false }

    /// rms 0...1 → dBFS → (dB - floor) / (ceil - floor), clamped to 0...1. Silence (rms <= 0) is 0.
    public static func level(rms: Float) -> Float {
        if rms <= 0 { return 0 }
        let db = 20 * log10(rms)
        return min(1, max(0, (db - floorDb) / (ceilDb - floorDb)))
    }

    public static func height(_ level: Float) -> Float { dotDp + (maxDp - dotDp) * min(1, max(0, level)) }

    public static func visibleBars(widthDp: Float) -> Int { max(0, Int((widthDp + gapDp) / (dotDp + gapDp))) }

    /// nil before the countdown starts (1:50), else the whole seconds left until the 2-minute cap, never below 0.
    public static func endsIn(elapsedMs: Int64) -> Int? {
        if elapsedMs < RecordingClock.countdownFromMs { return nil }
        let left = RecordingClock.maxMs - elapsedMs
        return left <= 0 ? 0 : Int((left + 999) / 1000)
    }

    public static func sendEnabled(_ stage: RecordingBarState.Stage, busy: Bool) -> Bool {
        stage == .listening && !busy
    }
}

/// Turns 16 kHz PCM16 (little endian, mono) into one level per 80 ms of audio. A window split between two chunks, or an odd byte at
/// a chunk's end, carries over.
public final class LevelMeter {
    public let sampleRate: Int
    private let samplesPerWindow: Int
    private var sumSquares = 0.0
    private var samples = 0
    private var pendingByte: UInt8?

    public init(sampleRate: Int = 16_000) {
        self.sampleRate = sampleRate
        self.samplesPerWindow = max(1, (sampleRate * 80) / 1000)
    }

    /// The finished windows' levels in this chunk, oldest first.
    public func add(_ pcm16le: Data) -> [Float] {
        var out: [Float] = []
        let bytes = Array(pcm16le)
        var i = 0
        if let pending = pendingByte, !bytes.isEmpty {
            consume(UInt16(pending) | (UInt16(bytes[0]) << 8), into: &out)
            pendingByte = nil
            i = 1
        }
        while i + 1 < bytes.count {
            consume(UInt16(bytes[i]) | (UInt16(bytes[i + 1]) << 8), into: &out)
            i += 2
        }
        if i < bytes.count { pendingByte = bytes[i] }
        return out
    }

    public func reset() {
        sumSquares = 0
        samples = 0
        pendingByte = nil
    }

    private func consume(_ raw: UInt16, into out: inout [Float]) {
        let s = Double(Int16(bitPattern: raw)) / 32_768
        sumSquares += s * s
        samples += 1
        if samples == samplesPerWindow {
            let rms = Float(sqrt(sumSquares / Double(samplesPerWindow)))
            out.append(RecordingBarRules.level(rms: rms))
            sumSquares = 0
            samples = 0
        }
    }
}

/// The newest levels, oldest first, capacity 256. push applies the release: v = max(level, last * release). `version` changes on every
/// push or clear, so the view that reads it redraws.
@Observable
public final class LevelTrail {
    private let capacity: Int
    private var values: [Float]
    private var count = 0
    private var head = 0
    private var last: Float = 0
    public private(set) var version = 0
    public private(set) var lastPushNanos: Int64 = 0

    public init(capacity: Int = 256) {
        self.capacity = capacity
        values = Array(repeating: 0, count: capacity)
    }

    public func push(_ level: Float, atNanos: Int64 = Int64(Date().timeIntervalSince1970 * 1_000_000_000)) {
        let v = max(min(1, max(0, level)), last * RecordingBarRules.release)
        last = v
        values[head] = v
        head = (head + 1) % capacity
        count = min(count + 1, capacity)
        lastPushNanos = atNanos
        version += 1
    }

    /// The newest levels, oldest → newest.
    public func snapshot() -> [Float] {
        let start = (head - count + capacity) % capacity
        return (0..<count).map { values[(start + $0) % capacity] }
    }

    public func clear() {
        count = 0
        head = 0
        last = 0
        lastPushNanos = 0
        version += 1
    }
}

/// The phone recognizer's steps for the bar (the recognizer itself stays platform code and runs the actions it is told).
public enum PhoneDictationSteps {
    public enum Action: Equatable, Sendable { case none, stopRecognizer, sendField, restoreField }

    public struct State: Equatable, Sendable {
        public var listening: Bool
        public var after: AfterSpeech?
        public init(listening: Bool, after: AfterSpeech?) { self.listening = listening; self.after = after }
    }

    public enum Event: Equatable, Sendable {
        case start
        case finish(AfterSpeech)
        case discard
        /// The recognizer stopped; problem = it reported an error (its own line shows).
        case ended(problem: Bool)
    }

    public static func step(_ s: State, _ e: Event) -> (State, Action) {
        let done = State(listening: false, after: nil)
        switch e {
        case .start:
            return (State(listening: true, after: nil), .none)
        case .finish(let after):
            return s.listening ? (State(listening: true, after: after), .stopRecognizer) : (s, .none)
        case .discard:
            return (done, s.listening ? .restoreField : .none)
        case .ended(let problem):
            if problem { return (done, .none) }                  // nothing is sent, even after ↑
            return (done, s.after == .send ? .sendField : .none)
        }
    }
}
