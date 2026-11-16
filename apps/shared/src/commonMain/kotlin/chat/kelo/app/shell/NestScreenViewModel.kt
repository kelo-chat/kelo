package chat.kelo.app.shell

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The owner must confirm [action] first ([result]: the question, where it goes, what is sent). */
data class NestConfirm(val action: NestAction, val form: JsonObject?, val result: NestActionResult.Confirm)

/**
 * One open Nest surface. [loaded]: the first answer came; [gone]: the package may not show it any more (403 / 404: the screen is
 * removed from view and the app re-reads the extensions); [error]: a failed load (the old [screen] stays); [busy]: the keys of the
 * buttons whose request is running; [message]: a line to show briefly (an action's `message`, or the `detail` of a refused one).
 */
data class NestScreenState(
    val loaded: Boolean = false,
    val screen: NestScreen = NestScreen(),
    val error: String? = null,
    val gone: Boolean = false,
    val refreshing: Boolean = false,
    val busy: Set<String> = emptySet(),
    val message: String? = null,
    val confirm: NestConfirm? = null,
)

/** Things an action asks the app to do outside the screen. */
sealed interface NestEffect {
    /** Open [url] in the system browser (never in a web view). */
    data class OpenUrl(val url: String) : NestEffect
    /** Kelo was asked to do something: [turnId] is the reply; the app offers to open the chat. */
    data class Started(val message: String, val turnId: String) : NestEffect
}

/** Loads a surface's screen and runs its actions (docs/nest-ui.md Part 1). One per surface; [reload] is for "the screen changed". */
class NestScreenViewModel(private val sessions: Sessions, val entry: NestEntry) : ViewModel() {
    private val _state = MutableStateFlow(NestScreenState())
    val state: StateFlow<NestScreenState> = _state.asStateFlow()
    private val fx = Channel<NestEffect>(Channel.BUFFERED)
    val effects: Flow<NestEffect> = fx.receiveAsFlow()

    /** The next effect that is waiting, if any (tests and the screen's own collector use [effects]). */
    fun nextEffect(): NestEffect? = fx.tryReceive().getOrNull()

    init {
        // Kelo changed this package's draft (the live event nest_changed {"id"}): the open screen reloads at once
        viewModelScope.launch { NestLive.changes.collect { id -> if (id == entry.packageId) load() } }
    }

    private val base get() = "/api/nest/extensions/${KeloApi.segment(entry.packageId)}"

    /** Fetches the screen: at open, on pull-to-refresh, after an action answers `refresh`, and when `nest_changed` names this package. */
    fun load() {
        _state.update { it.copy(refreshing = true) }
        viewModelScope.launch {
            try {
                val el = sessions.requireApi().get<JsonElement>("$base/data?surface=${KeloApi.segment(entry.surface)}")
                _state.update { it.copy(loaded = true, refreshing = false, screen = NestScreenParse.screen(el), error = null, gone = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                // 403 turned off or not allowed any more, 404 no such surface: gone from view. 429 and the rest: say it, keep what we had
                _state.update {
                    it.copy(loaded = true, refreshing = false, gone = e.status == 403 || e.status == 404, error = e.detail)
                }
            } catch (e: Exception) {
                _state.update { it.copy(loaded = true, refreshing = false, error = e.message ?: "") }
            }
        }
    }

    /** Runs [action] (a button, a tapped card or row, a form's submit with its [form] values). Ignored while it already runs. */
    fun run(action: NestAction, form: JsonObject? = null) {
        if (action.id.isEmpty() || action.key in _state.value.busy) return
        if (action.type == "open_url") {   // the app opens it itself (the address is in the screen)
            if (nestOpenable(action.url)) fx.trySend(NestEffect.OpenUrl(action.url))
            return
        }
        post(action, form) { buildJsonObject {
            form?.let { put("form", it) }
            if (action.item.isNotEmpty()) put("item", action.item)
            if (action.data.isNotEmpty()) put("data", action.data)
        } }
    }

    /** "Confirm" on the dialog: the same POST with the token only. */
    fun confirm() {
        val c = _state.value.confirm ?: return
        _state.update { it.copy(confirm = null) }
        post(c.action, null) { buildJsonObject { put("confirm", c.result.token) } }
    }

    fun dismissConfirm() = _state.update { it.copy(confirm = null) }
    fun clearMessage() = _state.update { it.copy(message = null) }

    private fun post(action: NestAction, form: JsonObject?, body: () -> JsonObject) {
        _state.update { it.copy(busy = it.busy + action.key) }
        viewModelScope.launch {
            try {
                val el = sessions.requireApi().post<JsonElement>("$base/actions/${KeloApi.segment(action.id)}", body())
                when (val r = NestActionResult.parse(el)) {
                    is NestActionResult.Done -> {
                        _state.update { it.copy(message = r.message.ifBlank { null }) }
                        if (r.refresh) load()
                    }
                    is NestActionResult.Confirm -> _state.update { it.copy(confirm = NestConfirm(action, form, r)) }
                    is NestActionResult.Open -> if (nestOpenable(r.url)) fx.trySend(NestEffect.OpenUrl(r.url))
                    is NestActionResult.Started -> {
                        _state.update { it.copy(message = r.message.ifBlank { null }) }
                        fx.trySend(NestEffect.Started(r.message, r.turnId))
                    }
                    NestActionResult.Unknown -> {}
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                _state.update { it.copy(message = e.detail, confirm = if (e.status == 410) null else it.confirm) }
                // 403 not allowed any more, 404 the button is gone: re-read the screen
                if (e.status == 403 || e.status == 404) load()
            } catch (e: Exception) {
                _state.update { it.copy(message = e.message) }
            } finally {
                _state.update { it.copy(busy = it.busy - action.key) }
            }
        }
    }
}
