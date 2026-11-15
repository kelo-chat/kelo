#if os(macOS)
import Testing
import SwiftUI
import AppKit
@testable import KeloChat

/// Where BubbleWithReaction puts the chip (Android ReactionChip.kt BubbleWithReaction): drawn with plain colours and measured from
/// the pixels, in left-to-right and right-to-left.
@MainActor @Suite struct ReactionLayoutTests {
    /// The image of a `bubbleWidth` × 40 blue bubble with a 26 × 24 red chip, and the red and blue columns and rows in it.
    private func draw(bubbleWidth: CGFloat, chip: Bool = true, rtl: Bool = false) -> (size: CGSize, red: (x: ClosedRange<Int>, y: ClosedRange<Int>)?, blue: ClosedRange<Int>?) {
        let view = BubbleWithReaction {
            Color.blue.frame(width: bubbleWidth, height: 40)
            if chip { Color.red.frame(width: 26, height: 24) }
        }
        .fixedSize()
        .environment(\.layoutDirection, rtl ? .rightToLeft : .leftToRight)
        let r = ImageRenderer(content: view)
        r.scale = 1
        guard let cg = r.cgImage else { return (.zero, nil, nil) }
        let rep = NSBitmapImageRep(cgImage: cg)
        var rx: [Int] = [], ry: [Int] = [], bx: [Int] = []
        for y in 0..<cg.height {
            for x in 0..<cg.width {
                guard let c = rep.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB), c.alphaComponent > 0.5 else { continue }
                if c.redComponent > 0.8 && c.blueComponent < 0.4 { rx.append(x); ry.append(y) }
                if c.blueComponent > 0.8 && c.redComponent < 0.4 && y == 5 { bx.append(x) }
            }
        }
        let red = rx.isEmpty ? nil : (x: rx.min()!...rx.max()!, y: ry.min()!...ry.max()!)
        return (CGSize(width: cg.width, height: cg.height), red, bx.isEmpty ? nil : bx.min()!...bx.max()!)
    }

    @Test func nearTheEndCornerAndHangingBelow() {
        let d = draw(bubbleWidth: 200)
        #expect(d.size == CGSize(width: 200, height: 40 + ReactionChip.overhang))   // room for what hangs below, no more
        #expect(d.red?.x == 164...189)                                                  // 10 in from the right edge
        #expect(d.red?.y == 27...50)                                                    // over the bottom edge, down to the room's end
    }

    @Test func mirroredInRightToLeft() {
        let d = draw(bubbleWidth: 200, rtl: true)
        #expect(d.red?.x == 10...35)                                                    // 10 in from the left (end) edge
        #expect(d.blue == 0...199)
    }

    @Test func centredUnderAShortBubble() {
        #expect(draw(bubbleWidth: 40).red?.x == 7...32)
        #expect(draw(bubbleWidth: 40, rtl: true).red?.x == 7...32)
    }

    @Test func noChipNoRoom() {
        let d = draw(bubbleWidth: 120, chip: false)
        #expect(d.size == CGSize(width: 120, height: 40))
        #expect(d.red == nil)
    }
}
#endif
