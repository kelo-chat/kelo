import Foundation
import KeloCore

// MARK: - Speech View & Models

public struct SpeechVoice: Codable, Equatable, Hashable, Sendable {
    public let id: Int
    public let kind: String
    public let n: Int

    public init(id: Int, kind: String, n: Int) {
        self.id = id
        self.kind = kind
        self.n = n
    }
}

public struct SpeechView: Codable, Equatable, Sendable {
    public let state: String
    public let declined: Bool
    public let model: VoiceModelView?
    public let progress: VoiceView.Progress?
    public let error: String?
    public let voice: Int
    public let speed: Double
    public let voices: [SpeechVoice]
    public let languages: [String]
    public let runtime: String

    public init(
        state: String,
        declined: Bool = false,
        model: VoiceModelView? = nil,
        progress: VoiceView.Progress? = nil,
        error: String? = nil,
        voice: Int = 0,
        speed: Double = 1.0,
        voices: [SpeechVoice] = [],
        languages: [String] = [],
        runtime: String = "ready"
    ) {
        self.state = state
        self.declined = declined
        self.model = model
        self.progress = progress
        self.error = error
        self.voice = voice
        self.speed = speed
        self.voices = voices
        self.languages = languages
        self.runtime = runtime
    }

    enum CodingKeys: String, CodingKey {
        case state, declined, model, progress, error, voice, speed, voices, languages, runtime
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.state = try container.decode(String.self, forKey: .state)
        self.declined = try container.decodeIfPresent(Bool.self, forKey: .declined) ?? false
        self.model = try container.decodeIfPresent(VoiceModelView.self, forKey: .model)
        self.progress = try container.decodeIfPresent(VoiceView.Progress.self, forKey: .progress)
        self.error = try container.decodeIfPresent(String.self, forKey: .error)
        self.voice = try container.decodeIfPresent(Int.self, forKey: .voice) ?? 0
        self.speed = try container.decodeIfPresent(Double.self, forKey: .speed) ?? 1.0
        self.voices = try container.decodeIfPresent([SpeechVoice].self, forKey: .voices) ?? []
        self.languages = try container.decodeIfPresent([String].self, forKey: .languages) ?? []
        self.runtime = try container.decodeIfPresent(String.self, forKey: .runtime) ?? "ready"
    }
}

// MARK: - Call Rules

public enum CallRules {
    public static let ringOnButton = true          // C4
    public static let ringMs: Int64 = 30_000       // C16
    public static let quietEndMs: Int64 = 5 * 60_000 // C8
    public static let bargeInPhones = true         // C5
    public static let prefetch = 2
    public static let echoTailMs: Int64 = 250

    public static func clock(elapsedMs: Int64) -> String {
        let total = max(0, elapsedMs) / 1000
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        let ss = String(format: "%02d", s)
        if h > 0 {
            let mm = String(format: "%02d", m)
            return "\(h):\(mm):\(ss)"
        } else {
            return "\(m):\(ss)"
        }
    }

    public static func mood(for phase: CallSteps.Phase) -> KeloMood {
        switch phase {
        case .listening, .hearing: return .listening
        case .transcribing, .waiting, .connecting: return .thinking
        case .speaking: return .reading
        default: return .idle
        }
    }
}

// MARK: - Speech Gate

public final class SpeechGate: @unchecked Sendable {
    public enum Out: Equatable, Sendable {
        case started
        case ended(Data)
        case dropped
    }

    private let startDb: Float
    private let bargeStartDb: Float
    private let absMinDb: Float
    private let startMs: Int
    private let bargeStartMs: Int
    private let endMs: Int
    private let preRollMs: Int
    private let maxMs: Int
    private let minSpeechMs: Int

    public var speaking: Bool = false

    private var floor: Float = -60.0
    public var floorDb: Float { floor }

    private var remainder = Data()
    private let maxPreRollWindows: Int
    private var preRollWindows: [Data] = []

    private var inUtterance = false
    private var utteranceChunks: [Data] = []
    private var utteranceTotalMs = 0
    private var utteranceSpeechMs = 0
    private var utteranceQuietMs = 0
    private var recentWindows: [Bool] = []

    public init(
        startDb: Float = 12.0,
        bargeStartDb: Float = 18.0,
        absMinDb: Float = -50.0,
        startMs: Int = 200,
        bargeStartMs: Int = 300,
        endMs: Int = 700,
        preRollMs: Int = 300,
        maxMs: Int = 60_000,
        minSpeechMs: Int = 300
    ) {
        self.startDb = startDb
        self.bargeStartDb = bargeStartDb
        self.absMinDb = absMinDb
        self.startMs = startMs
        self.bargeStartMs = bargeStartMs
        self.endMs = endMs
        self.preRollMs = preRollMs
        self.maxMs = maxMs
        self.minSpeechMs = minSpeechMs
        self.maxPreRollWindows = preRollMs / 20
    }

    public func feed(_ pcm16le: Data) -> [Out] {
        var all = remainder
        all.append(pcm16le)
        let windowBytes = 640 // 20 ms at 16 kHz mono 16-bit
        let numWindows = all.count / windowBytes
        if all.count % windowBytes != 0 {
            remainder = all.subdata(in: (numWindows * windowBytes)..<all.count)
        } else {
            remainder = Data()
        }

        var out: [Out] = []
        for w in 0..<numWindows {
            let offset = w * windowBytes
            let window = all.subdata(in: offset..<(offset + windowBytes))
            processWindow(window, out: &out)
        }
        return out
    }

    private func processWindow(_ window: Data, out: inout [Out]) {
        let db = calcDb(window)
        let neededDb = speaking ? bargeStartDb : startDb
        let isSpeech = db >= (floor + neededDb) && db >= absMinDb

        if !inUtterance {
            floor = max(-75.0, min(-30.0, 0.95 * floor + 0.05 * db))
            recentWindows.append(isSpeech)
            if recentWindows.count > 15 {
                recentWindows.removeFirst()
            }
            preRollWindows.append(window)
            if preRollWindows.count > maxPreRollWindows {
                preRollWindows.removeFirst()
            }

            let neededSpeechMs = speaking ? bargeStartMs : startMs
            let speechCount = recentWindows.filter { $0 }.count
            if speechCount * 20 >= neededSpeechMs {
                inUtterance = true
                out.append(.started)
                utteranceChunks = preRollWindows
                utteranceTotalMs = preRollWindows.count * 20
                utteranceSpeechMs = speechCount * 20
                utteranceQuietMs = 0
                recentWindows.removeAll()
                preRollWindows.removeAll()
            }
        } else {
            if !isSpeech {
                floor = max(-75.0, min(-30.0, 0.95 * floor + 0.05 * db))
                utteranceQuietMs += 20
            } else {
                utteranceQuietMs = 0
                utteranceSpeechMs += 20
            }

            utteranceChunks.append(window)
            utteranceTotalMs += 20

            if utteranceQuietMs >= endMs || utteranceTotalMs >= maxMs {
                finishUtterance(out: &out)
            }
        }
    }

    private func finishUtterance(out: inout [Out]) {
        let totalBytes = utteranceChunks.reduce(0) { $0 + $1.count }
        let limit = min(totalBytes, maxMs * 32)
        var pcm = Data(capacity: limit)
        for chunk in utteranceChunks {
            if pcm.count + chunk.count <= limit {
                pcm.append(chunk)
            } else {
                let take = limit - pcm.count
                pcm.append(chunk.prefix(take))
                break
            }
        }

        if utteranceSpeechMs < minSpeechMs {
            out.append(.dropped)
        } else {
            out.append(.ended(pcm))
        }

        inUtterance = false
        utteranceChunks.removeAll()
        utteranceTotalMs = 0
        utteranceSpeechMs = 0
        utteranceQuietMs = 0
        recentWindows.removeAll()
        preRollWindows.removeAll()
    }

    public func reset() {
        floor = -60.0
        inUtterance = false
        remainder = Data()
        utteranceChunks.removeAll()
        utteranceTotalMs = 0
        utteranceSpeechMs = 0
        utteranceQuietMs = 0
        recentWindows.removeAll()
        preRollWindows.removeAll()
    }

    private func calcDb(_ window: Data) -> Float {
        var sumSq = 0.0
        window.withUnsafeBytes { buf in
            let ptr = buf.bindMemory(to: Int16.self)
            for i in 0..<320 {
                let sample = Double(ptr[i])
                sumSq += sample * sample
            }
        }
        let rms = sqrt(sumSq / 320.0)
        if rms <= 0.0 { return -100.0 }
        return Float(20.0 * log10(rms / 32768.0))
    }
}

// MARK: - Sentence Cutter

public final class SentenceCutter: @unchecked Sendable {
    private let firstMax: Int
    private let max: Int
    private var pending = ""
    private var line = ""
    private var decided = false
    private var lineFence = false
    private var inFence = false
    private var first = true

    private static let ends: Set<Character> = [".", "!", "?", "…", "。", "！", "？"]
    private static let cjkEnds: Set<Character> = ["。", "！", "？"]
    private static let commas: Set<Character> = [",", "、", "，", ";", ":"]

    public init(firstMax: Int = 80, max: Int = 300) {
        self.firstMax = firstMax
        self.max = max
    }

    public func add(_ delta: String) -> [String] {
        var out: [String] = []
        for c in delta {
            if c == "\n" {
                endLine()
                flush(&out, final: false, newline: true)
                continue
            }
            if !decided {
                line.append(c)
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.count >= 3 {
                    decided = true
                    lineFence = trimmed.hasPrefix("```")
                    if !lineFence && !inFence { pending.append(line) }
                    line = ""
                }
            } else if !lineFence && !inFence {
                pending.append(c)
            }
        }
        cut(&out, final: false)
        return out
    }

    public func finish() -> [String] {
        var out: [String] = []
        endLine()
        flush(&out, final: true, newline: false)
        inFence = false
        return out
    }

    public func reset() {
        pending = ""; line = ""; decided = false; lineFence = false; inFence = false; first = true
    }

    private func endLine() {
        if !decided {
            if !inFence { pending.append(line) }
        } else if lineFence {
            inFence.toggle()
        }
        line = ""; decided = false; lineFence = false
    }

    private func flush(_ out: inout [String], final: Bool, newline: Bool) {
        cut(&out, final: final)
        if newline || final {
            emit(&out, raw: pending)
            pending = ""
        }
    }

    private func cut(_ out: inout [String], final: Bool) {
        while true {
            let idx = sentenceEnd(final: final)
            if idx >= 0 {
                let endIdx = pending.index(pending.startIndex, offsetBy: idx + 1)
                emit(&out, raw: String(pending[..<endIdx]))
                pending = String(pending[endIdx...])
                continue
            }
            if first && pending.count > firstMax {
                let comma = lastComma(pending)
                if comma >= 20 {
                    let endIdx = pending.index(pending.startIndex, offsetBy: comma + 1)
                    emit(&out, raw: String(pending[..<endIdx]))
                    pending = String(pending[endIdx...])
                    continue
                }
            }
            if pending.count > max {
                let prefixWindow = String(pending.prefix(max))
                let spaceIdx = prefixWindow.lastIndex(of: " ").map { prefixWindow.distance(from: prefixWindow.startIndex, to: $0) } ?? -1
                let at = (spaceIdx >= 1) ? spaceIdx : max
                let endIdx = pending.index(pending.startIndex, offsetBy: at)
                emit(&out, raw: String(pending[..<endIdx]))
                pending = String(pending[endIdx...])
                continue
            }
            return
        }
    }

    private func sentenceEnd(final: Bool) -> Int {
        let chars = Array(pending)
        for i in 0..<chars.count {
            let c = chars[i]
            if !Self.ends.contains(c) { continue }
            if Self.cjkEnds.contains(c) { return i }
            if i + 1 >= chars.count && !final { return -1 }
            if i + 1 < chars.count && !chars[i + 1].isWhitespace { continue }
            if c == "." && singleLetterBefore(chars, at: i) { continue }
            return i
        }
        return -1
    }

    private func singleLetterBefore(_ chars: [Character], at i: Int) -> Bool {
        if i < 1 || !chars[i - 1].isLetter { return false }
        return i < 2 || !chars[i - 2].isLetter
    }

    private func lastComma(_ s: String) -> Int {
        let chars = Array(s)
        for i in stride(from: chars.count - 1, through: 0, by: -1) {
            if Self.commas.contains(chars[i]) { return i }
        }
        return -1
    }

    private func emit(_ out: inout [String], raw: String) {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return }
        var piece = stripMarkdown(trimmed)
        if piece.hasSuffix(".") && !trimmed.hasSuffix(".") {
            piece.removeLast()
        }
        if piece.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return }
        out.append(piece)
        first = false
    }

    private func stripMarkdown(_ raw: String) -> String {
        var s = raw
        s = s.replacingOccurrences(of: #"\*\*([^*]+)\*\*"#, with: "$1", options: .regularExpression)
        s = s.replacingOccurrences(of: #"\*([^*]+)\*"#, with: "$1", options: .regularExpression)
        s = s.replacingOccurrences(of: #"\[([^\]]+)\]\([^)]+\)"#, with: "$1", options: .regularExpression)
        s = s.replacingOccurrences(of: #"`([^`]+)`"#, with: "$1", options: .regularExpression)
        s = String(s.unicodeScalars.filter { u in
            let v = u.value
            if (0x1F000...0x1FBFF).contains(v) || (0x2600...0x27BF).contains(v) || (0x2B00...0x2BFF).contains(v) { return false }
            return v != 0xFE0F && v != 0x200D
        })
        s = s.replacingOccurrences(of: #"[ \t]+"#, with: " ", options: .regularExpression)
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

// MARK: - Call Steps State Machine

public enum CallSteps {
    public enum Phase: String, CaseIterable, Equatable, Sendable {
        case ringing, preparing, connecting, listening, hearing, transcribing, waiting, speaking, ended
    }

    public enum EndReason: String, CaseIterable, Equatable, Sendable {
        case declined, missed, hungUp, background, interrupted, quiet, noStt, micDenied, failed
    }

    public enum Event: Equatable, Sendable {
        case accept
        case decline
        case ringTimeout
        case prepared
        case notPossible(EndReason)
        case turnStarted
        case speechStarted
        case speechEnded(Data)
        case speechDropped
        case transcribed(String)
        case transcribeFailed(offline: Bool)
        case firstSentence
        case queueIdle(replyBusy: Bool)
        case replyEnded
        case hangUp
        case background
        case interrupted
        case quietTimeout
    }

    public enum Action: Equatable, Sendable {
        case none
        case openMic
        case closeMic
        case sendAnswered
        case transcribe
        case sendText
        case holdText
        case stopPlayback
        case end
    }

    public struct State: Equatable, Sendable {
        public var phase: Phase
        public var held: String?
        public var replyBusy: Bool
        public var ended: EndReason?

        public init(phase: Phase, held: String? = nil, replyBusy: Bool = false, ended: EndReason? = nil) {
            self.phase = phase
            self.held = held
            self.replyBusy = replyBusy
            self.ended = ended
        }
    }

    public static func step(_ s: State, _ e: Event, bargeIn: Bool = true) -> (State, [Action]) {
        switch e {
        case .hangUp:
            if s.phase != .ended {
                return (State(phase: .ended, ended: .hungUp), [.stopPlayback, .closeMic, .end])
            }
        case .background:
            if s.phase != .ended {
                return (State(phase: .ended, ended: .background), [.stopPlayback, .closeMic, .end])
            }
        case .interrupted:
            if s.phase != .ended {
                return (State(phase: .ended, ended: .interrupted), [.stopPlayback, .closeMic, .end])
            }
        case .quietTimeout:
            if s.phase != .ended {
                return (State(phase: .ended, ended: .quiet), [.stopPlayback, .closeMic, .end])
            }
        default:
            break
        }

        if s.phase == .ended {
            return (s, [])
        }

        switch (s.phase, e) {
        case (.ringing, .accept):
            return (State(phase: .preparing, held: s.held, replyBusy: s.replyBusy), [])
        case (.ringing, .decline):
            return (State(phase: .ended, ended: .declined), [.end])
        case (.ringing, .ringTimeout):
            return (State(phase: .ended, ended: .missed), [.end])

        case (.preparing, .prepared):
            return (State(phase: .connecting, held: s.held, replyBusy: s.replyBusy), [.openMic, .sendAnswered])
        case (.preparing, .notPossible(let why)):
            return (State(phase: .ended, ended: why), [.end])

        case (.connecting, .turnStarted), (.waiting, .turnStarted):
            return (State(phase: .waiting, held: s.held, replyBusy: true), [])

        case (.waiting, .firstSentence):
            return (State(phase: .speaking, held: s.held, replyBusy: s.replyBusy), [])

        case (.speaking, .queueIdle(let busy)):
            return (State(phase: busy ? .waiting : .listening, held: s.held, replyBusy: busy), [])

        case (.waiting, .replyEnded), (.speaking, .replyEnded):
            if s.held != nil {
                return (State(phase: .waiting, held: nil, replyBusy: false), [.sendText])
            }
            return (State(phase: .listening, held: nil, replyBusy: false), [])

        case (.listening, .speechStarted):
            return (State(phase: .hearing, held: s.held, replyBusy: s.replyBusy), [])
        case (.speaking, .speechStarted):
            if bargeIn {
                return (State(phase: .hearing, held: s.held, replyBusy: s.replyBusy), [.stopPlayback])
            }
            return (s, [])
        case (.waiting, .speechStarted):
            return (State(phase: .hearing, held: s.held, replyBusy: s.replyBusy), [])

        case (.hearing, .speechEnded):
            return (State(phase: .transcribing, held: s.held, replyBusy: s.replyBusy), [.transcribe])
        case (.hearing, .speechDropped):
            return (State(phase: s.replyBusy ? .waiting : .listening, held: s.held, replyBusy: s.replyBusy), [])

        case (.transcribing, .transcribed(let text)):
            if text.isEmpty {
                return (State(phase: s.replyBusy ? .waiting : .listening, held: s.held, replyBusy: s.replyBusy), [])
            }
            if s.replyBusy {
                return (State(phase: .waiting, held: text, replyBusy: true), [.holdText])
            }
            return (State(phase: .waiting, held: nil, replyBusy: false), [.sendText])

        case (.listening, .transcribed(let text)), (.hearing, .transcribed(let text)):
            if text.isEmpty {
                return (State(phase: s.replyBusy ? .waiting : .listening, held: s.held, replyBusy: s.replyBusy), [])
            }
            if s.replyBusy {
                return (State(phase: .waiting, held: text, replyBusy: true), [.holdText])
            }
            return (State(phase: .waiting, held: nil, replyBusy: false), [.sendText])

        case (.transcribing, .transcribeFailed):
            return (State(phase: .listening, held: s.held, replyBusy: s.replyBusy), [])

        default:
            return (s, [])
        }
    }
}

// MARK: - Logic Helpers

public enum SpeechLang {
    public static func of(piece: String, appLang: String) -> String {
        language(of: piece, fallback: appLang)
    }

    public static func language(of text: String, fallback: String) -> String {
        var hangul = 0, kana = 0, han = 0, cyrillic = 0, arabic = 0, thai = 0, devanagari = 0, latin = 0
        for u in text.unicodeScalars {
            switch u.value {
            case 0xAC00...0xD7A3, 0x1100...0x11FF, 0x3130...0x318F: hangul += 1
            case 0x3040...0x30FF: kana += 1
            case 0x4E00...0x9FFF, 0x3400...0x4DBF: han += 1
            case 0x0400...0x04FF: cyrillic += 1
            case 0x0600...0x06FF: arabic += 1
            case 0x0E00...0x0E7F: thai += 1
            case 0x0900...0x097F: devanagari += 1
            case 0x41...0x5A, 0x61...0x7A, 0xC0...0x24F: latin += 1
            default: break
            }
        }
        let counts: [(String, Int)] = [("ko", hangul), ("ja", kana), ("zh", han), ("ru", cyrillic), ("ar", arabic), ("th", thai), ("hi", devanagari)]
        guard let best = counts.max(by: { $0.1 < $1.1 }), best.1 > 0 else { return fallback }
        if kana > 0 { return "ja" }
        if best.0 == "zh" && hangul > 0 { return "ko" }
        if best.1 < latin / 2 { return fallback }
        let base = fallback.split(separator: "-").first.map { $0.lowercased() } ?? ""
        if best.0 == "ru" && base == "uk" { return fallback }
        if best.0 == "zh" && base == "zh" { return fallback }
        return best.0
    }

    public static func server(_ tag: String, voice: SpeechView?) -> String? {
        guard let voice = voice, voice.state == "ready", voice.runtime == "ready" else { return nil }
        let primary = tag.components(separatedBy: CharacterSet(charactersIn: "-_"))[0].lowercased()
        return voice.languages.contains(primary) ? primary : nil
    }
}

public enum CallReadiness {
    public enum Need: Equatable, Sendable {
        case micPermission, sttQuestion, voiceQuestion, nothing, impossible
    }

    public static func next(
        stt: VoiceView?,
        voice: SpeechView?,
        micAllowed: Bool,
        phoneRecognizer: Bool,
        askedStt: Bool,
        askedVoice: Bool
    ) -> Need {
        if !micAllowed { return .micPermission }
        if stt?.state == "offered" && !askedStt { return .sttQuestion }
        if CallEars.pick(stt, phoneRecognizer: phoneRecognizer) == .none { return .impossible }
        if !askedVoice && voice != nil && (voice?.state == "offered" || voice?.state == "off") {
            return .voiceQuestion
        }
        return .nothing
    }
}

public enum CallEars {
    public enum Way: Equatable, Sendable { case server, phone, none }

    public static func pick(_ stt: VoiceView?, phoneRecognizer: Bool) -> Way {
        if let stt = stt, stt.state == "ready", stt.runtime == "ready" {
            return .server
        }
        if phoneRecognizer { return .phone }
        return .none
    }
}

public enum CallVoiceLine {
    public static func show(voice: SpeechView?, deviceSpokeThisCall: Bool, dismissed: Bool) -> Bool {
        deviceSpokeThisCall && !dismissed && voice != nil && voice?.state != "ready"
    }
}

public enum CallAvailability {
    public static func of(stt: VoiceView?, micAvailable: Bool, phoneRecognizer: Bool) -> Bool {
        (micAvailable && CallEars.pick(stt, phoneRecognizer: phoneRecognizer) != .none) ||
        (micAvailable && stt != nil && stt?.state == "offered" && stt?.runtime == "ready")
    }
}

// MARK: - Call Timings

public final class CallTimings: @unchecked Sendable {
    public enum Step: Hashable, Sendable {
        case speechEnd, sttDone, turnStarted, firstText, firstSentence, firstAudio, playStart
    }

    private var marks: [Step: Int64] = [:]

    public init() {}

    public func mark(_ step: Step, atMs: Int64) {
        marks[step] = atMs
    }

    public func line() -> String {
        var parts: [String] = []
        func delta(_ from: Step, _ to: Step, _ label: String) {
            if let t0 = marks[from], let t1 = marks[to], t1 >= t0 {
                parts.append("\(label)=\(t1 - t0)")
            }
        }
        delta(.speechEnd, .sttDone, "speech_end→stt")
        delta(.sttDone, .turnStarted, "stt→turn")
        delta(.turnStarted, .firstText, "turn→text")
        delta(.firstText, .firstSentence, "text→sentence")
        delta(.firstSentence, .firstAudio, "sentence→audio")
        delta(.firstAudio, .playStart, "audio→play")

        if let start = marks[.speechEnd], let end = marks[.playStart], end >= start {
            parts.append("total=\(end - start)")
        }
        return parts.joined(separator: " ")
    }
}
