package chat.kelo.app.voice

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.text.input.TextFieldValue

/**
 * The field the spoken words go into. The Composer owns the field, so it sets this on every composition: [get] reads the field,
 * [set] writes the field, and [send] puts the text in the field and sends it the way the send button does (it is refused while
 * Kelo replies, and the text then stays in the field).
 */
class VoiceTarget(
    val get: () -> TextFieldValue,
    val set: (TextFieldValue) -> Unit,
    val send: (String) -> Unit,
)

/**
 * The composer's voice, one per chat screen: the recording sources (the server's and, on the phone, its own recognizer) and the field
 * the words go into. [begin] remembers the caret when a recording starts; [deliver] merges the words there.
 */
@Stable
class ComposerVoice(val sources: List<RecordingBarSource>) {
    var target: VoiceTarget? = null

    private var anchor: VoiceInsert.Anchor? = null

    /** A recording starts: the words go where the caret is now (between the text before and after the selection). */
    fun begin() {
        val t = target ?: return
        val f = t.get()
        val at = f.selection.start.coerceIn(0, f.text.length)
        val to = f.selection.end.coerceIn(at, f.text.length)
        anchor = VoiceInsert.Anchor(f.text.substring(0, at), f.text.substring(to))
    }

    /** The words of a finished recording: merged at the anchor. FIELD puts them in the field only; SEND also sends them. */
    fun deliver(words: String, after: AfterSpeech) {
        val t = target ?: return
        val a = anchor ?: run {
            val f = t.get()
            VoiceInsert.Anchor(f.text.substring(0, f.selection.start.coerceIn(0, f.text.length)),
                f.text.substring(f.selection.end.coerceIn(0, f.text.length)))
        }
        val merged = VoiceInsert.merge(a, words)
        t.set(TextFieldValue(merged.text, androidx.compose.ui.text.TextRange(merged.cursor)))
        if (after == AfterSpeech.SEND) t.send(merged.text)
    }

    /** A recording was dropped (✕ on the phone recognizer): the field goes back to the text it had before the words. */
    fun restore() {
        val t = target ?: return
        val a = anchor ?: return
        anchor = null
        t.set(TextFieldValue(a.before + a.after, androidx.compose.ui.text.TextRange(a.before.length)))
    }

    /** The first source that is recording now, with its state; null while nothing records. */
    @Composable
    fun active(): Pair<RecordingBarSource, RecordingBarState>? {
        val states = sources.map { it.bar.collectAsState().value }
        val i = states.indexOfFirst { it != null }
        return if (i < 0) null else sources[i] to states[i]!!
    }
}
