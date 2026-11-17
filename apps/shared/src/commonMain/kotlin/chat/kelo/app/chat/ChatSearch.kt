package chat.kelo.app.chat

import chat.kelo.app.core.KeloApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * One result of GET /api/messages/search: [id] the message, [role] "user" or "assistant", [snippet] its text around the match with the
 * match wrapped in « and » (the server marks it; the app shows it as a highlight), [createdAt] unix seconds.
 */
@Serializable
data class SearchHit(
    val id: Long,
    val role: String = "",
    val snippet: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

/** A piece of a snippet: its text (no marks) and whether it is the match. */
data class SnippetPart(val text: String, val match: Boolean)

/**
 * The chat search's plain rules (no screen, no network): the query as typed is trimmed and its inner spaces collapse; a result's
 * snippet becomes plain parts with the match flagged; a page of results asks for the next one with before = its smallest id; a
 * message is either already in the chat's list or its window is fetched ([anchor]).
 */
object ChatSearchRules {
    /** Results per page (the server's default too) */
    const val PAGE = 20
    /** Messages each side of an opened result (GET /api/messages/around, n) */
    const val AROUND = 10
    /** Messages fetched after a search window's newest one at a time (the iPhone's MessageSearchRules.newerPage; the server allows 50) */
    const val NEWER_PAGE = 50
    /** How long the opened message stays highlighted, then it fades out over [HIGHLIGHT_FADE_MS] */
    const val HIGHLIGHT_HOLD_MS = 1500L
    const val HIGHLIGHT_FADE_MS = 600

    /** Where a message is when the chat opens at it: in the list already (scroll there), or it must be fetched with its window. */
    enum class Anchor { Loaded, Missing }

    /** The query the server gets: trimmed, inner whitespace collapsed to single spaces. */
    fun query(raw: String): String = raw.trim().replace(Regex("\\s+"), " ")

    fun hasQuery(raw: String): Boolean = query(raw).isNotEmpty()

    /** GET path of one page; [before] (the smallest id shown so far) asks for the older results. */
    fun searchPath(q: String, before: Long?, limit: Int = PAGE): String =
        "/api/messages/search?q=${KeloApi.segment(query(q))}&limit=$limit" + (before?.let { "&before=$it" } ?: "")

    /** GET path of the window around one message: [n] before and [n] after, the message itself included. */
    fun aroundPath(id: Long, n: Int = AROUND): String = "/api/messages/around?id=$id&n=$n"

    /**
     * A snippet as plain parts: the « » marks are taken out (a stray or unclosed mark too, an unclosed match running to the end)
     * and the text inside a pair is the match.
     */
    fun snippet(raw: String): List<SnippetPart> {
        val parts = mutableListOf<SnippetPart>()
        val buf = StringBuilder()
        var inMatch = false
        fun flush() {
            if (buf.isNotEmpty()) parts += SnippetPart(buf.toString(), inMatch)
            buf.clear()
        }
        for (ch in raw) {
            when (ch) {
                '«' -> { flush(); inMatch = true }
                '»' -> { flush(); inMatch = false }
                else -> buf.append(ch)
            }
        }
        flush()
        return parts
    }

    /** unix seconds (the server's created_at) as the milliseconds the chat's time labels take */
    fun timeMs(createdAtSeconds: Double): Long = (createdAtSeconds * 1000).toLong()

    /** A result from the user's own message (the row says "You") rather than the assistant's */
    fun isMine(role: String): Boolean = role == "user"

    /** A full page may have an older page behind it; a short one is the end of the results. */
    fun hasMore(pageSize: Int, limit: Int = PAGE): Boolean = pageSize >= limit

    /** The cursor for the next page: the smallest id on the results shown (null when there are none). */
    fun nextBefore(hits: List<SearchHit>): Long? = hits.minOfOrNull { it.id }

    /** Results already shown stay where they are; the page's new ones go on after them, in the server's order. */
    fun merge(shown: List<SearchHit>, page: List<SearchHit>): List<SearchHit> {
        val known = shown.mapTo(HashSet()) { it.id }
        return shown + page.filter { known.add(it.id) }
    }

    /**
     * The messages after [after] from a fetch around it (n = [NEWER_PAGE]): the rows with a bigger id, oldest first, and [more] when a
     * full page came (more may follow). A short page means the newest message is in.
     */
    fun newer(page: List<StoredMessage>, after: Long, size: Int = NEWER_PAGE): Newer {
        val rows = page.filter { it.id > after }.sortedBy { it.id }
        return Newer(rows, more = rows.size >= size)
    }

    /** The rows after a search window ([ChatSearchRules.newer]) and whether more follows them. */
    data class Newer(val rows: List<StoredMessage>, val more: Boolean)

    /** Whether the message [target] is among the rows the chat already has ([loadedIds]). */
    fun anchor(loadedIds: Collection<Long>, target: Long): Anchor = if (target in loadedIds) Anchor.Loaded else Anchor.Missing
}
