package chat.kelo.app.call

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.voice.VoiceApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * View holder for Kelo's own voice (brief §4.6): [refresh] reads GET /api/voice/speech;
 * polls every 2 seconds while downloading.
 */
open class SpeechModel(
    private val api: () -> KeloApi?,
    private val scope: CoroutineScope,
    initialView: SpeechView? = null,
) {
    private val _view = MutableStateFlow<SpeechView?>(initialView)
    open val view: StateFlow<SpeechView?> = _view.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private var watching = false

    fun refresh() {
        scope.launch { load() }
    }

    fun choose() = act { it.speechChoose() }
    fun decline() = act { it.speechDecline() }
    fun remove() = act { it.speechRemove() }
    fun prefs(voice: Int? = null, speed: Double? = null) = act { it.speechPrefs(voice, speed) }

    private fun act(call: suspend (VoiceApi) -> SpeechView) {
        val client = api() ?: return
        scope.launch {
            try {
                _view.value = call(VoiceApi(client))
                _error.value = null
                watchIfDownloading()
            } catch (e: ApiError) {
                _error.value = e.code?.ifEmpty { null } ?: e.message ?: "failed"
            }
        }
    }

    /** Reads GET /api/voice/speech now (the call awaits it). */
    open suspend fun load() {
        val client = api() ?: return
        try {
            _view.value = VoiceApi(client).speechView()
            _error.value = null
        } catch (e: ApiError) {
            _error.value = e.code?.ifEmpty { null } ?: e.message ?: "failed"
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
                try {
                    _view.value = VoiceApi(client).speechView()
                } catch (_: ApiError) {
                    /* try again in two seconds */
                }
            }
            watching = false
        }
    }
}
