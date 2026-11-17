package chat.kelo.app.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * The search screen's state. [query] is the field as typed; [hits] the results so far, newest first; [searched] a search for the
 * current query has answered (so "nothing found" may show); [hasMore] a full page came back; [failed] the last call did not reach
 * the server. The field's debounce is the screen's job, so this takes each query at once.
 */
data class ChatSearchState(
    val query: String = "",
    val hits: List<SearchHit> = emptyList(),
    val searched: Boolean = false,
    val loading: Boolean = false,
    val hasMore: Boolean = false,
    val failed: Boolean = false,
)

class ChatSearchViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(ChatSearchState())
    val state: StateFlow<ChatSearchState> = _state.asStateFlow()
    private var job: Job? = null

    /** A new query (or an empty one, which asks nothing and forgets the hits). A search still running for the old query is dropped. */
    fun setQuery(raw: String) {
        job?.cancel()
        val q = ChatSearchRules.query(raw)
        if (q.isEmpty()) {
            job = null
            _state.value = ChatSearchState(query = raw)
            return
        }
        _state.value = ChatSearchState(query = raw, loading = true)
        job = viewModelScope.launch { fetch(q, before = null) }
    }

    /** The next, older page of the current query; only when the last page was a full one. */
    fun loadMore() {
        val s = _state.value
        if (!s.hasMore || s.loading) return
        val before = ChatSearchRules.nextBefore(s.hits) ?: return
        val q = ChatSearchRules.query(s.query)
        _state.update { it.copy(loading = true) }
        job = viewModelScope.launch { fetch(q, before = before) }
    }

    private suspend fun fetch(q: String, before: Long?) {
        try {
            val page = sessions.requireApi().get<List<SearchHit>>(ChatSearchRules.searchPath(q, before))
            _state.update { s ->
                if (ChatSearchRules.query(s.query) != q) s   // the field changed meanwhile
                else s.copy(
                    hits = if (before == null) page else ChatSearchRules.merge(s.hits, page),
                    searched = true, loading = false, hasMore = ChatSearchRules.hasMore(page.size), failed = false,
                )
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            _state.update { s ->
                if (ChatSearchRules.query(s.query) != q) s
                else s.copy(searched = true, loading = false, hasMore = false, failed = true, hits = if (before == null) emptyList() else s.hits)
            }
        }
    }
}
