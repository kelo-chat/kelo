import Foundation

/// What a reply sounds like when it is read aloud (Android chat/ReadAloud.kt SpokenText): plain sentences, the language they are in,
/// and the finger rules of the held mic (Android voice/HoldToTalk.kt).
public enum SpokenText {
    /// `markdown` as sentences for a voice: no marks, link addresses, code blocks or emoji; list items and table rows end like sentences.
    public static func from(_ markdown: String) -> String { Markdown.spoken(markdown) }

    /// The BCP-47 language of `text`, from its writing system where that tells (Hangul, kana, Han, Cyrillic, Arabic, Thai, Devanagari);
    /// a text in the Latin alphabet gets `fallback` (the app's language), as the script cannot tell English from French.
    public static func language(_ text: String, fallback: String) -> String {
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
        // Japanese is written with Han characters too: any kana makes it Japanese; Han beside Hangul is Korean
        if kana > 0 { return "ja" }
        if best.0 == "zh" && hangul > 0 { return "ko" }
        // a word or two in another script inside a Latin text does not change its language
        if best.1 < latin / 2 { return fallback }
        let base = fallback.split(separator: "-").first.map { $0.lowercased() } ?? ""
        if best.0 == "ru" && base == "uk" { return fallback }
        if best.0 == "zh" && base == "zh" { return fallback }
        return best.0
    }

    /// `text` in pieces of at most `max` characters, cut at a sentence end, else a line end or a space.
    public static func chunks(_ text: String, max: Int = 3500) -> [String] {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.isEmpty { return [] }
        var out: [String] = []
        var rest = Substring(t)
        let stops: Set<Character> = [".", "!", "?", "。", "！", "？", "\n"]
        while rest.count > max {
            let window = rest.prefix(max)
            var cut = window.lastIndex(where: { stops.contains($0) }).map { window.distance(from: window.startIndex, to: $0) } ?? -1
            if cut < max / 2 { cut = window.lastIndex(of: " ").map { window.distance(from: window.startIndex, to: $0) } ?? -1 }
            if cut < max / 2 { cut = max - 1 }
            let end = rest.index(rest.startIndex, offsetBy: cut + 1)
            out.append(String(rest[..<end]).trimmingCharacters(in: .whitespacesAndNewlines))
            rest = Substring(String(rest[end...]).trimmingCharacters(in: .whitespacesAndNewlines))
        }
        if !rest.isEmpty { out.append(String(rest)) }
        return out.filter { !$0.isEmpty }
    }
}

/// The held mic's finger rules (Android voice/HoldToTalk.kt, same behaviour): a short touch is a tap (start / stop); holding it for
/// `holdMs` starts dictating; letting go on the button finishes (the words stay in the field, nothing is sent); letting go after
/// sliding off the button cancels (the field goes back to what it was).
public enum HoldToTalk {
    /// How long the finger must stay down before it is a hold and not a tap.
    public static let holdMs = 350

    public enum Phase: Equatable, Sendable {
        case idle
        /// finger down, not yet a hold
        case pressed
        /// finger down while listening already (started by a tap): a lift is the tap that stops it
        case pressedListening
        /// held: dictating, the finger is on the button
        case holding
        /// held: dictating, the finger slid off the button, so lifting cancels
        case holdingAway

        public var isHeld: Bool { self == .holding || self == .holdingAway }
    }

    public enum Event: Equatable, Sendable {
        case down(listening: Bool)
        /// the finger moved; `onButton`: whether it is still on the button
        case move(onButton: Bool)
        /// `holdMs` passed with the finger down
        case holdElapsed
        case up
        /// the system took the touch away
        case cancel
    }

    public enum Action: Equatable, Sendable {
        case none
        /// a short touch: start listening, or stop when it listens
        case tap
        /// start dictating now (the finger stays down)
        case start
        /// stop listening and keep the words
        case finish
        /// stop listening and drop the words
        case cancel
    }

    public struct Step: Equatable, Sendable {
        public var phase: Phase
        public var action: Action
        public init(_ phase: Phase, _ action: Action = .none) { self.phase = phase; self.action = action }
    }

    public static func step(_ phase: Phase, _ event: Event) -> Step {
        switch event {
        case .down(let listening): return Step(listening ? .pressedListening : .pressed)
        case .move(let onButton):
            switch phase {
            case .holding, .holdingAway: return Step(onButton ? .holding : .holdingAway)
            // sliding off before it became a hold: nothing happens at all
            case .pressed, .pressedListening: return onButton ? Step(phase) : Step(.idle)
            case .idle: return Step(.idle)
            }
        case .holdElapsed: return phase == .pressed ? Step(.holding, .start) : Step(phase)
        case .up:
            switch phase {
            case .pressed, .pressedListening: return Step(.idle, .tap)
            case .holding: return Step(.idle, .finish)
            case .holdingAway: return Step(.idle, .cancel)
            case .idle: return Step(.idle)
            }
        case .cancel: return phase.isHeld ? Step(.idle, .cancel) : Step(.idle)
        }
    }

    /// Whether a touch at (`x`, `y`) is still on a button of `width` x `height`; `slack` around it still counts as on it.
    public static func onButton(x: Double, y: Double, width: Double, height: Double, slack: Double) -> Bool {
        x >= -slack && x <= width + slack && y >= -slack && y <= height + slack
    }
}

/// Why dictation ended with an error, from the recognizer's error (domain and code): what the owner is told in one line.
public enum SpeechFailure: Equatable, Sendable {
    case none, noMatch, offline, failed

    /// `heard`: words came in before the error (an error after words is just the end, unless the connection went).
    public static func classify(domain: String, code: Int, heard: Bool) -> SpeechFailure {
        let networkCodes: Set<Int> = [-1001, -1003, -1004, -1005, -1009, -1018, -1200]   // NSURLError: timed out, no host, no connect, lost, offline, ...
        if domain == NSURLErrorDomain && networkCodes.contains(code) { return .offline }
        // the recognizer's own "stopped by the app" and "no speech detected"
        if domain == "kAFAssistantErrorDomain" {
            if code == 301 { return .none }
            if code == 1110 { return heard ? .none : .noMatch }
        }
        return heard ? .none : .failed
    }
}
