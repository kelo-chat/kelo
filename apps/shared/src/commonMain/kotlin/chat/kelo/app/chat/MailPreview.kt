package chat.kelo.app.chat

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.intOrNull

/** A designed mail's card part (server "mail"): where its preview is and what the owner should know. */
data class MailDesign(val previewPath: String, val pictures: Int, val links: Int) {
    companion object {
        /** null unless the server says the mail is designed and its preview is under /api/approvals/ (never a link out). */
        fun parse(e: JsonElement?): MailDesign? {
            val o = e as? JsonObject ?: return null
            val path = MailPreviewRules.text(o["preview"])
            if (MailPreviewRules.bool(o["designed"]) != true || !path.startsWith("/api/approvals/")) return null
            return MailDesign(path, MailPreviewRules.int(o["pictures"]), MailPreviewRules.int(o["links"]))
        }
    }
}

/** The preview the server sent (GET /api/approvals/{id}/mail-preview). [html] is the sanitized mail, shown by a web view with no script. */
data class MailPreview(val html: String, val blocks: List<MailBlock>, val cut: Boolean, val pictures: Int, val links: Int)

/** A piece of text with its look. [color] is ARGB; [href] is a link that may be opened after the owner confirms ("" when none). */
data class MailSpan(
    val text: String, val bold: Boolean, val italic: Boolean, val underline: Boolean, val strike: Boolean, val color: Long?, val href: String,
)

/** One cell of a table row. */
data class MailCell(val spans: List<MailSpan>, val bg: Long?, val align: MailAlign)

/** A block of the preview as plain structure (the desktop app draws these; a web view draws the document). */
data class MailBlock(
    val kind: Kind, val level: Int, val ordinal: Int, val depth: Int, val align: MailAlign, val bg: Long?,
    val spans: List<MailSpan>, val cells: List<MailCell>,
) {
    enum class Kind { HEADING, PARAGRAPH, ITEM, QUOTE, RULE, ROW }
}

enum class MailAlign { START, CENTER, END }

object MailPreviewRules {
    private val OPENABLE = setOf("http", "https", "mailto")
    private val HEX = Regex("#[0-9a-fA-F]{6}")

    /** The address a tapped link may open outside the app after the owner confirms: http, https, mailto only (null otherwise). */
    fun linkToOpen(url: String?): String? {
        if (url == null || url.any { it.code < 0x21 || it.code == 0x7f }) return null
        val scheme = url.substringBefore(':', "").lowercase()
        return if (scheme in OPENABLE && url.substringAfter(':', "").isNotEmpty()) url else null
    }

    /** What the confirm dialog shows first: the host (or the mail address), never cut mid-word. The whole address is shown below it. */
    fun linkLabel(url: String): String {
        val scheme = url.substringBefore(':').lowercase()
        val rest = url.substringAfter(':')
        if (scheme == "mailto") return rest.substringBefore('?')
        val host = rest.removePrefix("//").substringBefore('/').substringBefore('?').substringBefore('#').substringAfterLast('@')
        return host.ifEmpty { url }
    }

    /** The server's answer (the whole JSON), or null when it is not a preview. Unknown block kinds are dropped; colours are #rrggbb only. */
    fun parse(json: JsonElement): MailPreview? {
        val o = json as? JsonObject ?: return null
        val html = (o["html"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: return null
        val blocks = (o["blocks"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::block) }
        return MailPreview(html, blocks, bool(o["cut"]) == true, int(o["pictures"]), int(o["links"]))
    }

    private fun block(o: JsonObject): MailBlock? {
        val kind = when (text(o["kind"])) {
            "heading" -> MailBlock.Kind.HEADING
            "paragraph" -> MailBlock.Kind.PARAGRAPH
            "item" -> MailBlock.Kind.ITEM
            "quote" -> MailBlock.Kind.QUOTE
            "rule" -> MailBlock.Kind.RULE
            "row" -> MailBlock.Kind.ROW
            else -> return null
        }
        val cells = (o["cells"] as? JsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::cell) }
        return MailBlock(kind, int(o["level"]), int(o["ordinal"]), int(o["depth"]), align(o["align"]), colour(o["bg"]), spans(o["spans"]), cells)
    }

    private fun cell(o: JsonObject) = MailCell(spans(o["spans"]), colour(o["bg"]), align(o["align"]))

    private fun spans(e: JsonElement?): List<MailSpan> = (e as? JsonArray).orEmpty().mapNotNull { item ->
        val o = item as? JsonObject ?: return@mapNotNull null
        MailSpan(
            text(o["text"]), bool(o["b"]) == true, bool(o["i"]) == true, bool(o["u"]) == true, bool(o["s"]) == true,
            colour(o["color"]), linkToOpen(text(o["href"])).orEmpty(),
        )
    }

    private fun align(e: JsonElement?): MailAlign = when (text(e)) {
        "center" -> MailAlign.CENTER
        "end" -> MailAlign.END
        else -> MailAlign.START
    }

    private fun colour(e: JsonElement?): Long? {
        val s = text(e)
        return if (HEX.matches(s)) 0xFF000000L or s.substring(1).toLong(16) else null
    }

    internal fun text(e: JsonElement?): String = (e as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
    internal fun bool(e: JsonElement?): Boolean? = (e as? JsonPrimitive)?.booleanOrNull
    internal fun int(e: JsonElement?): Int = (e as? JsonPrimitive)?.intOrNull ?: 0
}

/**
 * The fetched previews by approval id, so an answered card keeps showing what the owner approved until the app restarts (the newest
 * eight). A preview is fetched once per card; a failure may be tried again.
 */
class MailPreviewStore(private val api: () -> KeloApi?) {
    sealed interface State {
        data object Idle : State
        data object Loading : State
        data class Ready(val p: MailPreview) : State
        /** 404: the card is no longer waiting and nothing was fetched before */
        data object Gone : State
        /** 413: the preview is larger than the server shows */
        data object TooBig : State
        data object Failed : State
    }

    private val flows = object : LinkedHashMap<String, MutableStateFlow<State>>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, MutableStateFlow<State>>?): Boolean = size > KEEP
    }
    private val lock = Any()
    private var owner: KeloApi? = null

    fun state(approvalId: String): StateFlow<State> = synchronized(lock) { slot(approvalId) }.asStateFlow()

    /** The previews belong to one session: a new api (signing out, pairing again) drops them. */
    private fun slot(approvalId: String): MutableStateFlow<State> {
        val now = api()
        if (now !== owner) {
            flows.clear()
            owner = now
        }
        return flows.getOrPut(approvalId) { MutableStateFlow(State.Idle) }
    }

    suspend fun load(approvalId: String, path: String) {
        val slot = synchronized(lock) {
            slot(approvalId).also { f ->
                if (f.value is State.Idle || f.value is State.Failed) f.value = State.Loading else return
            }
        }
        slot.value = try {
            val client = api()
            if (client == null) State.Failed
            else MailPreviewRules.parse(client.request("GET", path))?.let { State.Ready(it) } ?: State.Failed
        } catch (e: CancellationException) {
            slot.value = State.Idle
            throw e
        } catch (e: ApiError) {
            when (e.status) {
                404 -> State.Gone
                413 -> State.TooBig
                else -> State.Failed
            }
        } catch (e: Exception) {
            State.Failed
        }
    }

    private companion object {
        const val KEEP = 8
    }
}
