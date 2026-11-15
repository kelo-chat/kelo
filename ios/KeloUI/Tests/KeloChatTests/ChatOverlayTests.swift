import Testing
import CoreGraphics
@testable import KeloChat

/// Android ChatOverlayRulesTest: the "↓" sits on the composer's top border, clear of the field's text, instead of a band being cut
/// out of the list.
@Suite struct ChatOverlayTests {
    @Test func theButtonStraddlesTheCardTopOrSitsOnTheAttachments() {
        let size = ChatOverlayRules.jumpSize
        // the button's lower edge, below the composer's top: half of it inside the card, half above
        #expect(ChatOverlayRules.jumpDrop(cardTop: 6, tray: false) == 6 + size / 2)
        #expect(ChatOverlayRules.jumpDrop(cardTop: 6, tray: true) == 0)
    }

    @Test func theFieldKeepsItsFirstLineClearOfTheButton() {
        let size = ChatOverlayRules.jumpSize
        for inset in [CGFloat(12), 16, 0, 40] {
            let extra = ChatOverlayRules.fieldTopExtra(inset)
            #expect(extra >= 0)
            #expect(inset + extra - size / 2 >= ChatOverlayRules.fieldGap)
        }
        #expect(ChatOverlayRules.fieldTopExtra(12) == 10)
        #expect(ChatOverlayRules.fieldTopExtra(40) == 0)
    }

    @Test func scrolledToTheEndTheLastRowSitsJustAboveTheComposer() {
        // the list keeps 6 after its last row, the composer 6 above its card: the scrim has no room of its own
        let fade = ChatOverlayRules.scrimFade(composerTop: 6, listEnd: 6)
        #expect(fade == 0 && 6 + fade + 6 == ChatOverlayRules.listGap)
        #expect(ChatOverlayRules.scrimFade(composerTop: 6) + 6 == ChatOverlayRules.listGap)   // Android / desktop
        #expect((10...14).contains(ChatOverlayRules.listGap))                                  // a bit more than between two bubbles (8)
        #expect(ChatOverlayRules.scrimFade(composerTop: 20) == 0)
    }
}
