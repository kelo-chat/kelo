import SwiftUI
import KeloCore
import KeloShared

// What an install or an update puts on the phone, in full (Android my/NestContentsSheet.kt): the notes from loading it, where it
// connects, every file with its size, and the text of each .md file and the manifest (tap a file). Paths and long lines wrap inside
// the sheet (fixedSize vertical, no horizontal scroll); the sheet scrolls vertically only.

/// The "See everything it will install" sheet, on top of the link card.
struct NestContentsSheet: View {
    let contents: NestContents
    @State private var opened: String?
    @Environment(\.kelo) private var c
    private let mono = Font.system(.footnote, design: .monospaced)

    /// `initialOpened`: a file's text to show first (a test lays out the text view; the sheet opens on the list).
    init(contents: NestContents, initialOpened: String? = nil) {
        self.contents = contents
        _opened = State(initialValue: initialOpened)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let path = opened, let text = contents.texts[path] {
                LinkButton(L(R.string.nest_contents_back)) { opened = nil }
                Text(path).font(mono.weight(.semibold)).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                Text(text).font(mono).foregroundStyle(c.ink).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                if NestContentsRules.isCut(contents, path) { MutedText(L(R.string.nest_contents_cut), small: true) }
            } else {
                Text(L(R.string.nest_contents_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
                if !contents.warnings.isEmpty {
                    heading(L(R.string.nest_contents_warnings))
                    ForEach(Array(contents.warnings.enumerated()), id: \.offset) { _, w in
                        Text(w).font(KeloFont.small).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true)
                    }
                }
                heading(L(R.string.nest_contents_endpoints))
                if contents.endpoints.isEmpty { MutedText(L(R.string.nest_contents_none), small: true) }
                ForEach(Array(contents.endpoints.enumerated()), id: \.offset) { _, e in
                    Text(e).font(mono).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
                }
                heading(L(R.string.nest_contents_files))
                ForEach(Array(contents.files.enumerated()), id: \.offset) { _, f in fileRow(f) }
                MutedText(L(R.string.nest_contents_total, sizeText(contents.totalBytes)), small: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
    }

    private func heading(_ text: String) -> some View {
        Text(text).font(KeloFont.titleSmall).foregroundStyle(c.accent)
            .fixedSize(horizontal: false, vertical: true).padding(.top, 8)
    }

    /// A file: its path (monospace) with its size under it; a text file (one the sheet has the text of) opens on tap.
    @ViewBuilder private func fileRow(_ f: NestFile) -> some View {
        let row = VStack(alignment: .leading, spacing: 2) {
            Text(f.path).font(mono).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            MutedText(sizeText(f.bytes), small: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, 2)
        if contents.texts[f.path] != nil {
            Button { opened = f.path } label: { row }.buttonStyle(.plain)
        } else {
            row
        }
    }
}

/// The "What this update changes" sheet of an installed package with changed files: three lists, each only when it has something.
struct NestChangedSheet: View {
    let title: String
    let changed: NestChanged
    @Environment(\.kelo) private var c
    private let mono = Font.system(.footnote, design: .monospaced)

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(L(R.string.nest_changed_title)).font(KeloFont.titleMedium).foregroundStyle(c.ink)
            MutedText(title, small: true)
            pathList(L(R.string.nest_changed_added), changed.added)
            pathList(L(R.string.nest_changed_removed), changed.removed)
            pathList(L(R.string.nest_changed_modified), changed.modified)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
    }

    @ViewBuilder private func pathList(_ heading: String, _ paths: [String]) -> some View {
        if !paths.isEmpty {
            Text(heading).font(KeloFont.titleSmall).foregroundStyle(c.accent)
                .fixedSize(horizontal: false, vertical: true).padding(.top, 8)
            ForEach(Array(paths.enumerated()), id: \.offset) { _, p in
                Text(p).font(mono).foregroundStyle(c.ink).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

/// "N B" under 1 KB, else "N KB" (whole kilobytes, rounded up).
@MainActor fileprivate func sizeText(_ bytes: Int64) -> String {
    switch NestContentsRules.size(bytes) {
    case .bytes(let n): L(R.string.nest_contents_bytes, Int(n))
    case .kilobytes(let n): L(R.string.nest_contents_kb, Int(n))
    }
}
