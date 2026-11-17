package chat.kelo.app.my

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.agent.ForgetCalls
import chat.kelo.app.agent.ForgetRules
import chat.kelo.app.agent.ForgottenTopic
import chat.kelo.app.core.Sessions
import chat.kelo.app.res.Res
import chat.kelo.app.res.connect_failed
import chat.kelo.app.res.forget_allowed
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.jetbrains.compose.resources.StringResource

/** [items] null until the first load; [failed]: the list could not be read (a loaded list stays shown). */
data class ForgetUiState(val items: List<ForgottenTopic>? = null, val failed: Boolean = false, val busy: Set<String> = emptySet())

/** 마이 → Forgotten topics: what Kelo was asked to forget, and "Allow again" (the topic may be written about again). */
class ForgetViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(ForgetUiState())
    val state: StateFlow<ForgetUiState> = _state.asStateFlow()
    private val _messages = MutableSharedFlow<StringResource>(extraBufferCapacity = 4)
    val messages: SharedFlow<StringResource> = _messages.asSharedFlow()

    fun load() {
        viewModelScope.launch {
            try {
                val list = ForgetCalls.list(sessions.requireApi())
                _state.update { it.copy(items = list, failed = false) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(failed = true) }
            }
        }
    }

    /** "Allow again": the topic leaves the list at once; one the server no longer has (404) counts as done; any other failure puts the list back. */
    fun allowAgain(t: ForgottenTopic) {
        if (t.id in _state.value.busy) return
        _state.update { s -> s.copy(items = s.items?.let { ForgetRules.without(it, t.id) }, busy = s.busy + t.id) }
        viewModelScope.launch {
            try {
                ForgetCalls.allowAgain(sessions.requireApi(), t.id)
                _messages.tryEmit(Res.string.forget_allowed)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _messages.tryEmit(Res.string.connect_failed)
                load()
            } finally {
                _state.update { it.copy(busy = it.busy - t.id) }
            }
        }
    }
}
