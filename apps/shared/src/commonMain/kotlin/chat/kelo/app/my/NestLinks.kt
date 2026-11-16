package chat.kelo.app.my

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/*
 * My → Nest: install a package from a GitHub link someone shared (POST /api/nest/install-from-link, then .../confirm with the
 * permissions the owner saw), and the installed packages with their updates (GET /api/nest/installed). Nobody reviews packages.
 */

@Serializable data class NestWord(val key: String = "", val text: String = "", val level: String = "info")

@Serializable data class NestPerms(val hash: String = "", val connect: String = "", val words: List<NestWord> = emptyList())

@Serializable
data class NestLinkCard(
    val token: String = "", val id: String = "", val name: String = "", val description: String = "", val kind: String = "skill",
    val version: String = "", val author: String = "", val repo: String = "", val installed: String? = null,
    val permissions: NestPerms = NestPerms(), val contents: NestContents = NestContents(),
)

@Serializable
data class NestUpdate(
    val version: String = "", val widened: List<String> = emptyList(), @SerialName("permissions_hash") val permissionsHash: String = "",
    val words: List<NestWord> = emptyList(), val pinned: Boolean = false, val changed: NestChanged = NestChanged(),
)

@Serializable
data class NestInstalledRow(
    val id: String, val version: String = "", val name: String = "", val description: String = "", val author: String = "",
    val repo: String = "", val enabled: Boolean = true, val pinned: Boolean = false,
    @SerialName("permission_words") val words: List<NestWord> = emptyList(), val update: NestUpdate? = null,
) { val title: String get() = name.ifBlank { id } }

object NestLinkRules {
    private val LINK = Regex("^https://github\\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/tree/[^\\s]+)?/?$")
    /** A pasted link, trimmed; "github.com/a/b" gets https:// (people copy it without). */
    fun clean(raw: String): String {
        val s = raw.trim().removeSuffix(".git")
        return if (s.startsWith("github.com/")) "https://$s" else s
    }
    fun looksRight(raw: String): Boolean = LINK.matches(clean(raw))
    /** An update that asks for more than before must be shown before it installs. */
    fun needsLook(u: NestUpdate): Boolean = u.widened.isNotEmpty()
}

/**
 * [card]: the preview to show; [installing]: confirm running; [problem]: why the link or the install did not work (shown under the field);
 * [reason]: the server's own words for a refusal (422: an unknown field, a key-like text, not a package), shown under [problem].
 */
data class NestLinksState(
    val link: String = "", val checking: Boolean = false, val card: NestLinkCard? = null, val installing: Boolean = false,
    val problem: NestLinkProblem? = null, val reason: String = "", val installed: List<NestInstalledRow> = emptyList(),
    val loaded: Boolean = false, val busy: Set<String> = emptySet(), val look: NestInstalledRow? = null,
)

enum class NestLinkProblem { NOT_A_LINK, NOT_A_PACKAGE, EXPIRED, CHANGED, ALREADY, FAILED }

class NestLinksViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(NestLinksState())
    val state: StateFlow<NestLinksState> = _state.asStateFlow()

    fun setLink(v: String) = _state.update { it.copy(link = v, problem = null, reason = "") }
    fun dismissCard() = _state.update { it.copy(card = null) }
    fun dismissLook() = _state.update { it.copy(look = null) }

    fun loadInstalled() {
        viewModelScope.launch {
            val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@launch
            val rows = try { api.get<List<NestInstalledRow>>("/api/nest/installed") } catch (e: CancellationException) { throw e } catch (e: Exception) { emptyList() }
            _state.update { it.copy(installed = rows, loaded = true) }
        }
    }

    /** Reads the link: the server fetches the newest commit and answers with what it is and what it may do. */
    fun check() {
        val link = NestLinkRules.clean(_state.value.link)
        if (!NestLinkRules.looksRight(link)) { _state.update { it.copy(problem = NestLinkProblem.NOT_A_LINK) }; return }
        if (_state.value.checking) return
        _state.update { it.copy(checking = true, problem = null, reason = "") }
        viewModelScope.launch {
            try {
                val card = sessions.requireApi().post<NestLinkCard>("/api/nest/install-from-link", buildJsonObject { put("url", link) })
                _state.update { it.copy(card = card) }
            } catch (e: CancellationException) { throw e
            } catch (e: ApiError) {
                _state.update { it.copy(problem = if (e.status == 422) NestLinkProblem.NOT_A_PACKAGE else NestLinkProblem.FAILED,
                    reason = if (e.status == 422) e.detail else "") }
            } catch (e: Exception) { _state.update { it.copy(problem = NestLinkProblem.FAILED) }
            } finally { _state.update { it.copy(checking = false) } }
        }
    }

    /** Install exactly what the card showed (its token and permissions hash). */
    fun install() {
        val card = _state.value.card ?: return
        if (_state.value.installing) return
        _state.update { it.copy(installing = true) }
        viewModelScope.launch {
            try {
                sessions.requireApi().post<JsonElement>("/api/nest/install-from-link/confirm",
                    buildJsonObject { put("token", card.token); put("accept", card.permissions.hash) })
                _state.update { it.copy(card = null, link = "") }
                loadInstalled()
            } catch (e: CancellationException) { throw e
            } catch (e: ApiError) {
                val p = when {
                    e.status == 410 -> NestLinkProblem.EXPIRED
                    e.code == "changed" || e.code == "look_again" -> NestLinkProblem.CHANGED   // both: the owner has to look at it again
                    e.code == "already_installed" -> NestLinkProblem.ALREADY
                    else -> NestLinkProblem.FAILED
                }
                _state.update { it.copy(card = null, problem = p, reason = if (e.status == 422) e.detail else "") }
            } catch (e: Exception) { _state.update { it.copy(card = null, problem = NestLinkProblem.FAILED) }
            } finally { _state.update { it.copy(installing = false) } }
        }
    }

    /**
     * Update: one that asks for more opens [look] first; [accepted] = the owner saw the new permissions. The server may also answer
     * 409 `look_again` (it asks for more than the list shows): then the look opens instead of failing.
     */
    fun update(row: NestInstalledRow, accepted: Boolean = false) {
        val u = row.update ?: return
        if (NestLinkRules.needsLook(u) && !accepted) { _state.update { it.copy(look = row) }; return }
        busy(row.id) {
            val body = buildJsonObject { if (accepted) put("accept", u.permissionsHash) }
            try {
                it.post<JsonElement>("/api/nest/packages/${KeloApi.segment(row.id)}/update", body)
                _state.update { s -> s.copy(look = null) }
            } catch (e: ApiError) {
                if (e.code == "look_again") _state.update { s -> s.copy(look = row) } else throw e
            }
        }
    }

    fun remove(row: NestInstalledRow) = busy(row.id) { it.delete<JsonElement>("/api/nest/packages/${KeloApi.segment(row.id)}") }

    private fun busy(id: String, call: suspend (KeloApi) -> Unit) {
        if (id in _state.value.busy) return
        _state.update { it.copy(busy = it.busy + id) }
        viewModelScope.launch {
            try { call(sessions.requireApi()); loadInstalled() }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { _state.update { it.copy(problem = NestLinkProblem.FAILED) } }
            finally { _state.update { it.copy(busy = it.busy - id) } }
        }
    }
}
