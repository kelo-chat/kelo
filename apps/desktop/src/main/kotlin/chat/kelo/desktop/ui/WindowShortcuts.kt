package chat.kelo.desktop.ui

import androidx.compose.ui.input.key.Key

/** What `app.openPage` gets for the chat's search (Cmd/Ctrl+F): not a page, so [KeloApp] opens the search over the chat. */
const val OPEN_CHAT_SEARCH = "chat-search"

/**
 * What `app.openPage` gets for a key pressed with Cmd (macOS) or Ctrl (elsewhere), [withModifier] telling whether that modifier is
 * down: 1…5 a page, F the chat's search; null when the key is not a window shortcut.
 */
fun windowShortcut(key: Key, withModifier: Boolean): String? = if (!withModifier) null else when (key) {
    Key.One -> Page.CHAT.name
    Key.Two -> Page.GOALS.name
    Key.Three -> Page.WORK.name
    Key.Four -> Page.CONNECTORS.name
    Key.Five -> Page.MY.name
    Key.F -> OPEN_CHAT_SEARCH
    else -> null
}
