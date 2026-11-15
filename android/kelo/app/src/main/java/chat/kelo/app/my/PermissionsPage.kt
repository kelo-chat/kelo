package chat.kelo.app.my

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.HorizontalDivider
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.agent.AgentViewModel
import chat.kelo.app.agent.ApprovalRecordRow
import chat.kelo.app.agent.GroupCard
import chat.kelo.app.agent.RuleRow
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.theme.Kelo

/**
 * 마이 → 권한: the sites where the agent may act without asking (each can be taken back) and every question it asked before with
 * the answer it got. Questions still waiting are answered in the 작업 tab.
 */
@Composable
fun PermissionsPage(onBack: () -> Unit, onOpenWork: () -> Unit) {
    val container = LocalAppContainer.current
    val vm: AgentViewModel = viewModel { AgentViewModel(container.sessions) }
    val state by vm.perms.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) { vm.loadPerms() }
    val c = Kelo.colors
    SubPage(stringResource(R.string.agent_tab_permissions), onBack) {
        MutedText(stringResource(R.string.my_perm_intro), small = true)
        chat.kelo.app.device.DeviceSection()   // what this phone shares with the user's server, each only when turned on here
        val data = state.data
        if (data == null) {
            if (state.failed) ErrorText(stringResource(R.string.connect_failed), small = true) else SkeletonRows(4)
            return@SubPage
        }
        if (data.pending.isNotEmpty()) {
            MyCard(onClick = onOpenWork) {
                androidx.compose.material3.Text(
                    pluralStringResource(R.plurals.my_perm_waiting, data.pending.size, data.pending.size),
                    color = c.accent, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium,
                )
            }
        }
        SectionLabel(stringResource(R.string.agent_perm_rules))
        if (data.rules.isEmpty()) {
            MutedText(stringResource(R.string.agent_perm_rules_empty), small = true)
        } else {
            GroupCard {
                data.rules.forEachIndexed { i, r ->
                    if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                    RuleRow(r, onRevoke = { vm.revokeRule(r) })
                }
            }
        }
        SectionLabel(stringResource(R.string.agent_perm_history))
        if (data.history.isEmpty()) {
            EmptyNote(stringResource(R.string.agent_perm_history_empty))
        } else {
            GroupCard {
                data.history.forEachIndexed { i, a ->
                    if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                    ApprovalRecordRow(a)
                }
            }
        }
        if (data.historyNext != null) LinkButton(stringResource(R.string.agent_more), onClick = vm::loadMoreHistory)
    }
    VmMessages(vm)
}

/** The view model's short messages as toasts (sub pages of 마이 have no snackbar of their own). */
@Composable
internal fun VmMessages(vm: AgentViewModel) {
    val context = androidx.compose.ui.platform.LocalContext.current
    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            val text = if (m.detail != null) context.getString(m.text, m.detail) else context.getString(m.text, *m.args.toTypedArray())
            android.widget.Toast.makeText(context, text, android.widget.Toast.LENGTH_SHORT).show()
        }
    }
}
