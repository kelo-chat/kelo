package chat.kelo.desktop.pages.goals

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Goal
import chat.kelo.app.goals.GoalActivity
import chat.kelo.app.goals.GoalDetailRules
import chat.kelo.app.goals.GoalFact
import chat.kelo.app.goals.GoalMeta
import chat.kelo.app.goals.GoalMetaCodec
import chat.kelo.app.goals.GoalMetric
import chat.kelo.app.goals.GoalStep
import chat.kelo.app.goals.GoalWorkspace
import chat.kelo.app.goals.GoalsRules
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/** A day in the app's language ("2026. 11. 8."). */
@Composable
fun goalDayText(date: LocalDate): String = DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(LocalStrings.current.locale).format(date)

/**
 * One goal's own page (android/kelo .../goals/GoalDetailPage.kt): what the user told Kelo for it (a question they chose not to answer
 * shows as 말하지 않음), the plan (the user's own steps can be ticked; Kelo's show where they stand), numbers with a small chart, the
 * automations made for it (a click opens that schedule on 작업), the documents, and what happened so far. "Kelo에게 이어서
 * 맡기기" has Kelo pick it up again in the chat (POST /api/goals/{id}/work).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun GoalDetail(app: AppContainer, goal: Goal, onBack: () -> Unit, onEdit: (Goal) -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var w by remember(goal.id) { mutableStateOf<GoalWorkspace?>(null) }
    var failed by remember(goal.id) { mutableStateOf(false) }
    var ticking by remember(goal.id) { mutableStateOf(mapOf<Long, String>()) }
    var resumed by remember(goal.id) { mutableStateOf<Int?>(null) }
    var resuming by remember(goal.id) { mutableStateOf(false) }
    suspend fun load() {
        try {
            w = app.sessions.requireApi().get<GoalWorkspace>("/api/goals/${goal.id}"); failed = false; ticking = emptyMap()
        } catch (e: ApiError) { failed = true }
    }
    LaunchedEffect(goal.id) { load() }
    fun tick(s: GoalStep) {
        if (!GoalDetailRules.canTick(s)) return
        val next = GoalDetailRules.toggled(s)
        ticking = ticking + (s.id to next)
        scope.launch {
            try {
                app.sessions.requireApi().patch<JsonElement>("/api/goals/${goal.id}/steps/${s.id}", buildJsonObject { put("status", next) })
            } catch (e: ApiError) { failed = true }
            load()
        }
    }
    val ws = w
    val g = goal.copy(plan = ws?.goal?.plan ?: goal.plan, asking = ws?.goal?.asking ?: goal.asking, working = ws?.goal?.working ?: goal.working)
    val refreshMs = GoalsRules.refreshMs(listOf(g))
    LaunchedEffect(goal.id, refreshMs) {
        val ms = refreshMs ?: return@LaunchedEffect
        while (true) { kotlinx.coroutines.delay(ms); load() }
    }
    val meta = remember(g.note) { GoalMetaCodec.parse(g.note) }
    Box(modifier.fillMaxHeight(), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = 720.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(KeloIcons.ArrowLeft, contentDescription = stringResource(R.string.back), tint = c.ink) }
                Text(g.title, style = MaterialTheme.typography.titleLarge, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                TextButton(onClick = { onEdit(g) }) { Text(stringResource(R.string.goal_edit), color = c.accent) }
            }
            if (meta.note.isNotBlank()) Text(meta.note, style = MaterialTheme.typography.bodyMedium, color = c.muted)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                if (g.plan.total > 0) Chip(stringResource(R.string.goals_plan_count, g.plan.done, g.plan.total), c.success)
                meta.due?.let { Chip(stringResource(R.string.goals_due_by, goalDayText(it)), c.accent) }
                when (GoalsRules.activity(g)) {
                    GoalActivity.WORKING -> Chip(stringResource(R.string.goals_working), c.success)
                    GoalActivity.ASKING -> Chip(stringResource(R.string.goals_asking), c.accent)
                    null -> {}
                }
            }
            PrimaryButton(stringResource(R.string.goal_resume), enabled = !resuming && !g.isDone, modifier = Modifier.fillMaxWidth(), onClick = {
                resuming = true
                scope.launch {
                    try {
                        app.sessions.requireApi().post<JsonElement>("/api/goals/${goal.id}/work")
                        resumed = R.string.goal_resume_started
                        app.openRequests.value = OpenTarget.Chat
                    } catch (e: ApiError) {
                        resumed = if (e.status == 409) R.string.goal_resume_no_model else R.string.connect_failed
                    } finally { resuming = false }
                }
            })
            resumed?.let { MutedText(stringResource(it), small = true) }
            if (failed) ErrorText(stringResource(R.string.connect_failed), small = true)
            if (ws == null) { if (!failed) MutedText(stringResource(R.string.loading), small = true); return@Column }

            Heading(stringResource(R.string.goal_facts))
            if (ws.facts.isEmpty()) MutedText(stringResource(R.string.goal_facts_empty), small = true)
            ws.facts.forEach { FactRow(it) }

            Heading(stringResource(R.string.goal_plan))
            if (ws.steps.isEmpty()) MutedText(stringResource(R.string.goal_plan_empty), small = true)
            ws.steps.forEach { s ->
                val shown = ticking[s.id]?.let { s.copy(status = it) } ?: s
                StepRow(shown, onTick = { tick(s) })
            }
            if (ws.metrics.isNotEmpty()) {
                Heading(stringResource(R.string.goal_numbers))
                ws.metrics.forEach { MetricCard(it) }
            }
            if (ws.schedules.isNotEmpty()) {
                Heading(stringResource(R.string.goal_schedules))
                ws.schedules.forEach { s ->
                    // a click opens that schedule on 작업 (where it can be changed, paused or run)
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable(role = Role.Button) { app.openRequests.value = OpenTarget.Schedule(s.id) }
                            .padding(vertical = 6.dp, horizontal = 4.dp),
                        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Icon(KeloIcons.Calendar, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                        Column(Modifier.weight(1f)) {
                            Text(s.name, style = MaterialTheme.typography.bodyMedium, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            val line = listOfNotNull(s.description.ifBlank { null }, if (!s.enabled) stringResource(R.string.goal_schedule_off) else null).joinToString(" · ")
                            if (line.isNotEmpty()) Text(line, style = MaterialTheme.typography.bodySmall, color = c.muted)
                        }
                        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                    }
                }
            }
            if (ws.documents.isNotEmpty()) {
                Heading(stringResource(R.string.goal_documents))
                ws.documents.forEach { d ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Icon(KeloIcons.File, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                        Text(d.title.ifBlank { d.path.substringAfterLast('/') }, style = MaterialTheme.typography.bodyMedium, color = if (d.exists) c.ink else c.muted,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            if (ws.log.isNotEmpty()) {
                Heading(stringResource(R.string.goal_log))
                ws.log.take(20).forEach { e ->
                    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text(goalDayText(Instant.ofEpochMilli((e.at * 1000).toLong()).atZone(ZoneId.systemDefault()).toLocalDate()),
                            style = MaterialTheme.typography.labelSmall, color = c.muted, maxLines = 1, modifier = Modifier.padding(top = 2.dp))
                        Text(e.text, style = MaterialTheme.typography.bodySmall, color = c.ink, modifier = Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun Heading(text: String) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = Kelo.colors.accent, modifier = Modifier.padding(top = 12.dp).semantics { heading() })
}

@Composable
private fun Chip(text: String, tone: Color) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = tone, maxLines = 2,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(tone.copy(alpha = 0.12f)).padding(horizontal = 8.dp, vertical = 3.dp))
}

@Composable
private fun FactRow(f: GoalFact) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(f.key, style = MaterialTheme.typography.bodyMedium, color = c.muted, modifier = Modifier.weight(0.42f))
        if (f.skipped) Text(stringResource(R.string.goal_fact_skipped), style = MaterialTheme.typography.bodyMedium, color = c.muted, fontStyle = FontStyle.Italic, modifier = Modifier.weight(0.58f))
        else Text(f.value, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(0.58f))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StepRow(s: GoalStep, onTick: () -> Unit) {
    val c = Kelo.colors
    val label = stringResource(R.string.goal_mark_step)
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).then(if (s.mine) Modifier.clickable(role = Role.Checkbox, onClick = onTick) else Modifier).padding(vertical = 2.dp),
        verticalAlignment = Alignment.Top,
    ) {
        if (s.mine) {
            Checkbox(checked = s.isDone, onCheckedChange = { onTick() }, modifier = Modifier.semantics { contentDescription = label },
                colors = CheckboxDefaults.colors(checkedColor = c.success, uncheckedColor = c.muted, checkmarkColor = c.onSuccess))
        } else {
            Box(Modifier.size(48.dp), contentAlignment = Alignment.Center) {
                Icon(if (s.isDone) KeloIcons.Check else KeloIcons.Spark, contentDescription = null, tint = if (s.isDone) c.success else c.accent, modifier = Modifier.size(20.dp))
            }
        }
        Column(Modifier.weight(1f).padding(top = 12.dp, end = 4.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(s.title, style = MaterialTheme.typography.bodyLarge, color = if (s.isDone) c.muted else c.ink, textDecoration = if (s.isDone) TextDecoration.LineThrough else null)
            if (s.detail.isNotBlank()) Text(s.detail, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 3, overflow = TextOverflow.Ellipsis)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Chip(stringResource(if (s.mine) R.string.goal_owner_me else R.string.goal_owner_kelo), if (s.mine) c.muted else c.accent)
                if (!s.mine) Chip(stringResource(when (s.status) {
                    "done" -> R.string.goal_status_done
                    "doing" -> R.string.goal_status_doing
                    else -> R.string.goal_status_todo
                }), if (s.isDone) c.success else c.muted)
                s.due?.let { d -> runCatching { LocalDate.parse(d) }.getOrNull()?.let { Chip(stringResource(R.string.goal_due, goalDayText(it)), c.muted) } }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun MetricCard(m: GoalMetric) {
    val c = Kelo.colors
    val last = m.values.lastOrNull()?.value
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(m.name, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold, color = c.ink, modifier = Modifier.weight(1f))
            Text(if (last != null) stringResource(R.string.goal_number_latest, GoalDetailRules.withUnit(last, m.unit)) else stringResource(R.string.goal_number_none),
                style = MaterialTheme.typography.bodySmall, color = if (last != null) c.ink else c.muted)
        }
        val points = remember(m) { GoalDetailRules.chart(m) }
        val target = remember(m) { GoalDetailRules.targetLevel(m) }
        if (points.size >= 2) {
            val line = c.accent
            val goalLine = c.success
            val desc = m.values.joinToString(", ") { GoalDetailRules.number(it.value) }
            Canvas(Modifier.fillMaxWidth().height(64.dp).semantics { contentDescription = desc }) {
                val pad = 6.dp.toPx()
                fun at(p: Pair<Float, Float>) = Offset(pad + p.first * (size.width - 2 * pad), size.height - pad - p.second * (size.height - 2 * pad))
                target?.let { t ->
                    val y = size.height - pad - t * (size.height - 2 * pad)
                    drawLine(goalLine, Offset(pad, y), Offset(size.width - pad, y), strokeWidth = 1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 8f)))
                }
                val path = Path().apply { points.forEachIndexed { i, p -> val o = at(p); if (i == 0) moveTo(o.x, o.y) else lineTo(o.x, o.y) } }
                drawPath(path, line, style = Stroke(width = 2.dp.toPx()))
                drawCircle(line, radius = 3.5.dp.toPx(), center = at(points.last()))
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            m.start?.let { Text(stringResource(R.string.goal_number_start, GoalDetailRules.withUnit(it, m.unit)), style = MaterialTheme.typography.labelSmall, color = c.muted) }
            m.target?.let { Text(stringResource(R.string.goal_number_target, GoalDetailRules.withUnit(it, m.unit)), style = MaterialTheme.typography.labelSmall, color = c.success) }
        }
    }
}

/** Adding or editing a goal: the title, a note, a tag of one's own, an optional due date and the progress. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun GoalSheet(goal: Goal?, onClose: () -> Unit, onSave: (title: String, note: String, tag: String, done: () -> Unit) -> Unit) {
    val c = Kelo.colors
    val meta = remember(goal?.id) { GoalMetaCodec.parse(goal?.note.orEmpty()) }
    var title by remember(goal?.id) { mutableStateOf(goal?.title.orEmpty()) }
    var note by remember(goal?.id) { mutableStateOf(meta.note) }
    var tag by remember(goal?.id) { mutableStateOf(goal?.category.orEmpty()) }
    var dueDay by remember(goal?.id) { mutableStateOf(meta.due?.toEpochDay()) }
    var progress by remember(goal?.id) { mutableStateOf(meta.progress) }
    var picking by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    LaunchedEffect(saving) { if (saving) { delay(8_000); saving = false } }
    AlertDialog(
        onDismissRequest = onClose, containerColor = c.card, modifier = Modifier.widthIn(max = 560.dp),
        title = { Text(stringResource(if (goal == null) R.string.goals_add else R.string.goals_edit), color = c.ink) },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                KeloTextField(title, { title = it }, stringResource(R.string.goals_title_placeholder), maxLength = 200)
                KeloTextField(note, { note = it }, stringResource(R.string.goals_note_placeholder), singleLine = false, maxLength = 1800)
                KeloTextField(tag, { tag = it }, stringResource(R.string.goals_tag_placeholder), maxLength = 40)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(KeloIcons.Calendar, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp))
                    val due = dueDay?.let { LocalDate.ofEpochDay(it) }
                    Text(due?.let { stringResource(R.string.goals_due_by, goalDayText(it)) } ?: stringResource(R.string.goals_due_none),
                        style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f).padding(start = 10.dp))
                    if (due != null) TextButton(onClick = { dueDay = null }) { Text(stringResource(R.string.goals_due_clear), color = c.muted) }
                    TextButton(onClick = { picking = true }) { Text(stringResource(R.string.goals_due_pick), color = c.accent) }
                }
                Column {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(stringResource(R.string.goals_progress), style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
                        Text(progress?.let { stringResource(R.string.goals_progress_value, it) } ?: stringResource(R.string.goals_progress_none),
                            style = MaterialTheme.typography.bodySmall, color = c.muted)
                        if (progress != null) TextButton(onClick = { progress = null }) { Text(stringResource(R.string.goals_due_clear), color = c.muted) }
                    }
                    Slider(value = (progress ?: 0).toFloat(), onValueChange = { progress = (Math.round(it / 10f) * 10).coerceIn(0, 100) }, valueRange = 0f..100f, steps = 9,
                        colors = SliderDefaults.colors(thumbColor = c.accent, activeTrackColor = c.accent, inactiveTrackColor = c.line))
                }
            }
        },
        confirmButton = {
            PrimaryButton(stringResource(if (goal == null) R.string.goals_add_button else R.string.save), enabled = title.isNotBlank() && !saving, onClick = {
                saving = true
                val encoded = GoalMetaCodec.encode(GoalMeta(note, dueDay?.let { LocalDate.ofEpochDay(it) }, progress))
                onSave(title.trim(), encoded, GoalsRules.tag(tag)) { saving = false; onClose() }
            })
        },
        dismissButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
    if (picking) {
        val d = rememberDatePickerState(
            initialSelectedDateMillis = (dueDay?.let { LocalDate.ofEpochDay(it) } ?: LocalDate.now().plusWeeks(1)).atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
        )
        DatePickerDialog(
            onDismissRequest = { picking = false },
            confirmButton = {
                TextButton(onClick = {
                    d.selectedDateMillis?.let { ms -> dueDay = Instant.ofEpochMilli(ms).atZone(ZoneOffset.UTC).toLocalDate().toEpochDay() }
                    picking = false
                }) { Text(stringResource(R.string.ok), color = c.accent) }
            },
            dismissButton = { TextButton(onClick = { picking = false }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        ) { DatePicker(d) }
    }
}
