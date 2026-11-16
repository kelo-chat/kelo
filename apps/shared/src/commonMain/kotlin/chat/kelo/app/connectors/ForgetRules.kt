package chat.kelo.app.connectors

import chat.kelo.app.core.Sessions
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.loadString
import org.jetbrains.compose.resources.StringResource

/** A disconnected connector (or a deleted login) left data behind: [id] is the credential name the data is kept under. */
data class ForgetOffer(val id: String, val name: String, val data: ConnectorData)

/** One kind of kept data, in the order the dialog lists them. */
enum class ForgetPart { FILES, NOTES, ITEMS }

/** Whether to ask about forgetting the data a disconnect left, and what the question says (the counts, the trash note). */
object ForgetRules {
    /** Ask only when the server sent a "data" answer and something is kept. An older server (no "data") is never asked. */
    fun offer(data: ConnectorData?): Boolean = data != null && data.total > 0

    /** The kinds with something in them, zero kinds left out. */
    fun parts(data: ConnectorData): List<Pair<ForgetPart, Int>> =
        listOf(ForgetPart.FILES to data.files, ForgetPart.NOTES to data.notes, ForgetPart.ITEMS to data.items).filter { it.second > 0 }

    /** Files go to the library's trash, so the question says so. */
    fun mentionsTrash(data: ConnectorData): Boolean = data.files > 0

    fun resource(part: ForgetPart): StringResource = when (part) {
        ForgetPart.FILES -> Res.string.connector_forget_files
        ForgetPart.NOTES -> Res.string.connector_forget_notes
        ForgetPart.ITEMS -> Res.string.connector_forget_items
    }
}

/** "파일 3개 · 메모 2개 · 항목 5개" (the parts that are not zero), in the app's language. */
suspend fun forgetCountsText(data: ConnectorData): String =
    ForgetRules.parts(data).map { (part, n) -> loadString(ForgetRules.resource(part), n) }.joinToString(" · ")

/**
 * After a disconnect (or a login deleted from the vault): offer to forget what it left, when something is kept. [id] is the credential
 * name; the shown name is the connector's own name when the list has it, else [fallbackName].
 */
internal suspend fun Sessions.askToForget(id: String, data: ConnectorData?, fallbackName: String) {
    if (!ForgetRules.offer(data)) return
    val name = runCatching { ConnectorCalls.list(requireApi()).firstOrNull { it.item == id }?.name }.getOrNull()
        ?.takeIf { it.isNotBlank() } ?: fallbackName
    offerForget(ForgetOffer(id, name, data!!))
}
