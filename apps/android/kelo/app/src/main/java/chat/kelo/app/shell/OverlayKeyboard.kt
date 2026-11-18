package chat.kelo.app.shell

/**
 * Whether the keyboard must be put away because a page sits over the chat.
 *
 * The Work page and the live browser tab open as overlays on top of the chat rather than as
 * navigation, so the composer keeps focus underneath them and the keyboard would stay up over
 * the page. Kept apart from the UI so it can be tested.
 */
internal object OverlayKeyboard {
    fun shouldDismiss(workOpen: Boolean, liveTabOpen: Boolean): Boolean = workOpen || liveTabOpen
}
