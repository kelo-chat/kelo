package chat.kelo.app.agent

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import chat.kelo.app.chat.ApprovalLine
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.text.*
import chat.kelo.app.ui.theme.Kelo
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull

/**
 * The outgoing message of an approval card the owner may fix before allowing it (`"editable"` of a pending item: only tools that
 * SEND a message, never a payment). [field] is the argument the server replaces, [text] the message as it stands, [max] the
 * service's own limit (a text message) or null.
 */
data class EditableMessage(val field: String, val text: String, val max: Int?) {
    companion object {
        fun parse(e: JsonElement?): EditableMessage? {
            val o = e as? JsonObject ?: return null
            val field = (o["field"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.ifBlank { null } ?: return null
            val text = (o["text"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: return null
            return EditableMessage(field, text, (o["max"] as? JsonPrimitive)?.intOrNull)
        }
    }
}

/** What is wrong with an edited message, as the sheet says it. */
enum class EditProblem { EMPTY, TOO_LONG }

/** The rules of the edit sheet and of the card around it (the server checks the same two again). */
object ApprovalEditRules {
    /** Nothing to save: empty (blank), or over the service's limit. */
    fun problem(text: String, max: Int?): EditProblem? = when {
        text.isBlank() -> EditProblem.EMPTY
        max != null && text.trim().length > max -> EditProblem.TOO_LONG
        else -> null
    }

    /** "n / max" for the counter, only when the service has a limit. */
    fun counter(text: String, max: Int?): Pair<Int, Int>? = max?.let { text.trim().length to it }

    /** What the card shows as the message: the owner's saved text, else the server's. */
    fun shown(e: EditableMessage, draft: String?): String = draft ?: e.text

    /** The readable pairs without the one that is the message itself (the card shows the message in its own block). */
    fun withoutMessage(lines: List<ApprovalLine>, e: EditableMessage): List<ApprovalLine> =
        lines.filter { it.value.trim() != e.text.trim() }

    /** The text to send as `edited`, or null when nothing was changed (a saved text equal to the original is no edit). */
    fun toSend(e: EditableMessage?, draft: String?): String? =
        if (e == null || draft == null || draft.trim() == e.text.trim()) null else draft.trim()
}

/**
 * The texts the owner fixed in approval cards, by approval id, until the answer goes out. The card writes it when the sheet is saved;
 * every place that answers an approval reads it with [toSend] and clears it once the server took the answer.
 */
object ApprovalDrafts {
    private val drafts = HashMap<String, String>()

    fun get(id: String): String? = drafts[id]
    fun set(id: String, text: String) { drafts[id] = text }
    fun clear(id: String) { drafts.remove(id) }

    /** The `edited` object of an Allow for [e] with this approval's saved text, or null. */
    fun edited(id: String, e: EditableMessage?): JsonObject? =
        ApprovalEditRules.toSend(e, drafts[id])?.let { JsonObject(mapOf(e!!.field to JsonPrimitive(it))) }
}

/**
 * The message an approval will send, with "Edit" under it. Saving the sheet keeps the text for the Allow; the card then shows it with
 * "You changed the message". [onChanged] lets the card redraw.
 */
@Composable
fun EditableMessageBlock(approvalId: String, e: EditableMessage, enabled: Boolean) {
    val c = Kelo.colors
    var draft by remember(approvalId) { mutableStateOf(ApprovalDrafts.get(approvalId)) }
    var editing by remember(approvalId) { mutableStateOf(false) }
    val text = ApprovalEditRules.shown(e, draft)
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Column(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(c.bubble).padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(text, style = MaterialTheme.typography.bodyMedium, color = c.ink,
                modifier = Modifier.fillMaxWidth().heightIn(max = 160.dp).verticalScroll(rememberScrollState()))
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (ApprovalEditRules.toSend(e, draft) != null) {
                Text(string(Res.string.approval_edited_note), style = MaterialTheme.typography.labelSmall, color = c.muted, modifier = Modifier.weight(1f))
            } else androidx.compose.foundation.layout.Spacer(Modifier.weight(1f))
            if (enabled) LinkButton(string(Res.string.approval_edit_button), onClick = { editing = true })
        }
    }
    if (editing) {
        EditMessageSheet(text, e.max, onDismiss = { editing = false }) { saved ->
            ApprovalDrafts.set(approvalId, saved)
            draft = saved
            editing = false
        }
    }
}

/** The multi-line sheet: the text, a counter when the service has a limit, and the two refusals (empty, too long) next to the Save button. */
@Composable
fun EditMessageSheet(initial: String, max: Int?, onDismiss: () -> Unit, onSave: (String) -> Unit) {
    val c = Kelo.colors
    var message by remember { mutableStateOf(initial) }
    var tried by remember { mutableStateOf(false) }
    val problem = ApprovalEditRules.problem(message, max)
    KeloModal(onDismiss = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(string(Res.string.approval_edit_title), style = MaterialTheme.typography.titleMedium, color = c.ink)
            KeloTextField(message, { message = it }, string(Res.string.approval_edit_title), singleLine = false, modifier = Modifier.heightIn(min = 140.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val said = if (problem != null && (tried || problem == EditProblem.TOO_LONG)) string(
                    if (problem == EditProblem.EMPTY) Res.string.approval_edit_empty else Res.string.approval_edit_too_long,
                ) else ""
                Text(said, style = MaterialTheme.typography.bodySmall, color = c.danger, modifier = Modifier.weight(1f))
                ApprovalEditRules.counter(message, max)?.let { (n, m) ->
                    Text(string(Res.string.approval_edit_count, n, m), style = MaterialTheme.typography.bodySmall, color = if (n > m) c.danger else c.muted)
                }
            }
            PrimaryButton(string(Res.string.approval_edit_save), modifier = Modifier.fillMaxWidth(), onClick = {
                tried = true
                if (problem == null) onSave(message.trim())
            })
        }
    }
}
