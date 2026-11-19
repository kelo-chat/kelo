import Testing
import SwiftUI
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

/// The call card in the chat, closed and open, at 320 and 412 points (KELO_RENDER_DIR=<folder> writes PNGs to look at).
@Suite(.serialized) struct CallCardRenderTests {
    private func card() -> ChatItem.CallCard {
        let t: Int64 = 1_700_000_000_000
        func line(_ key: String, mine: Bool, _ text: String, _ at: Int64) -> ChatItem.Msg {
            ChatItem.Msg(key: key, mine: mine, text: text, id: at, at: at, fromCall: true, callId: "c1")
        }
        return ChatItem.CallCard(key: "call:c1:l1", callId: "c1", lines: [
            line("l1", mine: true, "나 목소리 너무 끊겨. 다시 말해볼게, 내일 오전 회의 몇 시였지?", t),
            line("l2", mine: false, "내일 오전 회의는 10시에 시작해요.", t + 20_000),
            line("l3", mine: true, "고마워.", t + 30_000),
            line("l4", mine: false, "네, 잘 들려요. 필요하면 회의 전에 알려 드릴게요.", t + 190_000),
        ])
    }

    @MainActor private func render(open: Bool, width: CGFloat) -> CGSize {
        let content = CallCardView(card: card(), open: open, agentName: "Kelo", onToggle: {})
            .keloTheme()
            .frame(width: width)
            .background(Color.white)
        let r = ImageRenderer(content: content)
        r.scale = 2
        if let dir = ProcessInfo.processInfo.environment["KELO_RENDER_DIR"], let img = r.nsImage,
           let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) {
            let name = "call-card-\(open ? "open" : "closed")-\(Int(width)).png"
            try? png.write(to: URL(fileURLWithPath: dir).appendingPathComponent(name))
        }
        return r.nsImage?.size ?? .zero
    }

    @MainActor @Test func callCardFitsPhoneWidthsClosedAndOpen() {
        for width: CGFloat in [320, 412] {
            let closed = render(open: false, width: width)
            let opened = render(open: true, width: width)
            #expect(closed.width == width, "closed call card at \(width) pt")
            #expect(opened.width == width, "open call card at \(width) pt")
            #expect(opened.height > closed.height, "the open call card shows its lines at \(width) pt")
        }
    }
}
