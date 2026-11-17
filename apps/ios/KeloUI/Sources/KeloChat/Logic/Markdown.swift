import Foundation

/// The Markdown a reply uses (Android chat/Markdown.kt, same rules): headings (h1-h3), paragraphs, block quotes, ordered and
/// unordered lists (nested by indent), tables, fenced code blocks, horizontal rules; inline bold, italic, `code` and links. Parsed
/// into plain data here; MarkdownView draws it (never as HTML).
public enum Markdown {
    public indirect enum Block: Equatable, Sendable {
        case para(String)
        /// level 1...3
        case heading(Int, String)
        case code(String, lang: String)
        case items([Entry])
        case quote([Block])
        case table(header: [String], align: [Align], rows: [[String]])
        case rule
    }

    /// One list entry: `number` is its own number in an ordered list (as written), 0 in a bullet list.
    public struct Entry: Equatable, Sendable {
        public var text: String
        public var depth = 0
        public var ordered = false
        public var number = 0
        public init(_ text: String, depth: Int = 0, ordered: Bool = false, number: Int = 0) {
            self.text = text; self.depth = depth; self.ordered = ordered; self.number = number
        }
    }

    public enum Align: Sendable { case start, center, end }

    public enum Span: Equatable, Sendable {
        case plain(String), code(String), bold(String), italic(String), link(String, url: String)
    }

    private static func re(_ p: String) -> NSRegularExpression { try! NSRegularExpression(pattern: p) }
    private static let inlineRe = re(#"(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*|\[[^\]\n]+\]\(https?://[^\s)]+\)|https?://[^\s<>()\[\]]+)"#)
    private static let linkRe = re(#"^\[([^\]]+)\]\((https?://[^\s)]+)\)$"#)
    private static let bulletRe = re(#"^(\s*)[-*+]\s+(.*)$"#)
    private static let numberRe = re(#"^(\s*)(\d{1,9})[.)]\s+(.*)$"#)
    private static let headingRe = re(#"^\s{0,3}(#{1,6})\s+(.*?)(\s+#+)?\s*$"#)
    private static let ruleRe = re(#"^\s{0,3}([-*_])(\s*\1){2,}\s*$"#)
    private static let quoteRe = re(#"^\s{0,3}>\s?(.*)$"#)
    private static let delimiterRe = re(#"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$"#)
    private static let trailing: Set<Character> = [".", ",", ";", ":", "!", "?", "'", "\""]

    /// Groups of a whole-string match, or nil ("" for a group that did not take part).
    static func match(_ r: NSRegularExpression, _ s: String) -> [String]? {
        let ns = s as NSString
        guard let m = r.firstMatch(in: s, range: NSRange(location: 0, length: ns.length)),
              m.range.location == 0, m.range.length == ns.length else { return nil }
        return (0..<m.numberOfRanges).map { i in
            let rg = m.range(at: i)
            return rg.location == NSNotFound ? "" : ns.substring(with: rg)
        }
    }

    static func matches(_ r: NSRegularExpression, _ s: String) -> Bool { match(r, s) != nil }

    /// Only these open in the browser; anything else stays text.
    public static func isWebUrl(_ url: String) -> Bool {
        let l = url.lowercased()
        return l.hasPrefix("https://") || l.hasPrefix("http://")
    }

    public static func inline(_ text: String) -> [Span] {
        var out: [Span] = []
        let ns = text as NSString
        var at = 0
        for m in inlineRe.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            if m.range.location < at { continue }
            if m.range.location > at { out.append(.plain(ns.substring(with: NSRange(location: at, length: m.range.location - at)))) }
            var tok = ns.substring(with: m.range)
            var end = m.range.location + m.range.length
            let n = (tok as NSString).length
            if tok.hasPrefix("`") && tok.hasSuffix("`") && n > 2 {
                out.append(.code(String(tok.dropFirst().dropLast())))
            } else if tok.hasPrefix("**") && tok.hasSuffix("**") && n > 4 {
                out.append(.bold(String(tok.dropFirst(2).dropLast(2))))
            } else if tok.hasPrefix("*") && tok.hasSuffix("*") && n > 2 {
                out.append(.italic(String(tok.dropFirst().dropLast())))
            } else if tok.hasPrefix("[") {
                if let g = match(linkRe, tok) { out.append(.link(g[1], url: g[2])) } else { out.append(.plain(tok)) }
            } else {
                // a bare address: its trailing punctuation belongs to the sentence
                var trimmed = tok
                while let last = trimmed.last, trailing.contains(last) { trimmed.removeLast() }
                end -= (tok as NSString).length - (trimmed as NSString).length
                tok = trimmed
                out.append(tok.count > "https://".count ? .link(tok, url: tok) : .plain(tok))
            }
            at = end
        }
        if at < ns.length { out.append(.plain(ns.substring(from: at))) }
        return merge(out)
    }

    private static func merge(_ spans: [Span]) -> [Span] {
        var out: [Span] = []
        for s in spans {
            if case .plain(let t) = s, case .plain(let p)? = out.last { out[out.count - 1] = .plain(p + t) } else { out.append(s) }
        }
        return out
    }

    /// The fence a line opens ("```" or "~~~", as long as written), or nil.
    public static func fenceOf(_ line: String) -> String? {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard let ch = t.first, ch == "`" || ch == "~" else { return nil }
        let n = t.prefix { $0 == ch }.count
        return n >= 3 ? String(t.prefix(n)) : nil
    }

    public static func closesFence(_ line: String, _ fence: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard let f = fence.first else { return false }
        return t.count >= fence.count && t.allSatisfy { $0 == f }
    }

    private static func indentOf(_ s: String) -> Int {
        var n = 0
        for ch in s {
            if ch == " " { n += 1 } else if ch == "\t" { n += 4 } else { return n }
        }
        return n
    }

    private static func isTableStart(_ lines: [String], _ i: Int) -> Bool {
        i + 1 < lines.count && lines[i].contains("|") && matches(delimiterRe, lines[i + 1]) &&
            (lines[i + 1].contains("|") || lines[i].trimmingCharacters(in: .whitespaces).hasPrefix("|"))
    }

    /// A table row's cells: outer pipes dropped, split at pipes that are not escaped (\|) or inside `code`.
    public static func cells(_ row: String) -> [String] {
        var t = row.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") && !t.hasSuffix("\\|") { t.removeLast() }
        var out: [String] = []
        var cur = ""
        var code = false
        let chars = Array(t)
        var i = 0
        while i < chars.count {
            let ch = chars[i]
            if ch == "\\" && i + 1 < chars.count && chars[i + 1] == "|" { cur.append("|"); i += 1 }
            else if ch == "`" { code.toggle(); cur.append(ch) }
            else if ch == "|" && !code { out.append(cur.trimmingCharacters(in: .whitespaces)); cur = "" }
            else { cur.append(ch) }
            i += 1
        }
        out.append(cur.trimmingCharacters(in: .whitespaces))
        return out
    }

    private static func alignOf(_ cell: String) -> Align {
        let c = cell.trimmingCharacters(in: .whitespaces)
        if c.hasPrefix(":") && c.hasSuffix(":") { return .center }
        return c.hasSuffix(":") ? .end : .start
    }

    private static func startsBlock(_ lines: [String], _ i: Int) -> Bool {
        let line = lines[i]
        return fenceOf(line) != nil || matches(headingRe, line) || matches(ruleRe, line) || matches(quoteRe, line) ||
            matches(bulletRe, line) || matches(numberRe, line) || isTableStart(lines, i)
    }

    private static func isBlank(_ s: String) -> Bool { s.allSatisfy(\.isWhitespace) }

    public static func blocks(_ text: String) -> [Block] {
        let lines = text.components(separatedBy: "\n")
        var out: [Block] = []
        var para: [String] = []
        func flush() { if !para.isEmpty { out.append(.para(para.joined(separator: "\n"))); para.removeAll() } }
        var i = 0
        while i < lines.count {
            let line = lines[i]
            if let fence = fenceOf(line) {   // fenced block; an unclosed one (still streaming) runs to the end
                flush()
                var info = String(line.trimmingCharacters(in: .whitespaces).dropFirst(fence.count)).trimmingCharacters(in: .whitespaces)
                if let sp = info.firstIndex(of: " ") { info = String(info[..<sp]) }
                let lang = info.trimmingCharacters(in: CharacterSet(charactersIn: "{}."))
                var body: [String] = []
                i += 1
                while i < lines.count && !closesFence(lines[i], fence) { body.append(lines[i]); i += 1 }
                i += 1
                out.append(.code(body.joined(separator: "\n"), lang: lang))
                continue
            }
            if isBlank(line) { flush(); i += 1; continue }
            if let h = match(headingRe, line) {
                flush()
                out.append(.heading(min(h[1].count, 3), h[2].trimmingCharacters(in: .whitespaces)))
                i += 1
                continue
            }
            if matches(ruleRe, line) { flush(); out.append(.rule); i += 1; continue }
            if matches(quoteRe, line) {
                flush()
                var inner: [String] = []
                while i < lines.count, let m = match(quoteRe, lines[i]) { inner.append(m[1]); i += 1 }
                out.append(.quote(blocks(inner.joined(separator: "\n"))))
                continue
            }
            if isTableStart(lines, i) {
                flush()
                let header = cells(lines[i])
                let align = cells(lines[i + 1]).map(alignOf)
                let n = header.count
                var rows: [[String]] = []
                i += 2
                while i < lines.count && !isBlank(lines[i]) && lines[i].contains("|") {
                    let r = cells(lines[i])
                    rows.append((0..<n).map { k in k < r.count ? r[k] : "" })
                    i += 1
                }
                out.append(.table(header: header, align: (0..<n).map { k in k < align.count ? align[k] : .start }, rows: rows))
                continue
            }
            if matches(bulletRe, line) || matches(numberRe, line) {
                flush()
                i = list(lines, i, &out)
                continue
            }
            para.append(line)
            i += 1
        }
        flush()
        return out
    }

    private static let maxDepth = 4

    private static func list(_ lines: [String], _ start: Int, _ out: inout [Block]) -> Int {
        var entries: [Entry] = []
        var indents: [Int] = []
        var i = start
        while i < lines.count {
            let line = lines[i]
            if isBlank(line) { break }
            let number = match(numberRe, line)
            let bullet = number == nil ? match(bulletRe, line) : nil
            if number == nil && bullet == nil {
                if !entries.isEmpty && indentOf(line) >= 2 && !startsBlock(lines, i) {
                    entries[entries.count - 1].text += "\n" + line.trimmingCharacters(in: .whitespaces)
                    i += 1
                    continue
                }
                break
            }
            let indent = indentOf((number ?? bullet!)[1])
            if indents.isEmpty {
                if indent >= 2 { indents.append(0) }
                indents.append(indent)
            } else if indent > indents.last! + 1 {
                if indents.count <= maxDepth { indents.append(indent) }
            } else {
                while indents.count > 1 && indent < indents.last! - 1 { indents.removeLast() }
            }
            let depth = indents.count - 1
            // a numbered list right after a bullet list (or the other way round) is a list of its own
            if depth == 0, let first = entries.first, first.ordered != (number != nil) { break }
            if let number {
                entries.append(Entry(number[3], depth: depth, ordered: true, number: Int(number[2]) ?? 1))
            } else {
                entries.append(Entry(bullet![2], depth: depth))
            }
            i += 1
        }
        out.append(.items(entries))
        return i
    }

    /// The text as a person reads it, without the Markdown marks (복사, 공유, 텍스트 선택).
    public static func plain(_ text: String) -> String { blocks(text).compactMap(plainBlock).joined(separator: "\n\n") }

    private static func plainBlock(_ b: Block) -> String? {
        switch b {
        case .para(let t), .heading(_, let t): return plainInline(t)
        case .code(let t, _): return t
        case .items(let entries):
            return entries.map { e in String(repeating: "  ", count: e.depth) + (e.ordered ? "\(e.number). " : "• ") + plainInline(e.text) }
                .joined(separator: "\n")
        case .quote(let blocks): return blocks.compactMap(plainBlock).joined(separator: "\n\n")
        case .table(let header, _, let rows):
            return ([header] + rows).map { r in r.map(plainInline).joined(separator: " | ") }.joined(separator: "\n")
        case .rule: return nil
        }
    }

    public static func plainInline(_ text: String) -> String {
        inline(text).map { s in
            switch s {
            case .plain(let t), .code(let t), .bold(let t), .italic(let t): return t
            case .link(let t, let url): return t == url ? url : "\(t) (\(url))"
            }
        }.joined()
    }

    /// The text as a voice should read it (SpokenText.from): like `plain`, but a link says only its words, a bare address and code
    /// blocks are left out, list items and table rows end like sentences, emoji and symbols are dropped (Android Markdown.spoken).
    public static func spoken(_ text: String) -> String {
        let cleaned = breakRe.stringByReplacingMatches(in: text, range: NSRange(location: 0, length: (text as NSString).length), withTemplate: "\n\n")
        return blocks(cleaned).compactMap(spokenBlock).joined(separator: "\n\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private static func spokenBlock(_ b: Block) -> String? {
        switch b {
        case .para(let t), .heading(_, let t): return sentence(spokenInline(t))
        case .code: return nil
        case .items(let entries):
            let lines = entries.compactMap { sentence(spokenInline($0.text)) }
            return lines.isEmpty ? nil : lines.joined(separator: "\n")
        case .quote(let blocks):
            let parts = blocks.compactMap(spokenBlock)
            return parts.isEmpty ? nil : parts.joined(separator: "\n\n")
        case .table(let header, _, let rows):
            let lines = ([header] + rows).compactMap { r in sentence(r.map(spokenInline).joined(separator: ", ")) }
            return lines.isEmpty ? nil : lines.joined(separator: "\n")
        case .rule: return nil
        }
    }

    private static func spokenInline(_ text: String) -> String {
        let joined = inline(text).map { s -> String in
            switch s {
            case .plain(let t), .code(let t), .bold(let t), .italic(let t): return t
            case .link(let t, let url): return t == url ? "" : t
            }
        }.joined()
        var out = withoutPictographs(joined)
        out = out.replacingOccurrences(of: "[ \\t]+", with: " ", options: .regularExpression)
        out = out.replacingOccurrences(of: " +([,.!?;:])", with: "$1", options: .regularExpression)
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// `t` with a full stop at its end when it has no closing mark, so a voice pauses between lines; nil when it has no letters or digits.
    private static func sentence(_ t: String) -> String? {
        let s = t.trimmingCharacters(in: .whitespacesAndNewlines)
        guard s.contains(where: { $0.isLetter || $0.isNumber }), let last = s.last else { return nil }
        let ends: Set<Character> = [".", "!", "?", ":", ";", "。", "！", "？", "…", "\"", "”", ")", "」"]
        return ends.contains(last) ? s : s + "."
    }

    /// `t` without emoji and pictographs (the symbol planes, the dingbat and symbol blocks, joiners and variation selectors).
    private static func withoutPictographs(_ t: String) -> String {
        String(String.UnicodeScalarView(t.unicodeScalars.filter { u in
            let v = u.value
            if (0x1F000...0x1FBFF).contains(v) || (0x2600...0x27BF).contains(v) || (0x2B00...0x2BFF).contains(v) { return false }
            return v != 0xFE0F && v != 0x200D
        }))
    }

    private static let breakTag = "<message_break/>"
    private static let breakRe = re(#"<message_break\s*/?>"#)

    /// Split one reply into chat bubbles at <message_break/>; hides a half-typed tag while the reply streams.
    public static func splitBubbles(_ text: String) -> [String] {
        var t = text
        for n in stride(from: min(breakTag.count - 1, t.count), through: 1, by: -1) where t.hasSuffix(String(breakTag.prefix(n))) {
            t = String(t.dropLast(n))
            break
        }
        let ns = t as NSString
        var parts: [String] = []
        var at = 0
        for m in breakRe.matches(in: t, range: NSRange(location: 0, length: ns.length)) {
            parts.append(ns.substring(with: NSRange(location: at, length: m.range.location - at)))
            at = m.range.location + m.range.length
        }
        parts.append(ns.substring(from: at))
        return parts.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
    }
}
