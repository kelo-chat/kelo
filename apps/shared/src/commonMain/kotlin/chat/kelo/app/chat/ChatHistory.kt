package chat.kelo.app.chat

import chat.kelo.app.Caches
import chat.kelo.app.core.ApiError
import kotlin.coroutines.cancellation.CancellationException
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** The stored messages: the newest page, older pages, and noticing that another phone talked to the same assistant. */
internal class ChatHistory(private val host: ChatHost, private val sync: ChatSync, kept: List<StoredMessage>?) {
    /** The newest stored message seen (0 = none yet): a different one on the server means news */
    var lastId = kept?.lastOrNull()?.id ?: 0L

    // ---- history
    fun reload() {
        host.scope.launch { refreshing { reloadNow() } }
    }

    /**
     * Loads the newest page. [afterReply]: a reply just ended; its rows are on screen (text, tool chips, pictures) and each message
     * is swapped for its stored version in place ([mergeStored]): the tidier stored text, the answer buttons and cards, the stored
     * pictures; the rest of the list (chips, cards of the turn, scroll position) stays as it is.
     */
    suspend fun reloadNow(afterReply: Boolean = false) {
        val api = host.api()
        val ms = api.get<List<StoredMessage>>("/api/messages?limit=${ChatViewModel.PAGE}")
        host.sessions.keep(api, Caches.MESSAGES, ms)
        if (host.state.value.busy) return   // a reply started meanwhile: its own items are on screen
        lastId = ms.lastOrNull()?.id ?: 0
        // the same rows (same keys) as the kept ones: the list updates in place, nothing jumps
        host.state.update { it.copy(items = if (afterReply) mergeStored(it.items, ms.map(::toChatItem)) else ms.map(::toChatItem),
            loaded = true, hasOlder = if (afterReply) it.hasOlder else ms.size >= ChatViewModel.PAGE, offline = false,
            secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
        sync.loadFollowupsNow()
        sync.refreshSecretsNow()
        sync.refreshAsks(force = true)   // a location card still waiting (it is not a stored message) comes back at the end
    }

    private fun mergeStored(shown: List<ChatItem>, stored: List<ChatItem>): List<ChatItem> = mergeStoredRows(shown, stored)

    /** Runs a refresh; a server that cannot be reached leaves what is shown and only marks it as possibly old. */
    private suspend fun refreshing(block: suspend () -> Unit) {
        try {
            block()
            if (host.state.value.offline) host.state.update { it.copy(offline = false) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiError) {
            if (!e.isUnauthorized) host.state.update { it.copy(offline = true) }
        } catch (e: Exception) {
            host.state.update { it.copy(offline = true) }
        }
    }

    /** Older messages when the list is scrolled to its top (before_id). */
    fun loadOlder() {
        val s = host.state.value
        if (!s.hasOlder || s.loadingOlder) return
        val first = s.items.firstNotNullOfOrNull { (it as? ChatItem.Msg)?.id } ?: return
        host.state.update { it.copy(loadingOlder = true) }
        host.scope.launch {
            try {
                val ms = host.api().get<List<StoredMessage>>("/api/messages?limit=${ChatViewModel.PAGE}&before_id=$first")
                host.state.update { st ->
                    val known = st.items.mapTo(HashSet()) { it.key }
                    st.copy(items = ms.map(::toChatItem).filter { it.key !in known } + st.items, hasOlder = ms.size >= ChatViewModel.PAGE)
                }
            } catch (_: ApiError) {
            } finally {
                host.state.update { it.copy(loadingOlder = false) }
            }
        }
    }

    /** Another phone may have talked to the same assistant: while idle and in view, reload when the newest message changed. */
    suspend fun checkForNews() {
        if (host.state.value.busy) return
        refreshing {
            val ms = host.api().get<List<StoredMessage>>("/api/messages?limit=1")
            if ((ms.lastOrNull()?.id ?: 0) != lastId && !host.state.value.busy) {
                host.stir()   // a new message came in: the character wakes up
                reloadNow()
            }
        }
    }

    /** After a reply: note the newest id, and keep the newest page for the next open (the rows on screen stay as they are). */
    fun remember() {
        host.scope.launch {
            runCatching {
                val api = host.api()
                val ms = api.get<List<StoredMessage>>("/api/messages?limit=${ChatViewModel.PAGE}")
                lastId = ms.lastOrNull()?.id ?: 0
                host.sessions.keep(api, Caches.MESSAGES, ms)
            }
        }
    }
}

/** A stored message as a row of the chat. */
internal fun toChatItem(m: StoredMessage) =
    ChatItem.Msg("m${m.id}", mine = m.role == "user", text = m.content, id = m.id, replies = m.replies, at = (m.createdAt * 1000).toLong(),
        reaction = m.reaction, briefing = m.briefing, comparison = m.comparison, images = m.images, files = m.attachments, autoText = m.autoText)

/**
 * The shown rows after a reply, matched to the stored messages (the shown row's key stays, so the list updates the row in place).
 * (1) A shown message with an id takes the stored message with that id. (2) A shown message without an id (just streamed) takes,
 * from the newest end, the stored messages whose ids are not shown, while the role agrees (the first mismatch stops it). (3) Stored
 * messages newer than the newest matched one that matched nothing (e.g. one another device sent) are appended at the end, in
 * order, with their own keys. Rows that are not messages stay where they are.
 */
internal fun mergeStoredRows(shown: List<ChatItem>, stored: List<ChatItem>): List<ChatItem> {
    val shownMsgs = shown.filterIsInstance<ChatItem.Msg>()
    val storedMsgs = stored.filterIsInstance<ChatItem.Msg>()   // oldest first
    val indexById = HashMap<Long, Int>()
    storedMsgs.forEachIndexed { i, m -> m.id?.let { indexById.putIfAbsent(it, i) } }
    val swap = HashMap<String, ChatItem.Msg>()                   // shown key -> the stored message it takes
    val used = HashSet<Int>()                                    // stored indexes taken
    for (o in shownMsgs) {
        val i = (o.id ?: continue).let { indexById[it] } ?: continue
        swap[o.key] = storedMsgs[i].copy(key = o.key)
        used += i
    }
    val shownIds = shownMsgs.mapNotNullTo(HashSet()) { it.id }
    val candidates = storedMsgs.indices.filter { storedMsgs[it].id !in shownIds && it !in used }
    var c = candidates.size - 1
    for (o in shownMsgs.asReversed()) {
        if (o.id != null) continue
        if (c < 0) break
        val i = candidates[c--]
        if (storedMsgs[i].mine != o.mine) break
        swap[o.key] = storedMsgs[i].copy(key = o.key)
        used += i
    }
    val newest = used.maxOrNull() ?: -1
    val extra = storedMsgs.indices.filter { it !in used && it > newest }.map { storedMsgs[it] }
    return shown.map { if (it is ChatItem.Msg) swap[it.key] ?: it else it } + extra
}

