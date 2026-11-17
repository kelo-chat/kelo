package chat.kelo.app.my

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.clockText
import chat.kelo.app.agent.dayLabel
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.ChoiceChip
import chat.kelo.app.ui.components.EmptyNote
import chat.kelo.app.ui.components.SectionLabel
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.keloViewModel
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/**
 * 마이 → Kelo가 한 일: what Kelo did outside, when, where and from which run (GET /api/actions).
 */
@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
fun ActionsPage(
    onBack: () -> Unit,
    onOpenTask: (Long) -> Unit = {},
    onOpenChat: () -> Unit = {},
) {
    val sessions = LocalAppServices.current.sessions
    val vm = keloViewModel("my-actions") { ActionsViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    val c = Kelo.colors

    LaunchedEffect(Unit) { vm.load() }

    SubPage(
        string(Res.string.actions_title),
        onBack,
        actions = {
            IconButton(onClick = { vm.refresh() }) {
                Icon(KeloIcons.Retry, contentDescription = string(Res.string.refresh), tint = c.muted)
            }
        },
    ) {
        PullToRefreshBox(
            isRefreshing = ui.loading,
            onRefresh = { vm.refresh() },
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(
                modifier = Modifier.fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                // Filter chips: 전체 / 보내기·바꾸기 / 브라우저 / 작업
                // a row that wraps: on a 320 dp screen (or in a longer language) the last chip would run off the edge
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth().padding(bottom = 2.dp),
                ) {
                    DidFilter.entries.forEach { f ->
                        ChoiceChip(
                            text = string(f.labelRes),
                            selected = ui.filter == f,
                            onClick = { vm.setFilter(f) },
                        )
                    }
                }

                // Period chips
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
                    modifier = Modifier.fillMaxWidth().padding(bottom = 4.dp),
                ) {
                    ActionPeriod.entries.forEach { p ->
                        ChoiceChip(
                            text = string(p.labelRes),
                            selected = ui.period == p,
                            onClick = { vm.setPeriod(p) },
                        )
                    }
                }

                if (ui.loading && ui.items.isEmpty()) {
                    SkeletonRows(rows = 3)
                } else if (ui.items.isEmpty()) {
                    EmptyNote(string(Res.string.actions_empty))
                } else {
                    ui.groups.forEach { group ->
                        SectionLabel(dayLabel(group.label))
                        group.items.forEach { item ->
                            ActionRow(item = item, onOpenTask = onOpenTask, onOpenChat = onOpenChat)
                        }
                    }
                }

                // Plain words about outside text: one muted sentence at the bottom
                Text(
                    string(Res.string.actions_outside_text_note),
                    color = c.muted,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 8.dp),
                )
            }
        }
    }
}

@Composable
internal fun ActionRow(
    item: ActionItem,
    onOpenTask: (Long) -> Unit,
    onOpenChat: () -> Unit = {},
) {
    val c = Kelo.colors
    val icon = when (item.normalizedKind) {
        "browser" -> KeloIcons.Globe
        "reply" -> KeloIcons.Chat
        "task" -> KeloIcons.Work
        "action" -> when (item.kind) {
            "send" -> KeloIcons.Send
            "change" -> KeloIcons.Edit
            "delete" -> KeloIcons.Trash
            else -> KeloIcons.Send
        }
        else -> KeloIcons.Spark
    }
    val clickMod = when {
        item.kind == "reply" -> Modifier.clickable(role = Role.Button) { onOpenChat() }
        item.taskId != null -> Modifier.clickable(role = Role.Button) { onOpenTask(item.taskId) }
        item.source == "chat" -> Modifier.clickable(role = Role.Button) { onOpenChat() }
        else -> Modifier
    }
    val time = clockText(item.timestamp)
    val origin = when {
        !item.site.isNullOrBlank() -> item.site
        !item.connector.isNullOrBlank() -> item.connector
        item.sourceName.isNotBlank() -> item.sourceName
        item.source == "schedule" -> string(Res.string.agent_source_schedule)
        item.source == "job" -> string(Res.string.work_source_job)
        item.source == "chat" -> string(Res.string.tab_chat)
        else -> null
    }
    val subtitle = listOfNotNull(origin, time.ifBlank { null }).joinToString(" · ")
    val outcome = item.displayOutcome
    val tagText = when (outcome) {
        "unknown" -> string(Res.string.actions_state_unknown)
        "failed" -> string(Res.string.actions_state_failed)
        "stopped", "interrupted" -> string(Res.string.agent_task_stopped)
        else -> null
    }
    val tagColor = when (outcome) {
        "failed" -> c.danger
        "unknown" -> c.warn
        else -> c.muted
    }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .then(clickMod)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Icon(icon, contentDescription = null, tint = c.muted, modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f)) {
            Text(
                item.displayTitle,
                color = c.ink,
                style = MaterialTheme.typography.bodyLarge,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            if (item.detail.isNotBlank() && item.detail != item.displayTitle) {
                Text(
                    item.detail,
                    color = c.muted,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            if (subtitle.isNotEmpty()) {
                Text(
                    subtitle,
                    color = c.muted,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
        if (tagText != null) {
            Box(
                modifier = Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .background(tagColor.copy(alpha = 0.15f))
                    .padding(horizontal = 6.dp, vertical = 2.dp),
            ) {
                Text(
                    tagText,
                    color = tagColor,
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Medium,
                )
            }
        }
    }
}
