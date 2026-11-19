package chat.kelo.app.agent

import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.runtime.getValue
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*
import kotlinx.coroutines.delay

/**
 * What the screen next to the capture field needs of it (the desktop's VNC screen): [on] says keys now go through the capture, [requester]
 * moves the focus there (a click on the page), and [shortcut] is where the capture hands the keys that are not typing (Ctrl/⌘/Alt combinations,
 * F keys) so the screen can send them as real keys; it answers whether it took the key.
 */
class RemoteKeyboardFocus {
    val requester = FocusRequester()
    var on by mutableStateOf(false)
    var shortcut: ((KeyEvent) -> Boolean)? = null
}

/** Provided by the live page; null where there is no live page. */
val LocalRemoteKeyboard = compositionLocalOf<RemoteKeyboardFocus?> { null }

/** The keyboard type that fits the focused field ([Typing.kind], the server's EDITING_JS): a password field gets the password keyboard. */
fun keyboardTypeFor(kind: String?): KeyboardType = when (kind) {
    "secret" -> KeyboardType.Password
    "number" -> KeyboardType.Number
    "tel" -> KeyboardType.Phone
    "email" -> KeyboardType.Email
    "url" -> KeyboardType.Uri
    else -> KeyboardType.Text
}

/** The named keys of a hardware keyboard that go to the page as keys (the rest is typed through the field): Tab, Escape, arrows, Home, End, Delete, PageUp/Down. */
fun pageKeyName(key: Key): String? = when (key) {
    Key.Tab -> "Tab"
    Key.Escape -> "Escape"
    Key.DirectionLeft -> "ArrowLeft"
    Key.DirectionRight -> "ArrowRight"
    Key.DirectionUp -> "ArrowUp"
    Key.DirectionDown -> "ArrowDown"
    Key.MoveHome -> "Home"
    Key.MoveEnd -> "End"
    Key.Delete -> "Delete"
    Key.PageUp -> "PageUp"
    Key.PageDown -> "PageDown"
    else -> null
}

/**
 * The keyboard for the page while the owner holds the tab (live typing, server live_v 3): a hidden one-line field that catches what the
 * owner's keyboard does (letters, a Korean syllable forming, deletions, Enter) and hands each change to [client] as steps for the page's
 * own field, so the page shows every keystroke at once. Nothing is drawn and nothing typed is kept or logged.
 *
 * [typing] is the focused field of the page (null: none has focus), [active] says live typing is on (the owner holds the tab and the server
 * is new enough), [open] that the owner opened the keyboard by hand (the keyboard key); [onOpenChange] reports a change of it.
 */
@Composable
fun RemoteKeyboard(client: LiveViewModel, typing: Typing?, active: Boolean, open: Boolean, onOpenChange: (Boolean) -> Unit, modifier: Modifier = Modifier) {
    if (!active) return
    val rk = LocalRemoteKeyboard.current
    val focus = rk?.requester ?: remember { FocusRequester() }
    val keyboard = LocalSoftwareKeyboardController.current
    var value by remember { mutableStateOf(emptyValue()) }
    fun startAgain() { value = emptyValue() }
    val resets by client.typingResets.collectAsStateWithLifecycle()
    LaunchedEffect(resets) { startAgain() }
    // the focus moved to another field (or a field took focus): the page's field is not the one the buffer was about
    LaunchedEffect(typing?.field) { if (client.typingReset()) startAgain() }
    // a field took focus on the page: the keyboard comes up, once per focus; it goes again 400 ms after the focus left (not for a keyboard the owner opened)
    var wasEditing by remember { mutableStateOf(false) }
    LaunchedEffect(typing != null) {
        val editing = typing != null
        if (editing && !wasEditing) { runCatching { focus.requestFocus() }; keyboard?.show() }
        if (!editing && wasEditing) {
            delay(400)
            if (!open) keyboard?.hide()
        }
        wasEditing = editing
    }
    LaunchedEffect(open) {
        if (open) { runCatching { focus.requestFocus() }; keyboard?.show() } else if (typing == null) keyboard?.hide()
    }
    DisposableEffect(client) { onDispose { client.typingCommit() } }
    val hint = string(Res.string.live_keyboard_desc)
    BasicTextField(
        value = value,
        onValueChange = { v ->
            val typed = RemoteTyping.typed(v.text, v.selection.start, v.selection.end, v.composition?.start, v.composition?.end)
            if (v.text == value.text && typed.composing.isEmpty() && v.selection != value.selection) {
                // the caret moved with no text change (a spacebar swipe, hardware arrows): arrows for the page, then start again
                val delta = v.selection.end - value.selection.end
                val key = if (delta < 0) "ArrowLeft" else "ArrowRight"
                client.typingKeys(List(kotlin.math.abs(delta).coerceAtMost(RemoteTyping.MAX_STEPS)) { key })
            } else {
                client.typingUpdate(typed)
                value = when {
                    typed.composing.isNotEmpty() -> v                                             // keep the composition untouched
                    !v.text.startsWith(RemoteTyping.MARK) || RemoteTyping.mustReset(typed) -> { client.typingReset(); emptyValue() }
                    v.selection.collapsed && v.selection.end != v.text.length -> v.copy(selection = TextRange(v.text.length))
                    else -> v
                }
            }
        },
        modifier = modifier.size(1.dp).alpha(0f).focusRequester(focus).semantics { contentDescription = hint }
            .onPreviewKeyEvent { e ->
                val name = pageKeyName(e.key)
                if (rk?.shortcut?.invoke(e) == true) { if (e.type == KeyEventType.KeyDown) client.typingKeys(emptyList()); true }   // a shortcut for the page: the buffer starts again
                else if (name == null || e.type != KeyEventType.KeyDown) false
                else { client.typingKeys(listOf(name)); true }
            },
        singleLine = true,
        keyboardOptions = KeyboardOptions(
            autoCorrectEnabled = false, capitalization = KeyboardCapitalization.None, keyboardType = keyboardTypeFor(typing?.kind),
            imeAction = ImeAction.Go,
        ),
        // the keyboard's action key (and a hardware Enter): commit what composes, then Enter. A textarea takes it as a new line.
        keyboardActions = KeyboardActions(onAny = { client.typingKeys(listOf("Enter")) }),
    )
}

private fun emptyValue() = TextFieldValue(RemoteTyping.MARK, TextRange(RemoteTyping.MARK.length))
