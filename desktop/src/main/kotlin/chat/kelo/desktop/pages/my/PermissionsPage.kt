package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.ApprovalHistory
import chat.kelo.app.agent.ApprovalRecord
import chat.kelo.app.agent.ApprovalRule
import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.agent.RuleList
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.my.ops.Loading
import chat.kelo.desktop.pages.my.ops.whenText
import chat.kelo.desktop.ui.Badge
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement

private const val PAGE = 50

/** What the page shows: the questions still waiting, the standing rules and the answered questions. */
private data class Perms(val pending: Int, val rules: List<ApprovalRule>, val history: List<ApprovalRecord>, val next: Double?)

/**
 * 마이 → 권한 (android/kelo .../my/PermissionsPage.kt): the sites where the agent may act without asking (each can be taken back) and
 * every question it asked before with the answer it got. Questions still waiting are answered in 작업 ([onOpenWork]). The phone's
 * section of what that phone shares (contacts, calendar, health…) has no counterpart on a desktop.
 */
@Composable
fun PermissionsPage(app: AppContainer, onBack: () -> Unit, onOpenWork: () -> Unit) {
    val sessions = app.sessions
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var data by remember { mutableStateOf<Perms?>(null) }
    var failed by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf("") }
    suspend fun load() {
        try {
            val api = sessions.requireApi()
            val pending = PendingApproval.parseList(api.request("GET", "/api/approvals/pending")).size
            val rules = api.get<RuleList>("/api/approvals/rules").items
            val history = api.get<ApprovalHistory>("/api/approvals/history?limit=$PAGE")
            data = Perms(pending, rules, history.items, history.nextBefore)
            failed = false
        } catch (e: ApiError) {
            failed = true
        }
    }
    LaunchedEffect(Unit) { load() }
    fun revoke(r: ApprovalRule) {
        data = data?.let { d -> d.copy(rules = d.rules.filter { it.id != r.id }) }
        scope.launch {
            try {
                sessions.requireApi().delete<JsonElement>("/api/approvals/rules/${r.id}")
                msg = strings.get(R.string.agent_rule_revoked, r.site)
            } catch (e: ApiError) {
                if (e.status != 404) { msg = e.detail; load() }
            }
        }
    }
    fun more() {
        val d = data ?: return
        val before = d.next ?: return
        scope.launch {
            runCatching { sessions.requireApi().get<ApprovalHistory>("/api/approvals/history?limit=$PAGE&before=$before") }.onSuccess { h ->
                data = data?.let { it.copy(history = it.history + h.items, next = h.nextBefore) }
            }
        }
    }
    val c = Kelo.colors
    SubPage(stringResource(R.string.agent_tab_permissions), onBack = onBack) {
        MutedText(stringResource(R.string.my_perm_intro), small = true)
        val d = data
        if (d == null) {
            if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else Loading()
            return@SubPage
        }
        if (d.pending > 0) {
            MyCard(onClick = onOpenWork) {
                Text(pluralStringResource(R.plurals.my_perm_waiting, d.pending, d.pending), color = c.accent, style = MaterialTheme.typography.bodyMedium)
            }
        }
        if (msg.isNotEmpty()) MutedText(msg, small = true)
        SectionLabel(stringResource(R.string.agent_perm_rules))
        if (d.rules.isEmpty()) {
            MutedText(stringResource(R.string.agent_perm_rules_empty), small = true)
        } else {
            GroupCard {
                d.rules.forEachIndexed { i, r ->
                    if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                    RuleRow(r, onRevoke = { revoke(r) })
                }
            }
        }
        SectionLabel(stringResource(R.string.agent_perm_history))
        if (d.history.isEmpty()) {
            EmptyNote(stringResource(R.string.agent_perm_history_empty))
        } else {
            GroupCard {
                d.history.forEachIndexed { i, a ->
                    if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                    RecordRow(a)
                }
            }
        }
        if (d.next != null) LinkButton(stringResource(R.string.agent_more), onClick = ::more)
    }
}

@Composable
private fun GroupCard(content: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp))) { content() }
}

@Composable
private fun RuleRow(r: ApprovalRule, onRevoke: () -> Unit) {
    val c = Kelo.colors
    var confirm by remember { mutableStateOf(false) }
    Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 10.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(r.site, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(when (r.kind) {
                "browser_action" -> stringResource(R.string.agent_rule_kind_browser)
                "login" -> stringResource(R.string.agent_rule_kind_login)
                else -> r.kindLabel.ifBlank { r.kind }
            }, style = MaterialTheme.typography.bodySmall, color = c.muted)
            Text(pluralStringResource(R.plurals.agent_rule_uses, r.uses, r.uses), style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        IconButton(onClick = { confirm = true }) { Icon(KeloIcons.Trash, contentDescription = stringResource(R.string.agent_rule_revoke, r.site), tint = c.muted) }
    }
    if (confirm) ConfirmDialog(stringResource(R.string.agent_rule_revoke_confirm, r.site), stringResource(R.string.agent_rule_revoke_yes), onDismiss = { confirm = false }, onYes = onRevoke)
}

@Composable
private fun RecordRow(a: ApprovalRecord) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(a.label.ifBlank { a.tool }, style = MaterialTheme.typography.bodyMedium, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
            val sub = listOf(a.site, a.detail).filter { it.isNotBlank() }.joinToString(" · ")
            if (sub.isNotEmpty()) Text(sub, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (a.askedAt > 0) Text(whenText(a.askedAt), style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        Badge(stringResource(when (a.decision) {
            "allowed_once" -> R.string.agent_decision_once
            "allowed_always" -> R.string.agent_decision_always
            "allowed_by_rule" -> R.string.agent_decision_rule
            "denied" -> R.string.agent_decision_denied
            "expired" -> R.string.agent_decision_expired
            "cancelled" -> R.string.agent_decision_cancelled
            else -> R.string.agent_decision_pending
        }), denied = a.decision == "denied")
    }
}
