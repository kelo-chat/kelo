import Testing
@testable import KeloChat

private typealias Block = Markdown.Block
private typealias Entry = Markdown.Entry
private typealias Span = Markdown.Span

/// Ports of the Android app's MarkdownTest / IncrementalBlocksTest, same inputs and expectations.
@Suite struct MarkdownTests {
    @Test func splitBubbles() {
        #expect(Markdown.splitBubbles("a<message_break/>b") == ["a", "b"])
        #expect(Markdown.splitBubbles("a <message_break /> b<message_break>") == ["a", "b"])
        #expect(Markdown.splitBubbles("a<message_br") == ["a"])
        #expect(Markdown.splitBubbles("  ") == [])
    }

    @Test func inlineSpans() {
        #expect(Markdown.inline("a **b** *c* `d` [e](https://x.y/z)") == [
            .plain("a "), .bold("b"), .plain(" "), .italic("c"), .plain(" "), .code("d"), .plain(" "), .link("e", url: "https://x.y/z"),
        ])
        #expect(Markdown.inline("[e](javascript:x)") == [.plain("[e](javascript:x)")])
    }

    @Test func blocks() {
        #expect(Markdown.blocks("intro\nmore\n\n- one\n* two\n1. first\n2) second\n```\ncode\n```\n# head\n```\nopen") == [
            .para("intro\nmore"),
            .items([Entry("one"), Entry("two")]),
            .items([Entry("first", ordered: true, number: 1), Entry("second", ordered: true, number: 2)]),
            .code("code", lang: ""),
            .heading(1, "head"),
            .code("open", lang: ""),
        ])
    }

    @Test func headings() {
        #expect(Markdown.blocks("# One\n## Two ##\n### Three\n#### Four\n#nospace") == [
            .heading(1, "One"), .heading(2, "Two"), .heading(3, "Three"), .heading(3, "Four"), .para("#nospace"),
        ])
        #expect(Markdown.blocks("text\n## H") == [.para("text"), .heading(2, "H")])
    }

    @Test func quotes() {
        #expect(Markdown.blocks("> a\n>b\n>\n> - c\n\nafter") == [.quote([.para("a\nb"), .items([Entry("c")])]), .para("after")])
        #expect(Markdown.blocks("> > deep") == [.quote([.quote([.para("deep")])])])
    }

    @Test func nestedLists() {
        #expect(Markdown.blocks("- a\n  - b\n    1. c\n    2. d\n  - e\n- f\n  more of f") == [.items([
            Entry("a"), Entry("b", depth: 1), Entry("c", depth: 2, ordered: true, number: 1), Entry("d", depth: 2, ordered: true, number: 2),
            Entry("e", depth: 1), Entry("f\nmore of f"),
        ])])
        #expect(Markdown.blocks("3. x\n\n4. y") == [.items([Entry("x", ordered: true, number: 3)]), .items([Entry("y", ordered: true, number: 4)])])
        #expect(Markdown.blocks("* a\n\t* b") == [.items([Entry("a"), Entry("b", depth: 1)])])
    }

    @Test func tables() {
        #expect(Markdown.blocks("| Name | Qty | Note |\n|:--|:-:|--:|\n| `a|b` | 2 |\n| c \\| d | 3 | x | extra |\n\nafter") == [
            .table(header: ["Name", "Qty", "Note"], align: [.start, .center, .end], rows: [["`a|b`", "2", ""], ["c | d", "3", "x"]]),
            .para("after"),
        ])
        #expect(Markdown.blocks("| a | b |") == [.para("| a | b |")])
        #expect(Markdown.blocks("a | b\n--- | ---\n1 | 2") == [.table(header: ["a", "b"], align: [.start, .start], rows: [["1", "2"]])])
    }

    @Test func codeFencesKeepTheirLanguage() {
        #expect(Markdown.blocks("```kotlin\nval x = 1\n\nprintln(x)\n```\n````\n~~~ inside\n````\n~~~py\nplain\n~~~") == [
            .code("val x = 1\n\nprintln(x)", lang: "kotlin"), .code("~~~ inside", lang: ""), .code("plain", lang: "py"),
        ])
        let b = Markdown.blocks("- a\n  ```sh\n  x\n  ```")
        #expect(b.count == 2)
        #expect(b.first == .items([Entry("a")]))
        if case .code(let t, let lang) = b[1] { #expect(t.trimmingCharacters(in: .whitespaces) == "x" && lang == "sh") } else { Issue.record("no code") }
    }

    @Test func rules() {
        #expect(Markdown.blocks("a\n---\nb\n* * *\n___") == [.para("a"), .rule, .para("b"), .rule, .rule])
        #expect(Markdown.blocks("- -") == [.items([Entry("-")])])
    }

    @Test func links() {
        #expect(Markdown.inline("see https://a.b/c?d=1. and [x](http://x.y)") == [
            .plain("see "), .link("https://a.b/c?d=1", url: "https://a.b/c?d=1"), .plain(". and "), .link("x", url: "http://x.y"),
        ])
        #expect(Markdown.inline("(at https://q.r)") == [.plain("(at "), .link("https://q.r", url: "https://q.r"), .plain(")")])
        #expect(Markdown.inline("[e](mailto:a@b.c) ftp://x javascript:alert(1)") == [.plain("[e](mailto:a@b.c) ftp://x javascript:alert(1)")])
        #expect(Markdown.isWebUrl("HTTPS://x"))
        #expect(!Markdown.isWebUrl("intent://x"))
    }

    @Test func plainTextDropsTheMarks() {
        let md = "# Title\n\nSome **bold** and `code`, see [docs](https://d.io) or https://x.y.\n\n- a\n  1. b\n\n> quoted\n\n| h1 | h2 |\n|---|---|\n| c | d |\n\n---\n\n```kt\nval x = 1\n```"
        #expect(Markdown.plain(md) == "Title\n\nSome bold and code, see docs (https://d.io) or https://x.y.\n\n• a\n  1. b\n\nquoted\n\nh1 | h2\nc | d\n\nval x = 1")
    }

}
