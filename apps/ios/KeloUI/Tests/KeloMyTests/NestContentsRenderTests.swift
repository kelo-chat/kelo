import Testing
import SwiftUI
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloMy

/// Lays out the contents sheet and the changed sheet at 320 and 412 points with long spaceless paths and long lines (the sheets scroll
/// vertically only, so a line that does not wrap would widen the image). KELO_RENDER_DIR=<folder> writes PNGs to look at.
@Suite(.serialized) struct NestContentsRenderTests {
    @MainActor private func render<V: View>(_ view: V, width: CGFloat, name: String) -> CGSize {
        let content = view.keloTheme().frame(width: width).background(Color.white)
        let r = ImageRenderer(content: content)
        r.scale = 2
        if let dir = ProcessInfo.processInfo.environment["KELO_RENDER_DIR"], let img = r.nsImage,
           let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name)-\(Int(width)).png"))
        }
        return r.nsImage?.size ?? .zero
    }

    private let longPath = String(repeating: "very_long_spaceless_folder_name_", count: 4) + "file.md"
    private var contents: NestContents {
        NestContents(
            files: [NestFile(path: "SKILL.md", bytes: 900), NestFile(path: longPath, bytes: 70_000),
                    NestFile(path: "scripts/한국어_이름이_아주_긴_파일_이름_정말로_길다.py", bytes: 2_048)],
            totalBytes: 72_948,
            texts: ["SKILL.md": String(repeating: "A long line without any break inside it, ", count: 12)],
            cut: ["SKILL.md"],
            endpoints: ["api.example.com", "https://a-very-long-host-name-without-spaces.example.org/some/long/path/segment"],
            warnings: ["A warning that goes on for a while so that it has to wrap to a second line on a phone."])
    }

    @MainActor @Test func theContentsSheetFitsPhoneWidthsWithLongPaths() {
        for width: CGFloat in [320, 412] {
            let list = render(NestContentsSheet(contents: contents), width: width, name: "nest-contents-list")
            #expect(list.width <= width + 0.5 && list.height > 0, "list \(width)")
            let text = render(NestContentsSheet(contents: contents, initialOpened: "SKILL.md"), width: width, name: "nest-contents-text")
            #expect(text.width <= width + 0.5 && text.height > 0, "text \(width)")
            let changed = NestChanged(added: [longPath], removed: ["old.md"], modified: [longPath, "SKILL.md"])
            let sheet = render(NestChangedSheet(title: "Reader", changed: changed), width: width, name: "nest-changed")
            #expect(sheet.width <= width + 0.5 && sheet.height > 0, "changed \(width)")
        }
    }
}
