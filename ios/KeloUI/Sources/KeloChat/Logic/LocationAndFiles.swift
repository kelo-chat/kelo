import Foundation
import KeloCore
import KeloShared

/// What the location card does for each tap (Android chat/LocationAsk.kt LocationAskRules; pure, so it is tested).
public enum LocationAskRules {
    public enum Step: Equatable, Sendable { case send, askSystem, openSettings, servicesOff }

    /// "Share my location": with the permission, read and send; without it, the system prompt, unless the server says the owner
    /// refused before (`settings`) or iOS will not ask again (`blocked`: denied or restricted): then Kelo's page in Settings.
    /// Location Services off for the whole phone come first (`servicesOff`): apps cannot open that page, so the
    /// card shows the way there.
    public static func onShare(granted: Bool, settings: Bool, blocked: Bool, servicesOn: Bool = true) -> Step {
        if !servicesOn { return .servicesOff }
        if granted { return .send }
        if settings || blocked { return .openSettings }
        return .askSystem
    }

    /// The one line an ended card leaves, or nil while it is open.
    public static func endLine(_ state: String) -> StringKey? {
        switch state {
        case ChatItem.LocationAsk.allowed: R.string.location_ask_shared
        case ChatItem.LocationAsk.refused: R.string.location_ask_refused
        case ChatItem.LocationAsk.waiting, ChatItem.LocationAsk.sending: nil
        default: R.string.location_ask_expired
        }
    }
}

/// How a user's message with files is drawn (Android chat/MessageFiles.kt MessageFileRules; pure, so it is tested).
public enum MessageFileRules {
    /// The bubble's text: nothing when it is the app's own sentence (`autoText`) and the files show; else the typed text without the
    /// lines the app added for each file (`line` of its path or name), since the files show as pictures and chips.
    public static func shownText(_ text: String, files: [MessageFile], autoText: Bool, line: (String) -> String) -> String {
        if files.isEmpty { return text }
        if autoText { return "" }
        let added = Set(files.flatMap { [line($0.path), line($0.name)] })
        return text.components(separatedBy: "\n").filter { !added.contains($0.trimmingCharacters(in: .whitespaces)) }
            .joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// A file name for a one-line chip: a long one is cut in the middle so its end and extension stay ("family-trip-budge…d-v3.pdf",
    /// at most `max` columns; Hangul, CJK, kana and full-width forms take two, as Android `MessageFileRules.chipName`), so a Korean
    /// name is cut once and never a second time by the text view. VoiceOver reads the whole name.
    public static func chipName(_ name: String, max: Int = 26) -> String {
        if columns(name) <= max { return name }
        let chars = Array(name)
        let dot = chars.lastIndex(of: ".") ?? -1
        var tail = Array(chars.suffix(Swift.max(Swift.min(chars.count - Swift.max(dot, chars.count - 10), 10), 8)))
        while columns(String(tail)) > max / 2, !tail.isEmpty { tail.removeFirst() }
        let budget = max - 1 - columns(String(tail))
        var head = ""
        var used = 0
        for c in chars {
            let w = width(c)
            if used + w > budget { break }
            head.append(c); used += w
        }
        return head + "…" + String(tail)
    }

    /// Display columns: 2 for Hangul, CJK, kana and full-width forms, else 1.
    public static func columns(_ s: String) -> Int { s.reduce(0) { $0 + width($1) } }

    static func width(_ c: Character) -> Int {
        guard let v = c.unicodeScalars.first?.value else { return 1 }
        switch v {
        case 0x1100...0x115F, 0x2E80...0xA4CF, 0xAC00...0xD7A3, 0xF900...0xFAFF, 0xFE30...0xFE4F, 0xFF00...0xFF60, 0xFFE0...0xFFE6: return 2
        default: return 1
        }
    }

    /// Rows of the picture grid: one big picture alone, else two a row.
    public static func rows(_ pictures: Int) -> [Int] {
        if pictures <= 0 { return [] }
        if pictures == 1 { return [1] }
        return Array(repeating: 2, count: pictures / 2) + (pictures % 2 == 1 ? [1] : [])
    }

    /// A lone picture's side, and each one's when there are more (points).
    public static let single: CGFloat = 200
    public static let grid: CGFloat = 104
}
