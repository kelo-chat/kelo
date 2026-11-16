import Testing
import Foundation

/// A field that holds one value (an address, a key, a token, a model id, a code, an e-mail, a name, a number) never wraps: it is one
/// line that scrolls sideways and its hint is one line; its button sits under it, never squeezed beside it. Every field of the app's
/// sources is checked here (as Android's and the desktop's SingleLineFieldsTest do), so a new screen that breaks this fails the tests.
///
/// The rules:
///  1. a multi-line field (`axis: .vertical`) is message-like: its line names a note, a prompt, a comment, an answer, a reply, a
///     message, a review or pasted JSON;
///  2. a raw TextField / SecureField (outside KeloTextField and the chat composer) is held to one line (`.lineLimit(1)` near it, or a
///     `field(...)` helper of its file that applies it), or is a system alert's field;
///  3. an HStack never holds a field and a text button (PrimaryButton, SecondaryButton) side by side;
///  4. KeloTextField keeps a one-value field at `.lineLimit(axis == .horizontal ? 1 : nil)`.
enum FieldWrapScan {
    struct Problem: CustomStringConvertible {
        let file: String, line: Int, rule: String
        var description: String { "\(file):\(line)  \(rule)" }
    }

    static let message = try! NSRegularExpression(pattern: "note|prompt|comment|answer|reply|message|review|json", options: .caseInsensitive)

    static func matches(_ re: NSRegularExpression, _ s: String) -> Bool {
        re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// The text inside the braces that open at `open`, or nil.
    static func block(_ chars: [Character], from open: Int) -> (String, Int)? {
        guard open < chars.count, chars[open] == "{" else { return nil }
        var depth = 0, i = open
        while i < chars.count {
            if chars[i] == "{" { depth += 1 }
            if chars[i] == "}" { depth -= 1; if depth == 0 { return (String(chars[(open + 1)..<i]), i) } }
            i += 1
        }
        return nil
    }

    static func scan(file name: String, source: String) -> [Problem] {
        var out: [Problem] = []
        let lines = source.components(separatedBy: "\n")
        let isComponent = name.hasSuffix("Design/Components.swift")
        let isComposer = name.hasSuffix("ComposerField.swift")
        for (i, line) in lines.enumerated() {
            let code = line.components(separatedBy: "//").first ?? line
            let around = lines[max(0, i - 2)...min(lines.count - 1, i + 2)].joined(separator: "\n")
            if code.contains("axis: .vertical") && !isComponent && !isComposer && !matches(message, around) {
                out.append(Problem(file: name, line: i + 1, rule: "a field that holds one value is multi-line (axis: .vertical)"))
            }
            let raw = code.range(of: #"(?<![A-Za-z])(TextField|SecureField)\("#, options: .regularExpression) != nil
            if raw && !isComponent && !isComposer {
                let window = lines[max(0, i - 6)...min(lines.count - 1, i + 6)].joined(separator: "\n")
                let helper = (code.contains("field(TextField") || code.contains("field(SecureField")) && source.contains("f.lineLimit(1)")
                if !window.contains(".lineLimit(1)") && !window.contains(".alert(") && !helper {
                    out.append(Problem(file: name, line: i + 1, rule: "a raw TextField / SecureField without .lineLimit(1): use KeloTextField"))
                }
            }
        }
        // rule 3: a field and a text button side by side
        let chars = Array(source)
        var idx = 0
        let text = source
        while let r = text.range(of: #"HStack(\([^)]*\))?\s*\{"#, options: .regularExpression, range: text.index(text.startIndex, offsetBy: idx)..<text.endIndex) {
            let openAt = text.distance(from: text.startIndex, to: r.upperBound) - 1
            idx = openAt + 1
            guard let (body, _) = block(chars, from: openAt) else { continue }
            let field = body.contains("KeloTextField(") || body.range(of: #"(?<![A-Za-z])(TextField|SecureField)\("#, options: .regularExpression) != nil
            let multi = body.contains("axis: .vertical")
            let button = body.range(of: #"(?<![A-Za-z])(PrimaryButton|SecondaryButton)\("#, options: .regularExpression) != nil
            if field && button && !multi {
                let line = text[..<r.lowerBound].filter { $0 == "\n" }.count + 1
                out.append(Problem(file: name, line: line, rule: "a one-value field and a text button side by side in an HStack: put the button under the field"))
            }
        }
        if isComponent {
            if let r = source.range(of: "public struct KeloTextField") {
                let rest = String(source[r.lowerBound...].prefix(4000))
                if !rest.contains(".lineLimit(axis == .horizontal ? 1 : nil)") {
                    out.append(Problem(file: name, line: 1, rule: "KeloTextField must keep a one-value field at one line"))
                }
            } else {
                out.append(Problem(file: name, line: 1, rule: "KeloTextField is missing"))
            }
        }
        return out
    }

    static var sources: URL {
        URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("Sources")
    }
}

@Suite struct SingleLineFieldsTests {
    @Test func noOneValueFieldCanWrap() throws {
        let root = FieldWrapScan.sources
        let files = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil)!
            .compactMap { $0 as? URL }.filter { $0.pathExtension == "swift" }
        #expect(files.count > 50)
        var problems: [FieldWrapScan.Problem] = []
        for f in files {
            let name = f.path.replacingOccurrences(of: root.path + "/", with: "")
            problems += FieldWrapScan.scan(file: name, source: try String(contentsOf: f, encoding: .utf8))
        }
        #expect(problems.isEmpty, "one-value fields that can wrap or are squeezed beside a button:\n\(problems.map(\.description).joined(separator: "\n"))")
    }

    @Test func theScanCatchesAWrappingFieldAndASqueezedButton() {
        let bad = """
        struct V: View {
            var body: some View {
                KeloTextField(L(R.string.model_url_placeholder), text: $url, axis: .vertical)
                TextField("", text: $key)
                HStack(spacing: 8) {
                    KeloTextField(L(R.string.model_key_paste), text: $key)
                    PrimaryButton(L(R.string.save)) { save() }
                }
                KeloTextField(L(R.string.goals_note_placeholder), text: $note, axis: .vertical)
            }
        }
        """
        let p = FieldWrapScan.scan(file: "Bad.swift", source: bad)
        #expect(p.count == 3, "\(p)")
        #expect(!p.contains { $0.line == 9 })   // a note may grow
    }
}
