import SwiftUI
import KeloShared

/// Draws parsed Markdown blocks natively (Android chat/MarkdownText.kt): headings, paragraphs, lists, quotes, tables (scroll
/// sideways when wide), code blocks (scroll sideways, monospaced), rules; links open in the browser. Never HTML.
struct MarkdownView: View {
    let blocks: [Markdown.Block]
    @Environment(\.kelo) private var c

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, b in
                MarkdownBlockView(block: b)
            }
        }
    }
}

private struct MarkdownBlockView: View {
    let block: Markdown.Block
    @Environment(\.kelo) private var c

    var body: some View {
        switch block {
        case .para(let t):
            Text(MarkdownInline.attributed(t, c: c)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                .fixedSize(horizontal: false, vertical: true)
        case .heading(let level, let t):
            Text(MarkdownInline.attributed(t, c: c))
                .font(level == 1 ? KeloFont.titleLarge : level == 2 ? KeloFont.titleMedium : KeloFont.titleSmall)
                .foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
        case .code(let t, let lang):
            // the language (or "text") and 복사 on top, the code under it scrolling sideways (Android MarkdownText CodeBlock)
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 4) {
                    Text(lang.isEmpty ? L(R.string.md_code_plain) : lang).font(.system(.caption, design: .monospaced)).foregroundStyle(c.muted)
                        .lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                    CopyButton(text: t)
                }
                .padding(.leading, 12).padding(.trailing, 4)
                .background(c.line.opacity(0.5))
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(t).font(.system(.footnote, design: .monospaced)).foregroundStyle(c.ink)
                        .textSelection(.enabled)
                        .padding(.horizontal, 12).padding(.vertical, 10)
                }
            }
            .background(c.card)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(c.line, lineWidth: 1))
        case .items(let entries):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(entries.enumerated()), id: \.offset) { _, e in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(e.ordered ? "\(e.number)." : L([R.string.chat_bullet, R.string.md_bullet_2, R.string.md_bullet_3][e.depth % 3])).font(KeloFont.bodyLarge).foregroundStyle(c.muted)
                        Text(MarkdownInline.attributed(e.text, c: c)).font(KeloFont.bodyLarge).foregroundStyle(c.ink)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.leading, CGFloat(e.depth) * 16)
                }
            }
        case .quote(let inner):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 2).fill(c.line).frame(width: 3)
                MarkdownView(blocks: inner).foregroundStyle(c.muted)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .table(let header, let align, let rows):
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 6) {
                    GridRow {
                        ForEach(Array(header.enumerated()), id: \.offset) { i, h in
                            Text(MarkdownInline.attributed(h, c: c)).font(KeloFont.button).foregroundStyle(c.ink)
                                .gridColumnAlignment(gridAlign(i < align.count ? align[i] : .start))
                        }
                    }
                    Divider().overlay(c.line).gridCellUnsizedAxes(.horizontal)
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, r in
                        GridRow {
                            ForEach(Array(r.enumerated()), id: \.offset) { _, cell in
                                Text(MarkdownInline.attributed(cell, c: c)).font(KeloFont.body).foregroundStyle(c.ink)
                                    .frame(maxWidth: 260, alignment: .leading)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
                .padding(.vertical, 2)
            }
        case .rule:
            Rectangle().fill(c.line).frame(height: 1).padding(.vertical, 4)
        }
    }

    private func gridAlign(_ a: Markdown.Align) -> HorizontalAlignment {
        switch a { case .start: .leading; case .center: .center; case .end: .trailing }
    }
}

enum MarkdownInline {
    /// Inline spans as one attributed string: bold, italic, monospaced code on a tint, and links (web addresses only).
    static func attributed(_ text: String, c: KeloColors) -> AttributedString {
        var out = AttributedString()
        for span in Markdown.inline(text) {
            switch span {
            case .plain(let t):
                out += AttributedString(t)
            case .bold(let t):
                var a = AttributedString(t)
                a.inlinePresentationIntent = .stronglyEmphasized
                out += a
            case .italic(let t):
                var a = AttributedString(t)
                a.inlinePresentationIntent = .emphasized
                out += a
            case .code(let t):
                var a = AttributedString(t)
                a.font = .system(.footnote, design: .monospaced)
                a.backgroundColor = c.line.opacity(0.6)
                out += a
            case .link(let t, let url):
                var a = AttributedString(t)
                if Markdown.isWebUrl(url), let u = URL(string: url) {
                    a.link = u
                    a.foregroundColor = c.accent
                    a.underlineStyle = .single
                }
                out += a
            }
        }
        return out
    }
}

/// Lays children out in rows, wrapping onto the next row instead of squeezing (Android FlowRow): long words stay whole.
struct FlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowH: CGFloat = 0, maxW: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(ProposedViewSize(width: width, height: nil))
            if x > 0 && x + size.width > width { y += rowH + lineSpacing; x = 0; rowH = 0 }
            x += size.width + spacing
            rowH = max(rowH, size.height)
            maxW = max(maxW, x - spacing)
        }
        return CGSize(width: proposal.width ?? maxW, height: y + rowH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowH: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(ProposedViewSize(width: bounds.width, height: nil))
            if x > bounds.minX && x + size.width > bounds.maxX { y += rowH + lineSpacing; x = bounds.minX; rowH = 0 }
            s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: min(size.width, bounds.width), height: size.height))
            x += size.width + spacing
            rowH = max(rowH, size.height)
        }
    }
}

/// 복사: puts `text` on the clipboard; the icon turns into a check (and the label into 복사됨) for a moment.
struct CopyButton: View {
    let text: String
    @Environment(\.kelo) private var c
    @State private var copied = false

    var body: some View {
        Button {
            Clipboard.copy(text)
            Haptics.tick()
            copied = true
            Task { try? await Task.sleep(nanoseconds: 1_500_000_000); copied = false }
        } label: {
            HStack(spacing: 4) {
                KeloIconView(copied ? .check : .copy, size: 14, tint: c.muted)
                Text(L(copied ? R.string.md_copied : R.string.md_copy)).font(KeloFont.small).foregroundStyle(c.muted).lineLimit(1)
            }
            .padding(.horizontal, 8).padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
