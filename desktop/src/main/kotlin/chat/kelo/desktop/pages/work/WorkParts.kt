// Ported from android/kelo .../work/WorkParts.kt (keep in step with it).
package chat.kelo.desktop.pages.work

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.*
import chat.kelo.app.work.*
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.Badge
import chat.kelo.desktop.ui.Tag
import androidx.compose.runtime.collectAsState
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/* The timeline's pieces: a rail down the left with a marker per entry (and the time beside it where there is one), section titles,
 * and the cards for running work, questions, the agenda and finished work. */

/** The rail's width: the time column and the line with its markers. */
internal val RAIL_WIDTH: Dp = 68.dp
private val TIME_WIDTH: Dp = 48.dp
private val MARK_Y: Dp = 20.dp

/** What sits on the rail beside an entry. */
internal enum class RailMarker { LIVE, ASK, PLANNED, PAUSED, DONE, FAILED, NONE }

/** One entry on the timeline: the time (or nothing) on the left, the rail with its marker, and the entry. */
@Composable
internal fun RailItem(marker: RailMarker, label: String?, content: @Composable () -> Unit) {
    val c = Kelo.colors
    val warn = askColor()
    // the rail is drawn behind the whole row, so it runs the entry's full height without measuring anything twice
    Row(
        Modifier.fillMaxWidth().drawBehind {
            val x = TIME_WIDTH.toPx() + (RAIL_WIDTH - TIME_WIDTH).toPx() / 2
            drawLine(c.line, Offset(x, 0f), Offset(x, size.height), strokeWidth = 2.dp.toPx())
            val y = MARK_Y.toPx()
            val r = 6.dp.toPx()
            when (marker) {
                RailMarker.LIVE -> { drawCircle(c.accent.copy(alpha = 0.25f), r * 1.8f, Offset(x, y)); drawCircle(c.accent, r, Offset(x, y)) }
                RailMarker.ASK -> drawCircle(warn, r, Offset(x, y))
                RailMarker.PLANNED -> { drawCircle(c.bg, r, Offset(x, y)); drawCircle(c.accent, r, Offset(x, y), style = Stroke(2.dp.toPx())) }
                RailMarker.PAUSED -> { drawCircle(c.bg, r, Offset(x, y)); drawCircle(c.muted, r, Offset(x, y), style = Stroke(2.dp.toPx())) }
                RailMarker.DONE -> drawCircle(c.success, r * 0.8f, Offset(x, y))
                RailMarker.FAILED -> drawCircle(c.danger, r * 0.8f, Offset(x, y))
                RailMarker.NONE -> {}
            }
        },
    ) {
        Box(Modifier.width(TIME_WIDTH).padding(top = MARK_Y - 8.dp, end = 6.dp), contentAlignment = Alignment.TopEnd) {
            if (label != null) Text(label, style = MaterialTheme.typography.labelSmall, color = c.muted, maxLines = 2, textAlign = TextAlign.End)
        }
        Box(Modifier.width(RAIL_WIDTH - TIME_WIDTH))
        Box(Modifier.weight(1f).padding(bottom = 12.dp)) { content() }
    }
}

/** A line on the rail without a marker (a placeholder, an empty section). */
@Composable
internal fun RailNote(content: @Composable () -> Unit) = RailItem(RailMarker.NONE, label = null) { Box(Modifier.padding(top = 6.dp)) { content() } }

/** A section's title: its name, how many things are in it, and an optional action. */
@Composable
internal fun SectionTitle(text: String, count: Int?, attention: Boolean = false, trailing: @Composable () -> Unit = {}) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().padding(start = 4.dp, top = 18.dp, bottom = 8.dp).semantics { heading() },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = c.ink)
        if (count != null && count > 0) {
            Text(
                count.toString(), style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.Bold,
                color = if (attention) c.onWarn else c.ink,
                modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(if (attention) askColor() else c.bubble).padding(horizontal = 7.dp, vertical = 1.dp),
            )
        }
        Box(Modifier.weight(1f))
        trailing()
    }
}

/** A day inside a section ("오늘 · 11월 7일 (토)"), set in line with the entries. */
@Composable
internal fun DayCaption(text: String) {
    Text(
        text, style = MaterialTheme.typography.labelLarge, color = Kelo.colors.accent,
        modifier = Modifier.padding(start = RAIL_WIDTH, top = 4.dp, bottom = 6.dp),
    )
}

@Composable
internal fun EmptyLine(text: String) = Text(text, style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)

/** The amber of things that wait for the user. */
@Composable
internal fun askColor(): Color = Kelo.colors.warn

/** A small coloured word for a state. */
@Composable
internal fun StatusChip(text: String, tone: Color) {
    Text(
        text, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold, color = tone, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(tone.copy(alpha = 0.12f)).padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/** An entry's box: a hairline outline on the page colour. */
@Composable
internal fun EntryBox(onClick: (() -> Unit)? = null, content: @Composable () -> Unit) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(12.dp)
    Column(
        Modifier.fillMaxWidth().clip(shape).background(c.card).border(1.dp, c.line, shape)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            // the card's actions end it, and their touch targets already leave room below
            .padding(start = 14.dp, end = 14.dp, top = 12.dp, bottom = 2.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) { content() }
}

@Composable
internal fun taskTone(status: String): Color = when (status) {
    AgentTask.STATUS_RUNNING -> Kelo.colors.accent
    AgentTask.STATUS_WAITING -> askColor()
    AgentTask.STATUS_FAILED -> Kelo.colors.danger
    AgentTask.STATUS_STOPPED, AgentTask.STATUS_INTERRUPTED, AgentTask.STATUS_WAITING_MODEL -> Kelo.colors.muted
    else -> Kelo.colors.success
}

@Composable
internal fun jobWord(status: String): String = stringResource(
    when (status) {
        Worker.RUNNING -> R.string.agent_status_running
        Worker.NEEDS_INPUT -> R.string.work_job_needs_input
        Worker.BLOCKED -> R.string.work_job_blocked
        Worker.FAILED -> R.string.agent_status_failed
        Worker.STOPPED -> R.string.agent_status_stopped
        Worker.INTERRUPTED -> R.string.work_job_interrupted
        else -> R.string.agent_status_done
    },
)

@Composable
internal fun jobTone(status: String): Color = when (status) {
    Worker.RUNNING -> Kelo.colors.accent
    Worker.NEEDS_INPUT, Worker.BLOCKED, Worker.INTERRUPTED -> askColor()
    Worker.FAILED -> Kelo.colors.danger
    Worker.STOPPED -> Kelo.colors.muted
    else -> Kelo.colors.success
}

@Composable
internal fun kindWord(kind: String): String = stringResource(
    when (kind) {
        "browser" -> R.string.work_kind_browser
        "research" -> R.string.work_kind_research
        else -> R.string.work_kind_general
    },
)

/** A small text action inside a card. */
@Composable
internal fun CardAction(text: String, icon: androidx.compose.ui.graphics.vector.ImageVector? = null, danger: Boolean = false, onClick: () -> Unit) {
    val c = Kelo.colors
    TextButton(onClick = onClick, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 8.dp, vertical = 0.dp)) {
        if (icon != null) Icon(icon, contentDescription = null, tint = if (danger) c.danger else c.accent, modifier = Modifier.size(16.dp).padding(end = 2.dp))
        Text(text, style = MaterialTheme.typography.labelLarge.copy(fontSize = MaterialTheme.typography.bodySmall.fontSize), color = if (danger) c.danger else c.accent)
    }
}

// ---- 지금 하는 일

/** A running task: what it is doing now, the jobs it handed off (nested), and its live browser and Stop. */
@Composable
internal fun NowTaskCard(
    vm: WorkModel, node: TaskNode, tabs: List<BrowserTab>, workers: List<Worker>,
    onOpen: () -> Unit, onOpenJob: (String) -> Unit, onOpenTask: (Long) -> Unit, onOpenLive: (String) -> Unit,
) {
    val c = Kelo.colors
    val t = node.task
    var confirmStop by remember { mutableStateOf(false) }
    EntryBox(onClick = onOpen) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(t.title.ifBlank { stringResource(R.string.agent_task_untitled) }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold,
                color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            StatusChip(statusWord(t.status), taskTone(t.status))
        }
        if (t.running && (t.liveStatus != null || t.status == AgentTask.STATUS_RUNNING)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(Modifier.size(12.dp), color = c.accent, strokeWidth = 1.5.dp)
                Text(t.liveStatus ?: statusWord(t.status), style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        sourceWord(t.source)?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = c.muted) }
        node.jobs.forEach { j -> NestedJob(vm, j, onOpen = { onOpenJob(j.worker.id) }, onOpenLive = onOpenLive) }
        node.children.forEach { ch -> NestedTask(ch, onOpen = { onOpenTask(ch.id) }) }
        if (t.running) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Timeline.tabFor(t, tabs, workers)?.let { tab -> CardAction(stringResource(R.string.work_live_view), KeloIcons.Eye) { onOpenLive(tab) } }
                CardAction(stringResource(R.string.agent_task_stop), KeloIcons.Stop, danger = true) { confirmStop = true }
            }
        }
    }
    if (confirmStop) {
        ConfirmDialog(stringResource(R.string.agent_task_stop_confirm), stringResource(R.string.agent_task_stop), onDismiss = { confirmStop = false }, onYes = {
            vm.stopTask(t.id) {}
        })
    }
}

/** A job inside its parent's card: a thin line on its left, its state, its last step and how far through its budget it is. */
@Composable
private fun NestedJob(vm: WorkModel, j: JobNode, onOpen: () -> Unit, onOpenLive: (String) -> Unit) {
    val c = Kelo.colors
    val line = jobTone(j.worker.status).copy(alpha = 0.5f)
    Column(
        Modifier.fillMaxWidth().padding(top = 4.dp)
            .drawBehind { drawRect(line, size = androidx.compose.ui.geometry.Size(2.dp.toPx(), size.height)) }
            .clickable(role = Role.Button, onClick = onOpen).padding(start = 12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        JobBody(vm, j.worker, onOpenLive)
    }
}

@Composable
private fun NestedTask(t: AgentTask, onOpen: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onOpen).padding(start = 12.dp, top = 2.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(Modifier.size(6.dp).background(taskTone(t.status), CircleShape))
        Text(t.title.ifBlank { stringResource(R.string.agent_task_untitled) }, style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 1,
            overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        Text(statusWord(t.status), style = MaterialTheme.typography.labelSmall, color = taskTone(t.status))
    }
}

/** A job's title, kind and state, its last step, its step budget, and its actions. */
@Composable
private fun JobBody(vm: WorkModel, w: Worker, onOpenLive: (String) -> Unit) {
    val c = Kelo.colors
    var confirmStop by remember { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Column(Modifier.weight(1f)) {
            Text(w.title.ifBlank { stringResource(R.string.agent_task_untitled) }, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium,
                color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Text(stringResource(R.string.work_job_line, kindWord(w.kind)), style = MaterialTheme.typography.labelSmall, color = c.muted)
        }
        StatusChip(jobWord(w.status), jobTone(w.status))
    }
    w.statusText?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis) }
    Timeline.progress(w)?.let { p ->
        LinearProgressIndicator(progress = { p }, modifier = Modifier.fillMaxWidth().height(4.dp).clip(RoundedCornerShape(2.dp)),
            color = jobTone(w.status), trackColor = c.line, drawStopIndicator = {})
        Text(stringResource(R.string.work_job_steps, w.steps, w.maxSteps), style = MaterialTheme.typography.labelSmall, color = c.muted)
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Timeline.tabFor(w)?.let { tab -> CardAction(stringResource(R.string.work_live_view), KeloIcons.Eye) { onOpenLive(tab) } }
        if (w.running) CardAction(stringResource(R.string.agent_task_stop), KeloIcons.Stop, danger = true) { confirmStop = true }
    }
    if (confirmStop) {
        ConfirmDialog(stringResource(R.string.work_job_stop_confirm), stringResource(R.string.agent_task_stop), onDismiss = { confirmStop = false }, onYes = {
            vm.stopWorker(w.id)
        })
    }
}

/** A job of its own (its parent task is not in the list) running now. */
@Composable
internal fun JobCard(vm: WorkModel, j: JobNode, onOpen: () -> Unit, onOpenLive: (String) -> Unit) {
    EntryBox(onClick = onOpen) { JobBody(vm, j.worker, onOpenLive) }
}

/** What the user allowed for a running task as a whole: the site, the step, how many are used; one tap stops it. */
@Composable
internal fun TaskGrantCard(g: chat.kelo.app.agent.TaskPermission, onRevoke: () -> Unit) {
    val c = Kelo.colors
    EntryBox {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(KeloIcons.Shield, contentDescription = null, tint = c.accent, modifier = Modifier.size(18.dp))
            Text(stringResource(R.string.work_grant_title), style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(1f))
        }
        Text(stringResource(R.string.work_grant_line, g.site, g.action, g.used, g.maxCount), style = MaterialTheme.typography.bodyMedium, color = c.ink)
        Row { CardAction(stringResource(R.string.work_grant_revoke), KeloIcons.Close, danger = true, onClick = onRevoke) }
    }
}

// ---- 답이 필요해요

/** An approval waiting: the same card as in the chat; a payment that needs this phone's card says so on top. */
@Composable
internal fun ApprovalAsk(vm: WorkModel, a: Ask.Approval, working: Boolean) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (a.deliver) StatusChip(stringResource(R.string.work_ask_card), askColor())
        else if (a.approval.unattended) StatusChip(stringResource(R.string.work_ask_unattended), askColor())
        PendingCard(vm, a.approval, working)
    }
}

/** A job stopped for the user: why, and how to let it go on (an answer, the tab handed back, or just "go on"). */
@Composable
internal fun JobAsk(vm: WorkModel, w: Worker, onOpen: () -> Unit, onOpenLive: (String) -> Unit) {
    val c = Kelo.colors
    var answering by remember { mutableStateOf(false) }
    EntryBox(onClick = onOpen) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(w.title.ifBlank { stringResource(R.string.agent_task_untitled) }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold,
                color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            StatusChip(jobWord(w.status), jobTone(w.status))
        }
        Text(waitingText(w), style = MaterialTheme.typography.bodyMedium, color = c.ink)
        w.reason?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 4, overflow = TextOverflow.Ellipsis) }
        Row(verticalAlignment = Alignment.CenterVertically) {
            CardAction(stringResource(R.string.work_job_resume), KeloIcons.Retry) { vm.resumeWorker(w.id) }
            CardAction(stringResource(R.string.work_job_answer), KeloIcons.Chat) { answering = true }
            if (w.waitingFor == "control") Timeline.tabFor(w)?.let { tab -> CardAction(stringResource(R.string.work_live_view), KeloIcons.Eye) { onOpenLive(tab) } }
        }
    }
    if (answering) MessageSheet(onSend = { text -> vm.messageWorker(w.id, text) }, onClose = { answering = false })
}

/**
 * A follow-up on hold: what it was about, since when, and 다시 시작 (the agent asks again) with the follow-up's other buttons.
 * 로그인하기 opens the sign-in sheet here.
 */
@Composable
internal fun HoldAsk(vm: WorkModel, f: chat.kelo.app.followup.Followup, busy: Boolean) {
    val c = Kelo.colors
    var login by remember(f.id) { mutableStateOf<String?>(null) }
    EntryBox {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(f.title.ifBlank { stringResource(R.string.agent_task_untitled) }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold,
                color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
            if (f.onHold) StatusChip(stringResource(R.string.followup_on_hold), c.muted)
        }
        Text(stringResource(followupKindText(f.kind)), style = MaterialTheme.typography.bodyMedium, color = c.ink)
        if (f.onHold && f.createdAt > 0) Text(stringResource(R.string.followup_hold_since, relText(f.createdAt)), style = MaterialTheme.typography.bodySmall, color = c.muted)
        val buttons = remember(f) { chat.kelo.app.followup.FollowupRules.holdButtons(f) }
        FollowupButtons(buttons, busy = busy, modifier = Modifier.padding(top = 2.dp, bottom = 10.dp), onPress = { step ->
            if (step is chat.kelo.app.followup.FollowupStep.OpenLogin) login = step.name else vm.runFollowup(f, step)
        })
    }
    login?.let { name ->
        SignInSheet(vm, name, onClose = { login = null })
    }
}

/**
 * A secure entry waiting for its value, as one short row: what it is ("텔레그램 봇 토큰 입력을 기다려요") and when it was asked. A click
 * goes to its card in the chat (scrolled to it and marked for a moment); the card is where the value goes in. [onCancel]: 그만두기.
 */
@Composable
internal fun EntryAsk(a: Ask.Entry, onOpen: () -> Unit, onCancel: () -> Unit) {
    val c = Kelo.colors
    val r = a.request
    EntryBox(onClick = if (a.chatCard) onOpen else null) {
        Text(entryText(r), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, color = c.ink, maxLines = 3,
            overflow = TextOverflow.Ellipsis)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (r.createdAt > 0) Text(relText(r.createdAt), style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(1f))
            else Box(Modifier.weight(1f))
            androidx.compose.material3.TextButton(onClick = onCancel) {
                Text(stringResource(R.string.work_entry_cancel), style = MaterialTheme.typography.labelLarge, color = c.muted)
            }
        }
    }
}

/** What a secure entry waits for, in words. */
@Composable
internal fun entryText(r: chat.kelo.app.chat.vault.SecretRequest): String {
    val name = r.label.ifBlank { r.hosts.firstOrNull() ?: r.name }
    return when {
        r.isCard -> stringResource(R.string.work_entry_card)
        r.isPin -> stringResource(R.string.work_entry_pin, r.hosts.firstOrNull() ?: name)
        r.isLogin -> stringResource(R.string.work_entry_login, name)
        else -> stringResource(R.string.work_entry_key, name)
    }
}

/** What a follow-up waits for, in words. */
internal fun followupKindText(kind: String): Int = when (kind) {
    "login" -> R.string.followup_kind_login
    "worker" -> R.string.followup_kind_worker
    "approval" -> R.string.followup_kind_approval
    "approval_expired" -> R.string.followup_kind_approval_expired
    else -> R.string.followup_kind_reply
}

@Composable
internal fun waitingText(w: Worker): String = when {
    w.status == Worker.INTERRUPTED -> stringResource(R.string.work_wait_restart)
    w.waitingFor == "login" -> stringResource(R.string.work_wait_login)
    w.waitingFor == "control" -> stringResource(R.string.work_wait_control)
    w.status == Worker.BLOCKED -> stringResource(R.string.work_wait_blocked)
    else -> stringResource(R.string.work_wait_answer)
}

/** A few words to a job: read before its next step, or it goes on with them. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun MessageSheet(onSend: (String) -> Unit, onClose: () -> Unit) {
    val c = Kelo.colors
    var text by rememberSaveable { mutableStateOf("") }
    DialogSheet(onDismissRequest = onClose) {
        Column(Modifier.fillMaxWidth().imePadding().padding(start = 20.dp, end = 20.dp, bottom = 28.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(stringResource(R.string.work_job_answer), style = MaterialTheme.typography.titleMedium, color = c.ink)
            Text(stringResource(R.string.work_job_answer_hint), style = MaterialTheme.typography.bodySmall, color = c.muted)
            KeloTextField(text, { text = it }, stringResource(R.string.work_job_answer_placeholder), singleLine = false, maxLength = 4000)
            PrimaryButton(stringResource(R.string.work_job_answer_send), enabled = text.isNotBlank(), onClick = { onSend(text); onClose() })
        }
    }
}

// ---- 다가오는 일

/** One planned run: the schedule's name and how often it runs; a note when it is running now or failed last time. */
@Composable
internal fun AgendaRow(s: Schedule, onResume: (() -> Unit)? = null, onOpen: () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onOpen).padding(top = 10.dp, bottom = 4.dp, end = 4.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(s.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = if (s.enabled) c.ink else c.muted,
                maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
            if (s.running) StatusChip(stringResource(R.string.agent_status_running), c.accent)
            if (s.paused) StatusChip(stringResource(R.string.work_run_paused), askColor())
            if (s.createdBy == "agent") StatusChip(stringResource(R.string.work_schedule_by_agent), c.muted)
        }
        Text(scheduleText(s), style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
        s.lastRun?.takeIf { it.at != null && it.status == "failed" }?.let { lr ->
            Text(stringResource(R.string.agent_schedule_last, lastRunWord(lr.status), relText(lr.at ?: 0.0)), style = MaterialTheme.typography.labelSmall, color = c.danger)
        }
        s.disabledReason?.takeIf { it.isNotBlank() && !s.enabled && !s.paused }?.let {
            Text(stringResource(R.string.agent_schedule_off_reason, it), style = MaterialTheme.typography.labelSmall, color = c.danger, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
        if (s.paused) {
            Text(stringResource(R.string.work_schedule_paused_reason, s.pausedReason?.ifBlank { null } ?: stringResource(R.string.work_schedule_paused_unknown)),
                style = MaterialTheme.typography.labelSmall, color = askColor(), maxLines = 3, overflow = TextOverflow.Ellipsis)
            if (onResume != null) CardAction(stringResource(R.string.work_schedule_resume), KeloIcons.Retry, onClick = onResume)
        }
    }
}

@Composable
internal fun PausedToggle(count: Int, open: Boolean, onToggle: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().padding(start = RAIL_WIDTH - 8.dp).clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onToggle)
            .padding(horizontal = 8.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text(pluralStringResource(R.plurals.work_paused_count, count, count), style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(1f))
        Icon(if (open) KeloIcons.Minus else KeloIcons.Plus, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
    }
}

// ---- 지난 일

/** A finished task: its title, its one-line result, where it came from, and the jobs it handed off. */
@Composable
internal fun PastRow(node: TaskNode, onOpen: () -> Unit, onOpenJob: (String) -> Unit) {
    val c = Kelo.colors
    val t = node.task
    val unread = !t.seen
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onOpen).padding(top = 10.dp, bottom = 4.dp, end = 4.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(t.title.ifBlank { stringResource(R.string.agent_task_untitled) }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium,
                color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
            if (unread) Box(Modifier.size(8.dp).background(c.accent, CircleShape))
        }
        val sub = t.result.ifBlank { statusWord(t.status) }
        Text(sub, style = MaterialTheme.typography.bodySmall, color = if (t.failed) c.danger else c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            sourceWord(t.source)?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = c.muted) }
            node.jobs.forEach { j ->
                Text(
                    stringResource(R.string.work_past_job, jobWord(j.worker.status)), style = MaterialTheme.typography.labelSmall, color = jobTone(j.worker.status),
                    modifier = Modifier.clip(RoundedCornerShape(6.dp)).clickable(role = Role.Button) { onOpenJob(j.worker.id) }.padding(horizontal = 2.dp),
                )
            }
            if (node.children.isNotEmpty()) {
                Text(pluralStringResource(R.plurals.work_past_children, node.children.size, node.children.size), style = MaterialTheme.typography.labelSmall, color = c.muted)
            }
        }
    }
}
