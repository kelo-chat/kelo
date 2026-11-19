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
    val removing: Set<String> = emptySet(),
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

    /** "목록에서 지우기": DELETE, then the row is gone from the list. */
    fun remove(key: String, onDone: (() -> Unit)? = null) {
        if (key in _state.value.removing) return
        _state.update { it.copy(removing = it.removing + key) }
        viewModelScope.launch {
            runCatching {
                val api = sessions.requireApi()
                FollowCalls.remove(api, key)
            }.onSuccess {
                _state.update { s -> s.copy(removing = s.removing - key, items = s.items.filter { it.key != key }) }
                onDone?.invoke()
            }.onFailure {
                _state.update { s -> s.copy(removing = s.removing - key) }
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
