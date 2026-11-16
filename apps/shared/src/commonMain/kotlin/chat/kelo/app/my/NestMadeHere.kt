package chat.kelo.app.my

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
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/*
 * My → Nest → "Made here" (docs/nest.md, "Made by talking"): the drafts Kelo made on this server (GET /api/nest/drafts) and the custom
 * skills (GET /api/skills, source "custom"), with Open, Share (POST /api/nest/drafts/{id}/share) and Delete.
 */

/** Where a draft was put on GitHub (`.shared.json`): "owner/name", the 40-character commit, when. */
@Serializable
data class NestShared(val repo: String = "", val commit: String = "", val at: Double? = null)

/** One row of GET /api/nest/drafts. */
@Serializable
data class NestDraftRow(
    val id: String, val kind: String = "skill", val name: String = "", val version: String = "",
    @SerialName("updated_at") val updatedAt: Double? = null, val shared: NestShared? = null,
)

@Serializable data class NestDraftList(val items: List<NestDraftRow> = emptyList())

/** GET /api/nest/drafts/{id}: the files (text) and the validator's findings. */
@Serializable
data class NestDraftDetails(
    val id: String = "", val files: Map<String, String> = emptyMap(), val valid: Boolean = true, val findings: List<String> = emptyList(),
    val version: String = "", val kind: String = "",
)

enum class MadeKind { SKILL, EXTENSION, CONNECTOR, CUSTOM_SKILL }

/** One line of "Made here". A [draft] can be shared and its screen opened; a custom skill is a skill Kelo made that is not a draft (yet). */
data class MadeItem(val id: String, val kind: MadeKind, val name: String, val version: String = "", val repo: String = "", val draft: Boolean = true) {
    val title: String get() = name.ifBlank { id }
    /** The shared repository's page, null when it was not shared (or the name is not a plain "owner/name"). */
    val repoUrl: String? get() = MadeHereRules.repoUrl(repo)
    val shared: Boolean get() = repo.isNotBlank()
}

object MadeHereRules {
    private val REPO = Regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

    fun repoUrl(repo: String): String? = repo.trim().takeIf { REPO.matches(it) }?.let { "https://github.com/$it" }

    private fun kind(k: String) = when (k) { "extension" -> MadeKind.EXTENSION; "connector" -> MadeKind.CONNECTOR; else -> MadeKind.SKILL }

    /** Drafts first (newest first), then the custom skills that are not a draft already (a skill made into a draft shows once). */
    fun items(drafts: List<NestDraftRow>, skills: List<CustomSkillRow>): List<MadeItem> {
        val d = drafts.sortedByDescending { it.updatedAt ?: 0.0 }.map { MadeItem(it.id, kind(it.kind), it.name, it.version, it.shared?.repo.orEmpty(), draft = true) }
        val ids = drafts.map { it.id }.toSet()
        val s = customSkillsForList(skills).filter { it.id !in ids }.map { MadeItem(it.id, MadeKind.CUSTOM_SKILL, it.name, draft = false) }
        return d + s
    }
}

/**
 * [loaded]: the first answer came; [items]: what was made here; [busy]: ids with a request running; [connectFor]: the id whose Share needs
 * GitHub write access (the sheet offers to connect it); [message]: a short failure line.
 */
data class MadeHereState(
    val loaded: Boolean = false,
    val failed: Boolean = false,
    val items: List<MadeItem> = emptyList(),
    val busy: Set<String> = emptySet(),
    val connectFor: MadeItem? = null,
    val message: String? = null,
)

sealed interface MadeEffect {
    /** Share started a chat turn: open the chat on [turnId] (Kelo asks where to put it). */
    data class OpenChat(val turnId: String) : MadeEffect
}

class MadeHereViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(MadeHereState())
    val state: StateFlow<MadeHereState> = _state.asStateFlow()
    private val fx = Channel<MadeEffect>(Channel.BUFFERED)
    val effects: Flow<MadeEffect> = fx.receiveAsFlow()
    fun nextEffect(): MadeEffect? = fx.tryReceive().getOrNull()

    init {
        // Kelo wrote to a draft (the live event nest_changed): the list follows
        viewModelScope.launch { chat.kelo.app.shell.NestLive.changes.collect { load() } }
    }

    /** Reads the drafts and the custom skills; each can fail on its own (an older server has no drafts) and the other still shows. */
    fun load() {
        viewModelScope.launch {
            val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@launch
            var bad = 0
            val drafts = try { api.get<NestDraftList>("/api/nest/drafts").items } catch (e: CancellationException) { throw e } catch (e: Exception) { bad++; emptyList() }
            val skills = try { api.get<CustomSkillList>("/api/skills").items } catch (e: CancellationException) { throw e } catch (e: Exception) { bad++; emptyList() }
            _state.update { it.copy(loaded = true, failed = bad == 2, items = MadeHereRules.items(drafts, skills)) }
        }
    }

    /** Share: 202 opens the chat on the turn; 409 `github_write_needed` raises the "connect GitHub" sheet; anything else says why not. */
    fun share(item: MadeItem) {
        if (item.id in _state.value.busy) return
        _state.update { it.copy(busy = it.busy + item.id, message = null) }
        viewModelScope.launch {
            try {
                val api = sessions.requireApi()
                // a custom skill is copied into a draft first ("share this skill"); the draft already being there is fine
                if (!item.draft) try {
                    api.post<JsonElement>("/api/nest/drafts/from-skill/${KeloApi.segment(item.id)}")
                } catch (e: ApiError) { if (e.status != 409) throw e }
                val turn = api.post<ShareStarted>("/api/nest/drafts/${KeloApi.segment(item.id)}/share").turnId
                _state.update { it.copy(connectFor = null) }
                if (turn.isNotBlank()) fx.trySend(MadeEffect.OpenChat(turn))
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status == 409 && e.code == "github_write_needed") _state.update { it.copy(connectFor = item) }
                else _state.update { it.copy(message = if (e.isNetwork) "" else e.detail) }   // blank = the app's own "couldn't start sharing"
            } catch (e: Exception) {
                _state.update { it.copy(message = e.message ?: "") }
            } finally {
                _state.update { it.copy(busy = it.busy - item.id) }
            }
        }
    }

    /** After GitHub was connected: Share again for the same item. */
    fun retryShare() { _state.value.connectFor?.let { share(it) } }
    fun dismissConnect() = _state.update { it.copy(connectFor = null) }
    fun clearMessage() = _state.update { it.copy(message = null) }

    /** Deletes the draft (DELETE /api/nest/drafts/{id}) or the custom skill, then reads the list again. */
    fun delete(item: MadeItem) {
        if (item.id in _state.value.busy) return
        _state.update { it.copy(busy = it.busy + item.id, message = null) }
        viewModelScope.launch {
            try {
                val path = if (item.draft) "/api/nest/drafts/${KeloApi.segment(item.id)}" else "/api/skills/custom/${KeloApi.segment(item.id)}"
                sessions.requireApi().delete<JsonElement>(path)
                _state.update { s -> s.copy(items = s.items.filterNot { it.id == item.id && it.draft == item.draft }) }
                load()
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                _state.update { it.copy(message = e.detail) }
            } finally {
                _state.update { it.copy(busy = it.busy - item.id) }
            }
        }
    }
}

/** POST /api/nest/drafts/{id}/share: 202 `{"turn_id"}`. */
@Serializable data class ShareStarted(@SerialName("turn_id") val turnId: String = "")
