import Foundation

/// A model as people read it: the server's label when it has one, else the id tidied up (Android chat/ModelChooserRules.kt
/// ModelNames). Never the raw id. One copy for the whole app: the chat (chip, chooser, sheet) and 마이 → 모델 use this one.
///
/// `humanize` is the server's own rule (assistant.models.model_label), so a name made here before the first list arrives reads
/// exactly like the server's label that replaces it: "gemini-3.8-flash-medium" -> "Gemini 3.8 Flash (Medium)", "sonnet" ->
/// "Sonnet (latest)", "gpt-oss-120b" -> "GPT-OSS 120B". tests/fixtures/model_labels.json (repo root) holds the cases every copy
/// (server, Android, desktop, iOS) is tested against.
public enum ModelNames {
    private static let acronyms: Set<String> = ["gpt", "glm", "oss", "ai", "vl", "moe", "api"]
    private static let dateTail = try! NSRegularExpression(pattern: "[-_](20[0-9]{6}|20[0-9]{2}-[0-9]{2}-[0-9]{2})$")
    private static let size = try! NSRegularExpression(pattern: "^[0-9]+(\\.[0-9]+)?[bkmtBKMT]$")
    private static let openaiO = try! NSRegularExpression(pattern: "^o[0-9]+$")
    /// The claude CLI's names for the newest model of each family
    private static let latestAliases: Set<String> = ["sonnet", "opus", "haiku"]
    /// A thinking level written at the end of the id: shown in brackets, as the CLIs' own labels do
    private static let tailWords: Set<String> = ["minimal", "low", "medium", "high", "xhigh", "max", "thinking"]

    public static func display(_ id: String, label: String?) -> String {
        let l = (label ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return !l.isEmpty && l != id ? l : humanize(id)
    }

    /// Readable names for a whole list, still told apart: two ids that read the same once tidied (gpt-4o and its dated snapshot)
    /// keep their date, "GPT-4o (2024-08-06)"; anything still equal keeps the name.
    public static func displayAll(_ ids: [String], labels: [String: String]) -> [String: String] {
        var first: [String: String] = [:]
        for id in ids { first[id] = display(id, label: labels[id]) }
        var counts: [String: Int] = [:]
        for v in first.values { counts[v, default: 0] += 1 }
        var out: [String: String] = [:]
        for (id, name) in first {
            if counts[name, default: 0] > 1, let d = dateTailMatch(lastSegment(id.trimmingCharacters(in: .whitespacesAndNewlines))) {
                out[id] = "\(name) (\(dashed(d)))"
            } else {
                out[id] = name
            }
        }
        return out
    }

    private static func digit(_ c: Character) -> Bool { c >= "0" && c <= "9" }

    private static func lastSegment(_ s: String) -> String {
        s.split(separator: "/", omittingEmptySubsequences: false).last.map(String.init) ?? s
    }

    /// The date of a dated snapshot ("20241022", "2024-08-06"), without its leading dash, or nil.
    private static func dateTailMatch(_ s: String) -> String? {
        let ns = s as NSString
        guard let m = dateTail.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return nil }
        return ns.substring(with: m.range(at: 1))
    }

    private static func dashed(_ d: String) -> String {
        guard d.count == 8, d.allSatisfy(digit) else { return d }
        let a = Array(d)
        return String(a[0..<4]) + "-" + String(a[4..<6]) + "-" + String(a[6..<8])
    }

    private static func capitalized(_ w: String) -> String {
        guard let f = w.first else { return w }
        return f.uppercased() + w.dropFirst()
    }

    /// "claude-sonnet-5-5" -> "Claude Sonnet 5.5", "anthropic/claude-3-5-haiku-20241022" -> "Claude 3.5 Haiku", "qwen3:8b" -> "Qwen3 8B",
    /// "claude-opus-4-6-thinking" -> "Claude Opus 4.6 (Thinking)", "gpt-5.1-mini" -> "GPT-5.1 Mini".
    public static func humanize(_ id: String) -> String {
        let trimmed = id.trimmingCharacters(in: .whitespacesAndNewlines)
        var s = trimmed
        if s.hasPrefix("models/") { s = String(s.dropFirst("models/".count)) }
        s = lastSegment(s)
        s = dateTail.stringByReplacingMatches(in: s, range: NSRange(location: 0, length: (s as NSString).length), withTemplate: "")
        if latestAliases.contains(s.lowercased()) { return capitalized(s.lowercased()) + " (latest)" }
        let parts = s.split(whereSeparator: { "-_ :".contains($0) }).map(String.init)
        if parts.isEmpty { return trimmed }
        var merged: [String] = []
        for p in parts {
            // a version written with dashes ("5-5", "3-5"): one or two digits right after a short number
            if let prev = merged.last, p.count <= 2, p.allSatisfy(digit), !prev.isEmpty, prev.count <= 3,
               prev.allSatisfy({ digit($0) || $0 == "." }) {
                merged[merged.count - 1] = "\(prev).\(p)"
            } else {
                merged.append(p)
            }
        }
        var tail = ""
        if merged.count >= 2, let last = merged.last, tailWords.contains(last.lowercased()) {
            merged.removeLast()
            tail = " (" + capitalized(last.lowercased()) + ")"
        }
        var words: [String] = []
        var i = 0
        while i < merged.count {
            if merged[i].lowercased() == "gpt" && i + 1 < merged.count {   // OpenAI writes GPT-5.1, GPT-4o, GPT-OSS
                words.append("GPT-" + word(merged[i + 1]))
                i += 2
            } else {
                words.append(word(merged[i]))
                i += 1
            }
        }
        return words.joined(separator: " ") + tail
    }

    private static func whole(_ r: NSRegularExpression, _ s: String) -> Bool {
        let ns = s as NSString
        guard let m = r.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)) else { return false }
        return m.range.location == 0 && m.range.length == ns.length
    }

    private static func word(_ w: String) -> String {
        if acronyms.contains(w.lowercased()) { return w.uppercased() }
        if whole(size, w) { return w.uppercased() }
        if whole(openaiO, w) { return w }
        if let f = w.first, f.isLetter { return capitalized(w) }
        return w
    }

    /// The chip's shorter form: a leading "Claude " says nothing next to Sonnet / Opus / Haiku.
    public static func short(_ name: String) -> String {
        guard name.hasPrefix("Claude ") else { return name }
        let rest = name.dropFirst("Claude ".count).trimmingCharacters(in: .whitespaces)
        return rest.isEmpty ? name : rest
    }
}
