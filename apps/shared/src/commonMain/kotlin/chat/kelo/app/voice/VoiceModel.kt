package chat.kelo.app.voice

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The owner's voice input state on this server, shared by the mic and My → 음성 입력. [refresh] reads GET /api/voice; while a model
 * is downloading it reads again every two seconds, so the chip and the page show the percent. A failed call keeps the last view.
 */
class VoiceModel(
    /** The live API client (null while signed out or before the server answered). */
    private val api: () -> KeloApi?,
    private val scope: CoroutineScope,
    private val lang: () -> String,
) {
    private val _view = MutableStateFlow<VoiceView?>(null)
    val view: StateFlow<VoiceView?> = _view.asStateFlow()

    private val _error = MutableStateFlow<VoiceProblem?>(null)
    /** The last call that failed (the page shows it as a line), null when the last call worked. */
    val error: StateFlow<VoiceProblem?> = _error.asStateFlow()

    private var watching = false

    fun refresh() {
        scope.launch { read() }
    }

    fun choose(id: String) = act { it.choose(id) }
    fun decline() = act { it.decline() }
    fun remove() = act { it.remove() }

    private fun act(call: suspend (VoiceApi) -> VoiceView) {
        val client = api() ?: return
        scope.launch {
            try {
                _view.value = call(VoiceApi(client))
                _error.value = null
                watchIfDownloading()
            } catch (e: ApiError) {
                _error.value = voiceProblem(if (e.isNetwork) null else e.status, e.code)
            }
        }
    }

    private suspend fun read() {
        val client = api() ?: return
        try {
            _view.value = VoiceApi(client).view(lang())
            _error.value = null
        } catch (e: ApiError) {
            _error.value = voiceProblem(if (e.isNetwork) null else e.status, e.code)
        }
        watchIfDownloading()
    }

    private fun watchIfDownloading() {
        if (watching || _view.value?.state != "downloading") return
        watching = true
        scope.launch {
            while (_view.value?.state == "downloading") {
                delay(2_000)
                val client = api() ?: break
                try { _view.value = VoiceApi(client).view(lang()) } catch (_: ApiError) { /* try again in two seconds */ }
            }
            watching = false
        }
    }
}

/** Puts the spoken words into the field where the caret was, with a space only where two words would touch. */
object VoiceInsert {
    data class Anchor(val before: String, val after: String)
    data class Merged(val text: String, val cursor: Int)

    fun merge(anchor: Anchor, spoken: String): Merged {
        val words = spoken.trim()
        if (words.isEmpty()) return Merged(anchor.before + anchor.after, anchor.before.length)
        val sep = if (anchor.before.isNotEmpty() && !anchor.before.last().isWhitespace()) " " else ""
        val head = anchor.before + sep + words
        return Merged(head + anchor.after, head.length)
    }
}
