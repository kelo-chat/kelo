import Testing
import SwiftUI
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// Renders a search result at the narrowest phone width (320 pt) and two more, with a long spaceless Korean word, a long German
/// word and a long agent name (KELO_RENDER_DIR=<folder> writes PNGs to look at; the layout is always run).
@Suite(.serialized) struct ChatSearchRenderTests {
    @MainActor private func render<V: View>(_ view: V, width: CGFloat, name: String) -> CGSize {
        let app = AppModel(store: MemorySessionStore(), prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-search-render-\(UUID().uuidString)")!),
                           transport: SearchRenderTransport(), cache: ScreenCache(directory: nil, key: { nil }))
        let content = view.environment(app).keloTheme().padding(16).frame(width: width).background(Color.white)
        let r = ImageRenderer(content: content)
        r.scale = 2
        if let dir = ProcessInfo.processInfo.environment["KELO_RENDER_DIR"], let img = r.nsImage,
           let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name)-\(Int(width)).png"))
        }
        return r.nsImage?.size ?? .zero
    }

    @MainActor @Test func resultsFitTheNarrowestPhone() {
        let long = String(repeating: "정말긴단어", count: 14)
        let hits = [
            MessageHit(id: 1, role: "user", snippet: "«\(long)» 끝", createdAt: 1700000000),
            MessageHit(id: 2, role: "assistant", snippet: "Donaudampfschifffahrtsgesellschaftskapitaen «Gesamtabrechnung» 2026 Schlussbericht", createdAt: 1700000000),
        ]
        for lang in ["ko", "de"] {
            withLanguage(lang) {
                for width: CGFloat in [320, 375, 414] {
                    for hit in hits {
                        let size = render(ChatSearchRow(hit: hit, who: "Kelo Assistentin mit einem sehr langen Namen"), width: width,
                                          name: "search-\(lang)-\(hit.id)")
                        #expect(size.width <= width + 0.5, "\(lang) \(width) \(hit.id)")
                        #expect(size.height > 0)
                    }
                }
            }
        }
    }
}

private final class SearchRenderTransport: HTTPTransport, @unchecked Sendable {
    func send(_ request: URLRequest) async throws -> (Int, Data) { (200, Data("[]".utf8)) }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        (200, AsyncThrowingStream { $0.finish() })
    }
}
