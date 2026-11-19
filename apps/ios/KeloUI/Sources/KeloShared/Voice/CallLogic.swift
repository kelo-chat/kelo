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
    /// The phone recognizer waits this long after a try that heard nothing, doubling each time, never longer than the max.
    /// The quiet the phone's recognizer is asked to wait for before it ends the owner's words (owner 2026-11-20).
    public static let phoneEndSilenceMs: Int64 = 1_800
    /// A reply event that is an answer (text, a step, a card): after it a call's words are no longer taken back.
    public static func isAnswer(_ ev: TurnEvent) -> Bool {
        switch ev {
        case .thinking, .retry, .reconnecting, .fallback, .userMessage, .reaction, .done, .error, .unknown: return false
        default: return true
        }
    }
    public static let phoneRetryMs: Int64 = 1_000
    public static let phoneRetryMaxMs: Int64 = 8_000
    /// This many recognizer errors in a row show the "ears failed" line on the call screen.
    public static let phoneErrorsNote = 3
    /// Answering waits at most this long for the server voice models to be read.
    public static let modelsWaitMs: Int64 = 3_000
    /// call-mode 5.2: the mic is muted while the call is a pill over the chat (the owner may flip it).
    public static let muteWhenMinimized = true
    /// iPhone only: how long the recognizer waits for a first word before it gives up with nothing heard.
    public static let phoneNoSpeechMs: Int64 = 8_000

    public static func phoneRetryWait(misses: Int) -> Int64 {
        misses <= 0 ? 0 : min(phoneRetryMaxMs, phoneRetryMs << Int64(min(misses, 10) - 1))
    }

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
    private let holdDb: Float
    private let bargeHoldDb: Float
    private let absMinDb: Float
    private let absHoldDb: Float
    private let startMs: Int
    private let bargeStartMs: Int
    private let endMs: Int
    private let pauseExtraMs: Int
    private let maxEndMs: Int
    private let innerPauseMs: Int
    private let resumeMs: Int
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
    private var speechRunMs = 0               // speech in a row inside the utterance
    private var pauseAtRun = 0                // the quiet run that speech just ended
    private var longestPauseMs = 0            // the longest inner pause so far
    private var recentWindows: [Bool] = []

    /// End after a clear pause: 1 s, longer after pauses inside the utterance (at most 1.8 s); hysteresis keeps soft sounds speech.
    public init(
        startDb: Float = 12.0,
        bargeStartDb: Float = 18.0,
        holdDb: Float = 6.0,
        bargeHoldDb: Float = 12.0,
        absMinDb: Float = -50.0,
        absHoldDb: Float = -56.0,
        startMs: Int = 200,
        bargeStartMs: Int = 300,
        endMs: Int = 1000,
        pauseExtraMs: Int = 500,
        maxEndMs: Int = 1800,
        innerPauseMs: Int = 300,
        resumeMs: Int = 60,
        preRollMs: Int = 300,
        maxMs: Int = 60_000,
        minSpeechMs: Int = 300
    ) {
        self.startDb = startDb
        self.bargeStartDb = bargeStartDb
        self.holdDb = holdDb
        self.bargeHoldDb = bargeHoldDb
        self.absMinDb = absMinDb
        self.absHoldDb = absHoldDb
        self.startMs = startMs
        self.bargeStartMs = bargeStartMs
        self.endMs = endMs
        self.pauseExtraMs = pauseExtraMs
        self.maxEndMs = maxEndMs
        self.innerPauseMs = innerPauseMs
        self.resumeMs = resumeMs
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
                speechRunMs = 0
                pauseAtRun = 0
                longestPauseMs = 0
                recentWindows.removeAll()
                preRollWindows.removeAll()
            }
        } else {
            let holds = db >= floor + (speaking ? bargeHoldDb : holdDb) && db >= absHoldDb
            if holds {
                utteranceSpeechMs += 20
                if speechRunMs == 0 { pauseAtRun = utteranceQuietMs }   // the quiet run before this speech
                speechRunMs += 20
                if utteranceQuietMs > 0 {
                    if speechRunMs >= resumeMs {                     // real speech again: that quiet run was a pause inside the utterance
                        if pauseAtRun >= innerPauseMs { longestPauseMs = max(longestPauseMs, pauseAtRun) }
                        utteranceQuietMs = 0
                    } else {
                        utteranceQuietMs += 20                       // too short to tell from a click: still quiet
                    }
                }
            } else {
                floor = max(-75.0, min(-30.0, 0.99 * floor + 0.01 * db))   // slowly: a soft word must not lift the floor over itself
                speechRunMs = 0
                utteranceQuietMs += 20
            }

            utteranceChunks.append(window)
            utteranceTotalMs += 20

            if utteranceQuietMs >= endNeededMs() || utteranceTotalMs >= maxMs {
                finishUtterance(out: &out)
            }
        }
    }

    /// The quiet that ends this utterance: `endMs`, or `pauseExtraMs` more than its longest inner pause, at most `maxEndMs`.
    private func endNeededMs() -> Int {
        longestPauseMs == 0 ? endMs : min(maxEndMs, max(endMs, longestPauseMs + pauseExtraMs))
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
        speechRunMs = 0
        pauseAtRun = 0
        longestPauseMs = 0
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
        speechRunMs = 0
        pauseAtRun = 0
        longestPauseMs = 0
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

/// The first piece is a short phrase so Kelo starts speaking early (owner 2026-11-20); later pieces are sentences, long ones cut at a
/// clause break (the Kotlin SentenceCutter, line for line).
public final class SentenceCutter: @unchecked Sendable {
    private let max: Int
    private var pending: [Character] = []   // text of the current piece (not yet cut)
    private var line = ""
    private var decided = false
    private var lineFence = false
    private var inFence = false
    private var spoken = 0                  // characters given out in this reply
    private var pieces = 0                  // pieces given out in this reply

    private static let ends: Set<Character> = [".", "!", "?", "…", "。", "！", "？"]
    private static let cjkEnds: Set<Character> = ["。", "！", "？"]
    private static let commas: Set<Character> = [",", "、", "，", ";", ":"]
    private static let cjkCommas: Set<Character> = ["、", "，"]
    private static let firstMinChars = 8     // the opening phrase: at least this many characters…
    private static let firstMinWords = 2     // …and words (text with spaces)
    private static let firstWordCut = 32     // no break by then: cut at a word
    private static let firstCutWords = 3     // with at least this many words before it
    private static let secondSoft = 50       // the piece after the opening is cut at a clause break past this
    private static let laterSoft = 120       // later sentences are cut at a clause break past this
    private static let minClause = 20        // a clause cut keeps at least this many characters before it

    public init(max: Int = 300) {
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
                    if !lineFence && !inFence { pending.append(contentsOf: line) }
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
        pending = []; line = ""; decided = false; lineFence = false; inFence = false; spoken = 0; pieces = 0
    }

    private func endLine() {
        if !decided {
            if !inFence { pending.append(contentsOf: line) }
        } else if lineFence {
            inFence.toggle()
        }
        line = ""; decided = false; lineFence = false
    }

    private func flush(_ out: inout [String], final: Bool, newline: Bool) {
        cut(&out, final: final)
        if newline || final {
            emit(&out, raw: String(pending))
            pending = []
        }
    }

    private func cut(_ out: inout [String], final: Bool) {
        while true {
            if spoken < Self.firstMinChars {                    // the opening phrase: out as soon as it is a phrase
                let b = openingBreak(final: final)
                if b >= 0 { take(&out, b + 1); continue }
                if pending.count > Self.firstWordCut {
                    let at = wordCut()
                    if at > 0 { take(&out, at); continue }
                }
            }
            let end = sentenceEnd(final: final)
            let soft = pieces <= 1 ? Self.secondSoft : Self.laterSoft
            let len = end >= 0 ? end + 1 : pending.count
            if len > soft {
                let b = clauseBreak(soft: soft, until: end >= 0 ? end : pending.count, final: final)
                if b >= 0 { take(&out, b + 1); continue }
            }
            if end >= 0 { take(&out, end + 1); continue }
            if pending.count > max {
                var space = -1
                for i in stride(from: min(max, pending.count - 1), through: 0, by: -1) where pending[i] == " " {
                    space = i
                    break
                }
                take(&out, space >= 1 ? space : max)
                continue
            }
            return
        }
    }

    private func take(_ out: inout [String], _ n: Int) {
        emit(&out, raw: String(pending[0..<n]))
        pending.removeFirst(n)
    }

    /// Whether the text may be cut right after the mark at `i`: a CJK mark always; else the next character is known and is a space
    /// ("1,000", "10:30", "3.5" are not cut), and a period after a single letter ("U.S.") is not an end.
    private func breakHere(_ i: Int, final: Bool) -> Bool {
        let c = pending[i]
        if Self.cjkEnds.contains(c) || Self.cjkCommas.contains(c) { return true }
        if i + 1 >= pending.count { return final }
        if !pending[i + 1].isWhitespace { return false }
        return !(c == "." && singleLetterBefore(pending, at: i))
    }

    /// Index of the first sentence end that may be cut, or -1.
    private func sentenceEnd(final: Bool) -> Int {
        for i in pending.indices where Self.ends.contains(pending[i]) && breakHere(i, final: final) { return i }
        return -1
    }

    /// The first place the opening phrase may end within its first `firstWordCut` characters: a sentence end, or a clause break once
    /// the phrase has `firstMinChars` characters and, in text with spaces, `firstMinWords` words. -1: none yet.
    private func openingBreak(final: Bool) -> Int {
        for i in 0..<min(pending.count, Self.firstWordCut) {
            let c = pending[i]
            if !Self.ends.contains(c) && !Self.commas.contains(c) { continue }
            if !breakHere(i, final: final) { continue }
            if Self.ends.contains(c) { return i }
            let phrase = String(pending[0...i]).trimmingCharacters(in: .whitespaces)
            if phrase.count >= Self.firstMinChars && (!phrase.contains(" ") || words(phrase) >= Self.firstMinWords) { return i }
        }
        return -1
    }

    /// No break came in the first `firstWordCut` characters: the last space within them with `firstCutWords` words before it and no
    /// open link, bracket, bold or code mark (never inside a word). 0: none (text without spaces waits for a mark).
    private func wordCut() -> Int {
        var i = min(Self.firstWordCut, pending.count - 1)
        while i >= 1 {
            if pending[i].isWhitespace {
                let before = String(pending[0..<i])
                if words(before) >= Self.firstCutWords && closed(before) { return i }
            }
            i -= 1
        }
        return 0
    }

    /// Where a piece longer than `soft` is cut: the last clause break before `soft` that leaves `minClause` characters, else the first
    /// one after it; only before `until` (the sentence end, or the end of the text so far). -1: none.
    private func clauseBreak(soft: Int, until: Int, final: Bool) -> Int {
        var best = -1
        var i = Self.minClause
        let limit = min(until, pending.count)
        while i < limit {
            if Self.commas.contains(pending[i]) && breakHere(i, final: final) {
                if i < soft { best = i } else { return best >= 0 ? best : i }
            }
            i += 1
        }
        return best
    }

    private func words(_ s: String) -> Int {
        s.split(whereSeparator: { $0 == " " || $0 == "\t" }).count
    }

    /// No `[`, `(`, `**` or backquote left open in `s` (a cut there would break Markdown before it is made plain).
    private func closed(_ s: String) -> Bool {
        s.filter { $0 == "[" }.count == s.filter { $0 == "]" }.count && s.filter { $0 == "(" }.count == s.filter { $0 == ")" }.count &&
            s.components(separatedBy: "**").count % 2 == 1 && s.filter { $0 == "`" }.count % 2 == 0
    }

    /// "U.S.", "A.": a single Latin letter before the period is an abbreviation. A Korean or other one-letter word ("네.") ends.
    private func singleLetterBefore(_ chars: [Character], at i: Int) -> Bool {
        if i < 1 || !chars[i - 1].isLetter || !chars[i - 1].isASCII { return false }
        return i < 2 || !chars[i - 2].isLetter
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
        spoken += piece.count
        pieces += 1
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
        case sendRefused(String, amend: Bool = false)   // the chat could not take the words now
        case questionOpened        // a secret or consent question stopped the call's turn: listen again, words held (2026-11-19)
        case typed(String)         // words typed into the call (the keyboard)
        case answered              // the first answer of this call's turn came (text, a step, a card)
        case takeBackRefused       // the chat could not take back the unanswered turn (it was answered meanwhile)
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
        case takeBack
    }

    /// What a `sendText` sends: `text`, and `amend` = it replaces the call's unanswered words (POST /api/turns "amend").
    public struct Outgoing: Equatable, Sendable {
        public let text: String
        public let amend: Bool
        public init(text: String, amend: Bool) { self.text = text; self.amend = amend }
    }

    public struct State: Equatable, Sendable {
        public var phase: Phase
        public var held: String?            // the owner's words not sent yet (heard or typed)
        public var replyBusy: Bool
        public var ended: EndReason?
        public var inFlight: Int            // pieces of the owner's speech still being turned into text
        public var again: String?           // words of a taken-back turn: sent again in front of `held`, as an amend
        public var unanswered: String?      // the words of the call's last sent turn while nothing of the answer has come
        public var sent: Outgoing?          // what this step's `sendText` sends

        public init(phase: Phase, held: String? = nil, replyBusy: Bool = false, ended: EndReason? = nil, inFlight: Int = 0,
                    again: String? = nil, unanswered: String? = nil, sent: Outgoing? = nil) {
            self.phase = phase
            self.held = held
            self.replyBusy = replyBusy
            self.ended = ended
            self.inFlight = inFlight
            self.again = again
            self.unanswered = unanswered
            self.sent = sent
        }
    }

    public static func step(_ state: State, _ e: Event, bargeIn: Bool = true) -> (State, [Action]) {
        var s = state
        s.sent = nil
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
        return next(s, e, bargeIn: bargeIn)
    }

    private static func next(_ s: State, _ e: Event, bargeIn: Bool) -> (State, [Action]) {
        let p = s.phase
        switch (p, e) {
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
            var n = s; n.phase = .waiting; n.replyBusy = true
            return (n, [])

        case (.waiting, .firstSentence):
            var n = s; n.phase = .speaking
            return (n, [])

        case (.speaking, .queueIdle(let busy)):
            if !busy && words(s) != nil {
                var n = s; n.replyBusy = false
                return send(n)
            }
            var n = s; n.phase = busy ? .waiting : .listening; n.replyBusy = busy
            return (n, [])

        case (.listening, .typed(let text)), (.hearing, .typed(let text)), (.transcribing, .typed(let text)):
            var t = s; t.held = join(s.held, text)
            if t.replyBusy { t.phase = .waiting; return (t, [.holdText]) }
            return send(t)

        case (.waiting, .typed(let text)), (.speaking, .typed(let text)):
            var n = s; n.held = join(s.held, text); n.replyBusy = true
            return (n, [.holdText])

        case (.waiting, .sendRefused(let text, let amend)):
            var n = s
            if amend { n.again = text } else { n.held = join(text, s.held) }
            n.replyBusy = true; n.unanswered = nil
            return (n, [.holdText])

        case (_, .replyEnded):
            return replyEnded(s)

        case (.waiting, .questionOpened):
            var n = s; n.phase = .listening; n.replyBusy = true; n.unanswered = nil
            return (n, [])

        case (_, .answered):
            var n = s; n.unanswered = nil
            return (n, [])

        case (_, .takeBackRefused):
            var n = s; n.again = nil
            return (n, [])

        case (.listening, .speechStarted), (.transcribing, .speechStarted):
            var n = s; n.phase = .hearing
            return (n, [])
        case (.waiting, .speechStarted):   // the owner goes on before Kelo answered: those words come back to be sent with the rest (2026-11-20)
            var n = s; n.phase = .hearing
            if let unanswered = s.unanswered {
                n.again = unanswered; n.unanswered = nil
                return (n, [.takeBack])
            }
            return (n, [])
        case (.speaking, .speechStarted):
            if bargeIn {
                var n = s; n.phase = .hearing
                return (n, [.stopPlayback])
            }
            return (s, [])

        case (.hearing, .speechEnded):
            var n = s; n.phase = .transcribing; n.inFlight = s.inFlight + 1
            return (n, [.transcribe])
        case (.hearing, .speechDropped):
            if s.inFlight > 0 { var n = s; n.phase = .transcribing; return (n, []) }
            return finish(s)

        case (.transcribing, .transcribed(let text)), (.listening, .transcribed(let text)), (.hearing, .transcribed(let text)),
             (.waiting, .transcribed(let text)):
            let takes = p == .transcribing || p == .listening || p == .hearing || (p == .waiting && s.inFlight > 0)
            if !takes { return (s, []) }
            var t = s; t.inFlight = max(0, s.inFlight - 1); t.held = join(s.held, text)
            if p == .hearing && s.inFlight > 0 { return (t, []) }   // the owner talks again: these words wait for the rest
            if t.inFlight > 0 {
                if p != .waiting { t.phase = .transcribing }
                return (t, [])
            }
            return finish(t)

        case (.transcribing, .transcribeFailed), (.hearing, .transcribeFailed), (.waiting, .transcribeFailed):
            let takes = p == .transcribing || p == .hearing || (p == .waiting && s.inFlight > 0)
            if !takes { return (s, []) }
            var t = s; t.inFlight = max(0, s.inFlight - 1)
            if p != .transcribing || t.inFlight > 0 { return (t, []) }
            if words(t) != nil { return finish(t) }
            t.phase = .listening
            return (t, [])

        default:
            return (s, [])
        }
    }

    /// Words joined with one space, blank parts left out; nil when nothing is left.
    public static func join(_ parts: String?...) -> String? {
        let joined = parts.compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }.joined(separator: " ")
        return joined.isEmpty ? nil : joined
    }

    private static func words(_ s: State) -> String? { join(s.again, s.held) }

    private static func idlePhase(_ s: State) -> Phase { s.replyBusy ? .waiting : .listening }

    /// The owner finished: their words go now, or wait for the running reply; nothing heard goes back to idle.
    private static func finish(_ s: State) -> (State, [Action]) {
        if words(s) == nil {
            var n = s; n.phase = idlePhase(s); n.held = nil; n.again = nil
            return (n, [])
        }
        if s.replyBusy {
            var n = s; n.phase = .waiting
            return (n, [.holdText])
        }
        return send(s)
    }

    private static func send(_ s: State) -> (State, [Action]) {
        guard let text = words(s) else {
            var n = s; n.phase = idlePhase(s)
            return (n, [])
        }
        var n = s
        n.phase = .waiting; n.held = nil; n.again = nil; n.unanswered = text
        n.sent = Outgoing(text: text, amend: s.again != nil)
        return (n, [.sendText])
    }

    private static func replyEnded(_ s: State) -> (State, [Action]) {
        var free = s; free.replyBusy = false; free.unanswered = nil
        switch s.phase {
        case .hearing, .transcribing:
            return (free, [])   // the owner's words go once they finish (2026-11-20)
        case .listening, .waiting:
            if words(s) != nil { return send(free) }
            free.phase = .listening
            return (free, [])
        case .speaking:
            if words(s) != nil { return send(free) }   // still playing: queueIdle moves on to listening
            return (free, [])
        default:
            return (s, [])
        }
    }
}

// MARK: - Sound Line

/// The live sound line's shape (the Kotlin CallSoundLineRules). Points are -1...1 offsets from the centre line, left to right.
public enum CallSoundLineRules {
    /// [levels] holds [n] levels oldest to newest (LevelTrail.copyInto). The newest [count] levels give the height at each point; a soft
    /// travelling wave gives the shape. [synthetic]: Kelo speaks with the device voice, which gives no levels: a gentle 0.35 moves the line.
    public static func points(levels: [Float], n: Int, count: Int, timeSec: Float, synthetic: Bool) -> [Float] {
        var out = [Float](repeating: 0, count: max(0, count))
        for i in 0..<out.count {
            let k = n - count + i
            let level: Float
            if synthetic {
                level = 0.35
            } else if k >= 0 && k < n && k < levels.count {
                level = min(max(levels[k], 0), 1)
            } else {
                level = 0
            }
            let wave = sin(Float(i) * 0.55 + timeSec * 7)
            let edge = sin(Float.pi * (Float(i) + 0.5) / Float(count))   // calm at both ends
            out[i] = min(max(level * wave * edge, -1), 1)
        }
        return out
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

/// What the call screen says when Kelo's voice could not be played as planned.
public enum CallVoiceTrouble: Equatable, Sendable { case deviceVoice, noVoice }

/// The short reason a call log line gives for an error (never the text of the error: no user words in the log).
public enum CallLog {
    public static func why(_ e: Error) -> String {
        if let a = e as? ApiError { return "ApiError(\(a.status))" }
        return String(describing: type(of: e))
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
