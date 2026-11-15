package chat.kelo.app.my

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.Card
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SkeletonLines
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.text.DateFormat
import java.util.Date

/** One request to try Google sign-in, as GET /api/operator/beta lists it. */
@Serializable
data class BetaRequestRow(
    val id: Long = 0,
    @SerialName("created_at") val createdAt: Double = 0.0,
    val email: String = "",
    val products: List<String> = emptyList(),
    val status: String = "requested",
    /** the email to support when it came in: "sent", "no_mailbox", "failed" or "" (still going) */
    val notice: String = "",
)

@Serializable
data class BetaRequests(
    val requests: List<BetaRequestRow> = emptyList(),
    val open: Int = 0,
    @SerialName("mail_from") val mailFrom: String = "",
    val receiving: Boolean = false,
)

@Serializable
data class BetaAnswer(
    val mailed: Boolean = false,
    @SerialName("mail_error") val mailError: String = "",
)

object OperatorBetaRules {
    /** "Gmail, Google Calendar" (product names stay as they are in every language). */
    fun products(ids: List<String>): String = ids.mapNotNull { mapOf("gmail" to "Gmail", "calendar" to "Google Calendar")[it] }.joinToString(", ")

    /** Approved / Decline show only while a request waits; an approved one can still be taken back with Decline. */
    fun canApprove(r: BetaRequestRow): Boolean = r.status == "requested"
    fun canDecline(r: BetaRequestRow): Boolean = r.status != "declined"
}

/** 운영자 → 베타 신청: people asking to try Google sign-in while the Google app is in Testing mode; Approved or Decline. */
@Composable
fun BetaRequestsCard() {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var data by remember { mutableStateOf<BetaRequests?>(null) }
    var failed by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    var msg by remember { mutableStateOf("") }
    var declining by remember { mutableStateOf<BetaRequestRow?>(null) }
    var deleting by remember { mutableStateOf<Long?>(null) }
    suspend fun load() {
        runCatching { sessions.requireApi().get<BetaRequests>("/api/operator/beta") }
            .onSuccess { data = it; failed = false }.onFailure { failed = true }
    }
    LaunchedEffect(Unit) { load() }
    val mailedText = stringResource(R.string.operator_beta_mailed, "%s")
    fun answer(r: BetaRequestRow, action: String, mail: Boolean) {
        err = ""; msg = ""
        scope.launch {
            try {
                val a = sessions.requireApi().post<BetaAnswer>("/api/operator/beta/${r.id}", buildJsonObject { put("action", action); put("email", mail) })
                msg = if (a.mailed) mailedText.replace("%s", r.email) else a.mailError
                load()
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }
    Card {
        MutedText(stringResource(R.string.operator_beta_note), small = true)
        val d = data ?: run {
            if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else SkeletonLines(2)
            return@Card
        }
        if (!d.receiving) MutedText(stringResource(R.string.operator_beta_not_receiving), small = true)
        if (d.mailFrom.isNotBlank()) MutedText(stringResource(R.string.operator_beta_mail_from, d.mailFrom), small = true)
        else Text(stringResource(R.string.operator_beta_no_mail), color = Kelo.colors.danger, style = MaterialTheme.typography.bodySmall)
        Text(stringResource(R.string.operator_beta_open, d.open), color = Kelo.colors.ink, fontWeight = FontWeight.SemiBold,
            style = MaterialTheme.typography.bodyMedium)
        if (d.requests.isEmpty()) MutedText(stringResource(R.string.operator_beta_empty), small = true)
        for (r in d.requests) BetaRow(r, onApprove = { answer(r, "approve", true) }, onDecline = { declining = r }, onDelete = { deleting = r.id })
        if (msg.isNotEmpty()) MutedText(msg, small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
    declining?.let { r ->
        var mail by remember(r.id) { mutableStateOf(false) }
        val c = Kelo.colors
        AlertDialog(
            onDismissRequest = { declining = null },
            containerColor = c.card,
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(stringResource(R.string.operator_beta_decline_confirm), color = c.ink, style = MaterialTheme.typography.bodyLarge)
                    Text(r.email, color = c.muted, style = MaterialTheme.typography.bodySmall)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Checkbox(checked = mail, onCheckedChange = { mail = it },
                            colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent))
                        Text(stringResource(R.string.operator_beta_decline_mail), color = c.ink, style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.weight(1f))
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { declining = null; answer(r, "decline", mail) }) {
                    Text(stringResource(R.string.operator_beta_decline), color = c.danger)
                }
            },
            dismissButton = { TextButton(onClick = { declining = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    }
    deleting?.let { id ->
        ConfirmDialog(stringResource(R.string.operator_reports_delete_confirm), stringResource(R.string.operator_reports_delete), onDismiss = { deleting = null }, onYes = {
            deleting = null
            scope.launch {
                try {
                    sessions.requireApi().request("DELETE", "/api/operator/beta/$id")
                    load()
                } catch (e: ApiError) {
                    err = e.detail
                }
            }
        })
    }
}

@Composable
private fun BetaRow(r: BetaRequestRow, onApprove: () -> Unit, onDecline: () -> Unit, onDelete: () -> Unit) {
    val c = Kelo.colors
    val whenText = remember(r.createdAt) { DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT).format(Date((r.createdAt * 1000).toLong())) }
    val (stateText, stateColor) = when (r.status) {
        "approved" -> stringResource(R.string.operator_beta_state_approved) to c.success
        "declined" -> stringResource(R.string.operator_beta_declined) to c.muted
        else -> stringResource(R.string.operator_beta_waiting) to c.accent
    }
    Column(
        Modifier.fillMaxWidth().background(c.bubble, RoundedCornerShape(12.dp)).padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(r.email, color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        Text(OperatorBetaRules.products(r.products) + " · " + whenText, color = c.muted, style = MaterialTheme.typography.bodySmall)
        Text(stateText, color = stateColor, style = MaterialTheme.typography.bodySmall)
        when (r.notice) {
            "no_mailbox" -> Text(stringResource(R.string.operator_beta_notice_no_mail), color = c.muted, style = MaterialTheme.typography.bodySmall)
            "failed" -> Text(stringResource(R.string.operator_beta_notice_failed), color = c.danger, style = MaterialTheme.typography.bodySmall)
        }
        if (OperatorBetaRules.canApprove(r)) {
            Text(stringResource(R.string.operator_beta_steps, r.email), color = c.ink, style = MaterialTheme.typography.bodySmall)
            PrimaryButton(stringResource(R.string.operator_beta_approve), onClick = onApprove)
        }
        @OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            if (OperatorBetaRules.canDecline(r)) SecondaryButton(stringResource(R.string.operator_beta_decline), onClick = onDecline)
            LinkButton(stringResource(R.string.operator_reports_delete), onClick = onDelete, danger = true)
        }
    }
}
