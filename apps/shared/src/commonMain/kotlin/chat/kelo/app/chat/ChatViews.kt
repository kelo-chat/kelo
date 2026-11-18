package chat.kelo.app.chat

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * One browser tab or the phone the server shows in the chat (GET /api/chat/views, chat_views.py). [key]: the tab ("owner", a device id,
 * "task:…") or "android" for the phone; [kind] "browser" or "phone"; [anchorId]: the stored message the newest card goes after.
 */
@Serializable
data class ChatViewItem(
    val key: String,
    val kind: String = "browser",
    @SerialName("owner_asked") val ownerAsked: Boolean = false,
    @SerialName("worker_id") val workerId: String? = null,
    @SerialName("anchor_id") val anchorId: Long = 0,
    val title: String = "",
    val url: String = "",
    val host: String = "",
    val status: String = "",
    val image: String? = null,
    val width: Int = 0,
    val height: Int = 0,
)

/** The answer of GET /api/chat/views: [unchanged] when the `since` version is still the newest (no pictures sent), [at] the server's clock. */
@Serializable
data class ChatViewsAnswer(
    val version: Long = 0,
    val unchanged: Boolean = false,
    val items: List<ChatViewItem> = emptyList(),
    val at: Double = 0.0,
)

/** The key the phone's view has (its live source and its card's tab). */
internal const val PHONE_VIEW_KEY = "android"

/**
 * The rows after the server's views are compared with the shown rows (the cards that are not stored messages):
 * (a) a card whose view is listed takes the view's words and picture; (b) a card whose view is gone is removed, when the card is
 * older than the answer ([checkedAt], unix seconds, minus 5 s) so a card that came a moment before the answer stays; (c) when
 * [insert] a view with no card gets one, after the stored message [ChatViewItem.anchorId] (or at the top or end when that message
 * is not on the page).
 */
internal fun applyViews(items: List<ChatItem>, views: List<ChatViewItem>, checkedAt: Double, insert: Boolean = true): List<ChatItem> {
    val byKey = views.associateBy { it.key }
    val present = HashSet<String>()
    val out = ArrayList<ChatItem>(items.size + views.size)
    for (item in items) {
        if (item !is ChatItem.Browser) {
            out += item
            continue
        }
        val key = if (item.device) PHONE_VIEW_KEY else item.tab
        val view = byKey[key]
        when {
            view != null -> {
                present += key
                out += item.copy(
                    title = view.title.ifEmpty { item.title }, url = view.url.ifEmpty { item.url }, host = view.host.ifEmpty { item.host },
                    status = view.status.ifEmpty { item.status },
                    image = view.image ?: item.image,
                    width = if (view.image != null) view.width else item.width, height = if (view.image != null) view.height else item.height,
                )
            }
            item.at > 0 && item.at < checkedAt - 5 -> Unit   // closed on the server: its card goes
            else -> out += item
        }
    }
    if (!insert) return out
    for (view in views) {
        if (view.key in present) continue
        val card = ChatItem.Browser(
            key = "view:${view.key}", id = "", title = view.title, host = view.host, url = view.url, status = view.status,
            image = view.image, width = view.width, height = view.height, tab = if (view.kind == "phone") PHONE_VIEW_KEY else view.key,
            device = view.kind == "phone", at = checkedAt,
        )
        out.add(cardPosition(out, view.anchorId), card)
    }
    return out
}

/** Where a view's card goes: after the stored message with [anchorId]; the end when that id is unknown and newer than the page. */
private fun cardPosition(out: List<ChatItem>, anchorId: Long): Int {
    val msgs = out.withIndex().filter { (_, it) -> it is ChatItem.Msg && it.id != null }
    if (anchorId <= 0 || msgs.isEmpty()) return out.size
    msgs.firstOrNull { (it.value as ChatItem.Msg).id == anchorId }?.let { return it.index + 1 }
    if (anchorId > (msgs.last().value as ChatItem.Msg).id!!) return out.size
    return (msgs.lastOrNull { (it.value as ChatItem.Msg).id!! < anchorId }?.index ?: -1) + 1
}
