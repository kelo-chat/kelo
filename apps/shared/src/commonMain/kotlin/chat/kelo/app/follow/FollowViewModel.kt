package chat.kelo.app.follow

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class FollowState(
    val items: List<FollowItem> = emptyList(),
    val loading: Boolean = false,
    val failed: Boolean = false,
    val stopping: Set<String> = emptySet(),
    val replanning: Set<String> = emptySet(),
)

class FollowViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(FollowState())
    val state: StateFlow<FollowState> = _state.asStateFlow()

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            _state.update { it.copy(loading = true, failed = false) }
            runCatching {
                val api = sessions.requireApi()
                FollowCalls.list(api)
            }.onSuccess { list ->
                _state.update { it.copy(items = list, loading = false, failed = false) }
            }.onFailure {
                _state.update { it.copy(loading = false, failed = true) }
            }
        }
    }

    fun stop(key: String, onDone: (() -> Unit)? = null) {
        if (key in _state.value.stopping) return
        _state.update { it.copy(stopping = it.stopping + key) }
        viewModelScope.launch {
            runCatching {
                val api = sessions.requireApi()
                FollowCalls.stop(api, key)
            }.onSuccess { resp ->
                _state.update { s ->
                    s.copy(
                        stopping = s.stopping - key,
                        items = s.items.map { if (it.key == key) it.copy(state = resp.state.ifBlank { "stopped" }) else it },
                    )
                }
                onDone?.invoke()
            }.onFailure {
                _state.update { it.copy(stopping = it.stopping - key) }
            }
        }
    }

    fun replan(key: String, onDone: (() -> Unit)? = null) {
        if (key in _state.value.replanning) return
        _state.update { it.copy(replanning = it.replanning + key) }
        viewModelScope.launch {
            runCatching {
                val api = sessions.requireApi()
                FollowCalls.replan(api, key)
            }.onSuccess { resp ->
                _state.update { s ->
                    s.copy(
                        replanning = s.replanning - key,
                        items = s.items.map { if (it.key == key) it.copy(state = resp.state.ifBlank { "watching" }) else it },
                    )
                }
                load()
                onDone?.invoke()
            }.onFailure {
                _state.update { it.copy(replanning = it.replanning - key) }
            }
        }
    }
}
