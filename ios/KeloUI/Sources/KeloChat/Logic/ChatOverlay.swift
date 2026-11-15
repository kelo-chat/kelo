import CoreGraphics

/// The "↓" button is part of the composer, not of the list (Android chat/ChatOverlayClear.kt; at the top the chat is like every
/// other page: the messages scroll on under the floating agent). It straddles the composer card's top border at the trailing
/// side (its lower half on the card's empty top strip, which the text field keeps clear: `fieldTopExtra`), so the list runs on down
/// to the composer with no band cut out.
public enum ChatOverlayRules {
    /// Scrolled to the end, the room between the last row and the composer's top (its card, or the attachments waiting above it):
    /// a little more than between two bubbles (8). The "↓" sits on the composer's edge and shows only away from the end, so it needs
    /// none.
    public static let listGap: CGFloat = 12
    /// The "↓" button's diameter.
    public static let jumpSize: CGFloat = 36
    /// The least room between the button's lower edge and the text field's first line.
    public static let fieldGap: CGFloat = 4

    /// How far the "↓" button's lower edge sits below the top of what the composer shows above its card. `cardTop`: the room above
    /// the card. The button straddles the card's top border; while attachments wait above the card (`tray`) it sits on top of them
    /// instead (its lower half would cover a chip).
    public static func jumpDrop(cardTop: CGFloat, tray: Bool, size: CGFloat = jumpSize) -> CGFloat {
        tray ? 0 : cardTop + size / 2
    }

    /// The extra room the composer's text field needs at its top so the button's lower half (inside the card) stays `fieldGap` clear of
    /// the first line, given the field's own top inset `fieldTop`.
    public static func fieldTopExtra(_ fieldTop: CGFloat, size: CGFloat = jumpSize) -> CGFloat {
        max(0, size / 2 + fieldGap - fieldTop)
    }

    /// The composer's transparent room above its content (the top of its scrim), so the last row ends `listGap` above the composer:
    /// what is left after the composer's own top room and the room the list keeps after its last row. The list keeps the composer's
    /// whole measured height free at its end (a bottom safe-area inset), so this holds as the field grows, with the attachments open
    /// and with the keyboard up.
    public static func scrimFade(composerTop: CGFloat, listEnd: CGFloat = 0) -> CGFloat { max(0, listGap - composerTop - listEnd) }
}
