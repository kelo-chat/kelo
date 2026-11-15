import Testing
import SwiftUI
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// Renders the location card and a message with files at phone widths (375 and 414 points) with long words, to look at
/// (KELO_RENDER_DIR=<folder> writes PNGs; without it the views are still laid out, so a layout that cannot fit fails here first).
@Suite(.serialized) struct LocationAndFilesRenderTests {
    @MainActor private func render<V: View>(_ view: V, width: CGFloat, name: String) -> CGSize {
        let app = AppModel(store: MemorySessionStore(), prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-render-\(UUID().uuidString)")!),
                           transport: FakeTransportForRender(), cache: ScreenCache(directory: nil, key: { nil }))
        let content = view.environment(app).keloTheme().padding(16).frame(width: width).background(Color.white)
        let r = ImageRenderer(content: content)
        r.scale = 2
        if let dir = ProcessInfo.processInfo.environment["KELO_RENDER_DIR"], let img = r.nsImage,
           let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name)-\(Int(width)).png"))
        }
        return r.nsImage?.size ?? .zero
    }

    @MainActor @Test func cardsAndFilesFitAtPhoneWidths() {
        for lang in ["de", "ko"] {
            Strings.shared.use(lang)
            let app = AppModel(store: MemorySessionStore(), prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-r-\(UUID().uuidString)")!),
                               transport: FakeTransportForRender(), cache: ScreenCache(directory: nil, key: { nil }))
            let model = ChatModel(app: app, uploader: FakeUploader())
            let reason = String(repeating: "Um freie Parkplätze in der Nähe zu finden ", count: 3)
            let open = ChatItem.LocationAsk(key: "l", approvalId: "a", reason: reason, settings: true)
            let sending = ChatItem.LocationAsk(key: "l2", approvalId: "b", reason: "", settings: false, state: ChatItem.LocationAsk.sending)
            let ended = ChatItem.LocationAsk(key: "l3", approvalId: "c", state: ChatItem.LocationAsk.refused)
            let files = [MessageFile(name: "a.jpg", path: "uploads/a.jpg", kind: "image"), MessageFile(name: "b.jpg", path: "uploads/b.jpg", kind: "image"),
                         MessageFile(name: "c.mov", path: "uploads/c.mov", kind: "video"),
                         MessageFile(name: "Jahresabschlussbericht_Geschaeftsjahr_2026_endgueltige_Fassung_unterschrieben.pdf",
                                     path: "uploads/Jahresabschlussbericht.pdf"),
                         MessageFile(name: "2026년-3분기-가족여행-예산-정리-최종본-수정3.pdf", path: "uploads/ko.pdf")]
            for width: CGFloat in [375, 414] {
                for (n, item) in [("open", open), ("sending", sending), ("ended", ended)] {
                    let size = render(LocationAskCard(item: item, model: model), width: width, name: "location-\(n)-\(lang)")
                    #expect(size.width <= width + 0.5, "\(n) \(lang) \(width)")
                }
                let size = render(UserFiles(files: files), width: width, name: "files-\(lang)")
                #expect(size.width <= width + 0.5)
                let one = render(UserFiles(files: [files[0]]), width: width, name: "file-one-\(lang)")
                #expect(one.width <= width + 0.5)
            }
        }
        Strings.shared.use("en")
    }
}

private final class FakeTransportForRender: HTTPTransport, @unchecked Sendable {
    func send(_ request: URLRequest) async throws -> (Int, Data) { (200, Data("[]".utf8)) }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        (200, AsyncThrowingStream { $0.finish() })
    }
}
