package chat.kelo.app.follow

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.agent.clockText
import chat.kelo.app.agent.dayClockText
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.work.EntryBox
import chat.kelo.app.work.RailItem
import chat.kelo.app.work.RailMarker
import chat.kelo.app.work.StatusChip

/** One row in the "Kelo가 챙기는 일" section. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FollowItemRow(item: FollowItem, onClick: () -> Unit) {
    val marker = when (item.state) {
        "stopped" -> RailMarker.PAUSED
        "done" -> RailMarker.DONE
        else -> RailMarker.LIVE
    }
    val timeLabel = if (item.startsAt > 0) clockText(item.startsAt) else null
    val c = Kelo.colors

    RailItem(marker = marker, label = timeLabel) {
        EntryBox(onClick = onClick) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    item.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.Bold,
                    color = c.ink,
                    modifier = Modifier.weight(1f, fill = false),
                )
                if (item.state == "stopped") {
                    StatusChip(string(Res.string.follow_state_stopped), c.muted)
                }
            }

            if (item.startsAt > 0) {
                Text(
                    dayClockText(item.startsAt),
                    style = MaterialTheme.typography.bodySmall,
                    color = c.muted,
                )
            }

            // One line of what Kelo does ("출발 하루 전 알림 · 체크인 열리면 준비")
            val summary = followPlanSummary(item)
            if (summary.isNotBlank()) {
                Text(
                    summary,
                    style = MaterialTheme.typography.bodySmall,
                    color = c.ink,
                )
            }

            // Prep states (예정 / 하는 중 / 해 뒀어요 / 실패)
            if (item.prep.isNotEmpty()) {
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                    modifier = Modifier.padding(top = 2.dp, bottom = 4.dp),
                ) {
                    item.prep.forEach { p ->
                        val label = if (p.what.isNotBlank()) "${p.what}: ${prepStateText(p.state)}" else prepStateText(p.state)
                        StatusChip(label, prepStateTone(p.state))
                    }
                }
            }
        }
    }
}

/**
 * Tap opens a sheet with the full plan and buttons:
 * "그만 챙기기" (POST stop, confirm first) and "다시 계획하기" (POST replan).
 */
@Composable
fun FollowPlanSheet(
    item: FollowItem,
    onDismiss: () -> Unit,
    onStop: (String) -> Unit,
    onReplan: (String) -> Unit,
) {
    val c = Kelo.colors
    var confirmStop by remember { mutableStateOf(false) }

    KeloModal(onDismiss = onDismiss) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 24.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // Header
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Top,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        item.title,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = c.ink,
                    )
                    if (item.startsAt > 0) {
                        Text(
                            dayClockText(item.startsAt),
                            style = MaterialTheme.typography.bodySmall,
                            color = c.muted,
                            modifier = Modifier.padding(top = 2.dp),
                        )
                    }
                }
                IconButton(onClick = onDismiss, modifier = Modifier.size(32.dp)) {
                    Icon(KeloIcons.Close, contentDescription = null, tint = c.muted)
                }
            }

            if (item.state == "stopped") {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatusChip(string(Res.string.follow_state_stopped), c.muted)
                }
            }

            // Checks section (Things to watch)
            if (item.checks.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        string(Res.string.follow_plan_checks),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        color = c.ink,
                    )
                    item.checks.forEach { ch ->
                        val fieldsText = if (ch.fields.isNotEmpty()) ch.fields.joinToString(", ") else ch.kind
                        val checkDesc = if (ch.from.isNotBlank() && ch.until.isNotBlank()) {
                            "$fieldsText (${ch.from} ~ ${ch.until})"
                        } else fieldsText
                        Text("• $checkDesc", style = MaterialTheme.typography.bodySmall, color = c.ink)
                        if (ch.nextAt != null && ch.nextAt > 0) {
                            Text(
                                "  " + string(Res.string.follow_next_check, clockText(ch.nextAt)),
                                style = MaterialTheme.typography.labelSmall,
                                color = c.muted,
                            )
                        }
                    }
                }
            }

            // Prep section (Preparations)
            if (item.prep.isNotEmpty()) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        string(Res.string.follow_plan_prep),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        color = c.ink,
                    )
                    item.prep.forEach { p ->
                        Row(
                            Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            val whenSuffix = if (p.`when`.isNotBlank()) " (${p.`when`})" else ""
                            Text(
                                "• ${p.what}$whenSuffix",
                                style = MaterialTheme.typography.bodySmall,
                                color = c.ink,
                                modifier = Modifier.weight(1f),
                            )
                            StatusChip(prepStateText(p.state), prepStateTone(p.state))
                        }
                    }
                }
            }

            // Tell section (Notifications)
            val tell = item.plan?.tell
            if (tell != null && (tell.lead.isNotEmpty() || tell.onChange)) {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(
                        string(Res.string.follow_plan_tell),
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.Bold,
                        color = c.ink,
                    )
                    if (tell.lead.isNotEmpty()) {
                        tell.lead.forEach { leadOffset ->
                            Text("• $leadOffset", style = MaterialTheme.typography.bodySmall, color = c.ink)
                        }
                    }
                    if (tell.onChange) {
                        Text(
                            "• " + string(Res.string.follow_plan_tell_change),
                            style = MaterialTheme.typography.bodySmall,
                            color = c.ink,
                        )
                    }
                }
            }

            // Action buttons: "그만 챙기기" (confirm first) and "다시 계획하기"
            Row(
                Modifier.fillMaxWidth().padding(top = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (item.state != "stopped") {
                    OutlinedButton(
                        onClick = { confirmStop = true },
                        modifier = Modifier.weight(1f),
                        shape = RoundedCornerShape(8.dp),
                        contentPadding = PaddingValues(vertical = 10.dp),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = c.danger),
                    ) {
                        Text(string(Res.string.follow_stop), style = MaterialTheme.typography.labelLarge)
                    }
                }

                PrimaryButton(
                    text = string(Res.string.follow_replan),
                    onClick = {
                        onReplan(item.key)
                        onDismiss()
                    },
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }

    if (confirmStop) {
        ConfirmDialog(
            title = string(Res.string.follow_stop_confirm),
            text = string(Res.string.follow_stop_confirm_detail),
            yes = string(Res.string.follow_stop_yes),
            onYes = {
                confirmStop = false
                onStop(item.key)
                onDismiss()
            },
            onDismiss = { confirmStop = false },
            danger = true,
        )
    }
}
