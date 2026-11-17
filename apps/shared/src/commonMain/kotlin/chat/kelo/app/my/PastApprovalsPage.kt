package chat.kelo.app.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.AgentViewModel
import chat.kelo.app.agent.ApprovalRecord
import chat.kelo.app.agent.ApprovalRecordRow
import chat.kelo.app.agent.DecisionBadge
import chat.kelo.app.agent.GroupCard
import chat.kelo.app.agent.PastApprovalsRules
import chat.kelo.app.agent.dayClockText
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.EmptyNote
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.text.*
import chat.kelo.app.ui.theme.Kelo

/**
 * 마이 → 권한 → 지난 승인: every approval the agent asked for, newest first, a page at a time. Tapping one shows what it was, where, when,
 * how it was answered and whether the message was fixed first. Empty and "could not load" have their own words.
 */
@Composable
fun PastApprovalsPage(onBack: () -> Unit) {
    val services = LocalAppServices.current
    val vm = rememberViewModel("my-past-approvals") { AgentViewModel(services.sessions) }
    val state by vm.past.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.loadPast() }
    var open by remember { mutableStateOf<ApprovalRecord?>(null) }
    val c = Kelo.colors
    SubPage(string(Res.string.approval_history_title), onBack) {
        val data = state.data
        when {
            data == null && state.failed -> {
                ErrorText(string(Res.string.approval_history_error), small = true)
                LinkButton(string(Res.string.retry), onClick = vm::loadPast)
            }
            data == null -> SkeletonRows(4)
            data.items.isEmpty() -> EmptyNote(string(Res.string.approval_history_empty))
            else -> {
                GroupCard {
                    data.items.forEachIndexed { i, a ->
                        if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                        ApprovalRecordRow(a, onClick = { open = a })
                    }
                }
                if (data.moreFailed) ErrorText(string(Res.string.approval_history_error), small = true)
                if (data.next != null && !data.loadingMore) LinkButton(string(Res.string.agent_more), onClick = vm::loadMorePast)
            }
        }
    }
    open?.let { PastApprovalSheet(it) { open = null } }
}

/** What one past approval was: the question, where, when, the answer and who gave it, and that the message was fixed. */
@Composable
private fun PastApprovalSheet(a: ApprovalRecord, onDismiss: () -> Unit) {
    val c = Kelo.colors
    KeloModal(onDismiss = onDismiss) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(a.label.ifBlank { a.tool }, style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.weight(1f))
                DecisionBadge(a.decision)
            }
            if (a.detail.isNotBlank()) Text(a.detail, style = MaterialTheme.typography.bodyMedium, color = c.ink)
            PastApprovalsRules.where(a).takeIf { it.isNotBlank() }?.let { PastLine(string(Res.string.approval_history_where), it) }
            if (a.askedAt > 0) PastLine(string(Res.string.approval_history_when), dayClockText(a.askedAt))
            PastApprovalsRules.answeredBy(a)?.let { who ->
                PastLine(string(Res.string.approval_history_answered), string(when (who) {
                    "notification" -> Res.string.approval_via_notification
                    "messenger" -> Res.string.approval_via_messenger
                    else -> Res.string.approval_via_app
                }))
            }
            if (a.edited) Text(string(Res.string.approval_edited_note), style = MaterialTheme.typography.bodySmall, color = c.muted)
            LinkButton(string(Res.string.close), onClick = onDismiss)
        }
    }
}

@Composable
private fun PastLine(label: String, value: String) {
    val c = Kelo.colors
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(0.35f))
        Text(value, style = MaterialTheme.typography.bodySmall, color = c.ink, modifier = Modifier.weight(0.65f))
    }
}
