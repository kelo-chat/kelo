package chat.kelo.desktop.pages

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.VerticalDivider
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Goal
import chat.kelo.app.goals.GoalActivity
import chat.kelo.app.goals.GoalMetaCodec
import chat.kelo.app.goals.GoalSuggestion
import chat.kelo.app.goals.GoalSuggestions
import chat.kelo.app.goals.GoalsRules
import chat.kelo.app.goals.GoalsState
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.goals.GoalDetail
import chat.kelo.desktop.pages.goals.GoalSheet
import chat.kelo.desktop.pages.goals.goalDayText
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PageTitle
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.LocalDate

/**
 * 다짐, as on the phone (android/kelo .../goals/GoalsScreen.kt): 해볼 만한 다짐 (goals Kelo found in the conversations: 추가 /
 * 아니에요) on top, then 진행 중 (the nearest due date first) and 끝남 (folded until opened); each goal shows its tag, due date,
 * progress and how far its plan is. "다짐 추가" opens the form (title, note, tag, due date, progress); the bin removes a goal with
 * an undo offered for a few seconds before it is deleted on the server. A click opens the goal's own page beside the list (or over
 * it in a narrow window).
 */
@Composable
fun GoalsPane(app: AppContainer, topInset: Dp) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var state by remember { mutableStateOf(GoalsState()) }
    var openId by remember { mutableStateOf<Long?>(null) }
    var showDone by remember { mutableStateOf(false) }
    var adding by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<Goal?>(null) }
    var askSchedules by remember { mutableStateOf<Goal?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val today = remember { LocalDate.now() }

    suspend fun load() {
        try {
            val api = app.sessions.requireApi()
            val goals = api.get<List<Goal>>("/api/goals")
            state = state.copy(goals = goals, loaded = true, failed = false, offline = false)
        } catch (e: ApiError) {
            state = if (state.loaded && e.isNetwork) state.copy(offline = true) else state.copy(failed = true, loaded = true)
        }
        runCatching { app.sessions.requireApi().get<GoalSuggestions>("/api/goals/suggestions") }.getOrNull()?.let { s -> state = state.copy(suggestions = s.items) }
    }
    LaunchedEffect(Unit) { load() }
    // "Kelo is working" / "waiting for you" follow the server while the pane is open (GoalsRules.refreshMs)
    val refreshMs = GoalsRules.refreshMs(state.goals)
    LaunchedEffect(refreshMs) {
        val ms = refreshMs ?: return@LaunchedEffect
        while (true) { kotlinx.coroutines.delay(ms); load() }
    }
    fun call(block: suspend () -> Unit) = scope.launch {
        try { block() } catch (e: ApiError) { state = state.copy(failed = true) }
        load()
    }
    fun toggle(g: Goal) = call {
        app.sessions.requireApi().patch<JsonElement>("/api/goals/${g.id}", buildJsonObject { put("status", if (g.isDone) Goal.STATUS_TRACKING else Goal.STATUS_DONE) })
    }
    // the bin, then (only for a goal with schedules on) "이 다짐에 묶인 예약 N개도 끌까요?" before the undo offer; the real delete
    // after the undo window says what to do with them (GoalsRules.deletePath), and the server stops the work running for the goal
    fun startRemove(g: Goal, pauseSchedules: Boolean) {
        state = state.copy(removing = state.removing + g.id,
            pauseSchedules = if (pauseSchedules) state.pauseSchedules + g.id else state.pauseSchedules - g.id)
        if (openId == g.id) openId = null
        scope.launch {
            snackbar.currentSnackbarData?.dismiss()
            val r = try {
                snackbar.showSnackbar(strings.get(R.string.goals_removed, g.title), actionLabel = strings.get(R.string.undo), duration = SnackbarDuration.Long)
            } catch (e: kotlinx.coroutines.CancellationException) {
                // left the page while the offer was up: the delete stands, with the schedule choice made for it
                val pause = g.id in state.pauseSchedules
                app.scope.launch { runCatching { app.sessions.requireApi().delete<JsonElement>(GoalsRules.deletePath(g.id, pause)) } }
                throw e
            }
            if (r == SnackbarResult.ActionPerformed) state = state.copy(removing = state.removing - g.id, pauseSchedules = state.pauseSchedules - g.id)
            else if (g.id in state.removing) {
                try {
                    app.sessions.requireApi().delete<JsonElement>(GoalsRules.deletePath(g.id, g.id in state.pauseSchedules))
                    state = state.copy(goals = state.goals.filter { it.id != g.id })
                } catch (_: ApiError) {
                } finally {
                    state = state.copy(removing = state.removing - g.id, pauseSchedules = state.pauseSchedules - g.id)   // a failed delete brings the goal back
                }
                load()
            }
        }
    }
    fun remove(g: Goal) {
        if (GoalsRules.asksAboutSchedules(g)) askSchedules = g else startRemove(g, false)
    }
    fun accept(s: GoalSuggestion) {
        state = state.copy(settling = state.settling + s.id)
        scope.launch {
            try {
                app.sessions.requireApi().post<JsonElement>("/api/goals/suggestions/${s.id}/accept")
                launch { snackbar.showSnackbar(strings.get(R.string.goals_suggest_added, s.title)) }
            } catch (e: ApiError) {
                state = state.copy(failed = true)
            } finally {
                state = state.copy(settling = state.settling - s.id)
            }
            load()
        }
    }
    fun dismiss(s: GoalSuggestion) {
        state = state.copy(settling = state.settling + s.id, suggestions = state.suggestions.filter { it.id != s.id })
        scope.launch {
            runCatching { app.sessions.requireApi().post<JsonElement>("/api/goals/suggestions/${s.id}/dismiss") }
            state = state.copy(settling = state.settling - s.id)
        }
    }

    Box(Modifier.fillMaxSize().padding(top = topInset)) {
        BoxWithConstraints(Modifier.fillMaxSize()) {
            val wide = maxWidth > 860.dp
            val opened = openId?.let { id -> state.goals.firstOrNull { it.id == id } }
            Row(Modifier.fillMaxSize()) {
                if (!wide && opened != null) {
                    GoalDetail(app, opened, onBack = { openId = null; scope.launch { load() } }, onEdit = { editing = it }, modifier = Modifier.weight(1f))
                    return@Row
                }
                Box(Modifier.weight(1f).fillMaxHeight(), contentAlignment = Alignment.TopCenter) {
                    Column(
                        Modifier.widthIn(max = 720.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            PageTitle(stringResource(R.string.tab_goals), Modifier.weight(1f))
                            PrimaryButton(stringResource(R.string.goals_add), onClick = { adding = true })
                        }
                        if (state.failed) ErrorText(stringResource(R.string.connect_failed), small = true)
                        if (state.offline) MutedText(stringResource(R.string.connect_failed), small = true)
                        val sugg = state.shownSuggestions
                        if (sugg.isNotEmpty()) {
                            GroupHeading(stringResource(R.string.goals_suggest_title), sugg.size)
                            sugg.forEach { s -> SuggestionCard(s, onAdd = { accept(s) }, onNo = { dismiss(s) }) }
                        }
                        GroupHeading(stringResource(R.string.goals_open), state.tracking.size)
                        if (!state.loaded) MutedText(stringResource(R.string.loading), small = true)
                        else if (state.tracking.isEmpty()) MutedText(stringResource(R.string.goals_empty), small = true, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp))
                        state.tracking.forEach { g -> GoalRow(g, today, selected = g.id == openId, onToggle = ::toggle, onOpen = { openId = it.id }, onDelete = ::remove) }
                        if (state.done.isNotEmpty()) {
                            GroupHeading(stringResource(R.string.goals_done), state.done.size) {
                                LinkButton(stringResource(if (showDone) R.string.goals_hide else R.string.goals_show), onClick = { showDone = !showDone })
                            }
                            if (showDone) state.done.forEach { g -> GoalRow(g, today, selected = g.id == openId, onToggle = ::toggle, onOpen = { openId = it.id }, onDelete = ::remove) }
                        }
                    }
                }
                if (wide && opened != null) {
                    VerticalDivider(color = c.line)
                    GoalDetail(app, opened, onBack = { openId = null; scope.launch { load() } }, onEdit = { editing = it }, modifier = Modifier.weight(1f))
                }
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(16.dp)) { data ->
            Snackbar(data, shape = RoundedCornerShape(8.dp), containerColor = c.ink, contentColor = c.bg, actionColor = c.accent)
        }
    }
    if (adding) GoalSheet(null, onClose = { adding = false }) { title, note, tag, done ->
        call {
            app.sessions.requireApi().post<JsonElement>("/api/goals", buildJsonObject { put("title", title); put("note", note); put("category", tag) })
            done()
        }
    }
    askSchedules?.let { g ->
        SchedulesQuestion(g.schedulesOn, onPause = { askSchedules = null; startRemove(g, true) },
            onKeep = { askSchedules = null; startRemove(g, false) }, onCancel = { askSchedules = null })
    }
    editing?.let { g ->
        GoalSheet(g, onClose = { editing = null }) { title, note, tag, done ->
            call {
                app.sessions.requireApi().patch<JsonElement>("/api/goals/${g.id}", buildJsonObject { put("title", title); put("note", note); put("category", tag) })
                done()
            }
        }
    }
}

@Composable
private fun GroupHeading(text: String, count: Int, trailing: @Composable () -> Unit = {}) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(start = 4.dp, top = 14.dp, bottom = 2.dp).semantics { heading() }, verticalAlignment = Alignment.CenterVertically) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = c.accent)
        Text(" · $count", style = MaterialTheme.typography.labelLarge, color = c.muted, modifier = Modifier.weight(1f))
        trailing()
    }
}

/** One goal: its box, the title, the next step, the note, and its tag, plan, asking state, due date and progress. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun GoalRow(g: Goal, today: LocalDate, selected: Boolean, onToggle: (Goal) -> Unit, onOpen: (Goal) -> Unit, onDelete: (Goal) -> Unit) {
    val c = Kelo.colors
    val doneLabel = stringResource(R.string.goals_mark_done)
    val meta = remember(g.note) { GoalMetaCodec.parse(g.note) }
    var checked by remember(g.status) { mutableStateOf(g.isDone) }
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(if (selected) c.mine else c.card).border(1.dp, c.line, RoundedCornerShape(12.dp))
            .clickable(role = Role.Button) { onOpen(g) }.padding(vertical = 6.dp, horizontal = 4.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Checkbox(
            checked = checked, onCheckedChange = { checked = !g.isDone; onToggle(g) },
            modifier = Modifier.semantics { contentDescription = doneLabel },
            colors = CheckboxDefaults.colors(checkedColor = c.success, uncheckedColor = c.muted, checkmarkColor = c.onSuccess),
        )
        Column(Modifier.weight(1f).padding(top = 12.dp, end = 4.dp, bottom = 6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(g.title, style = MaterialTheme.typography.bodyLarge, color = if (checked) c.muted else c.ink,
                textDecoration = if (checked) TextDecoration.LineThrough else null, maxLines = 3, overflow = TextOverflow.Ellipsis)
            if (!g.isDone && g.nextStep != null) Text(stringResource(R.string.goals_next, g.nextStep!!), style = MaterialTheme.typography.bodySmall, color = c.ink,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (meta.note.isNotBlank()) Text(meta.note, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (g.category.isNotBlank()) TonePill("#" + g.category, c.muted)
                if (g.plan.total > 0) TonePill(stringResource(R.string.goals_plan_count, g.plan.done, g.plan.total), c.success)
                when (GoalsRules.activity(g)) {
                    GoalActivity.WORKING -> TonePill(stringResource(R.string.goals_working), c.success)
                    GoalActivity.ASKING -> TonePill(stringResource(R.string.goals_asking), c.accent)
                    null -> {}
                }
                meta.due?.let { d ->
                    val late = !g.isDone && GoalsRules.overdue(d, today)
                    TonePill(stringResource(if (late) R.string.goals_due_late else R.string.goals_due_by, goalDayText(d)), if (late) c.danger else c.accent)
                }
            }
            meta.progress?.let { p ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    LinearProgressIndicator(progress = { p / 100f }, modifier = Modifier.weight(1f).height(4.dp).clip(RoundedCornerShape(2.dp)),
                        color = c.success, trackColor = c.line, drawStopIndicator = {})
                    Text(stringResource(R.string.goals_progress_value, p), style = MaterialTheme.typography.labelSmall, color = c.muted)
                }
            }
        }
        IconButton(onClick = { onDelete(g) }) { Icon(KeloIcons.Trash, contentDescription = stringResource(R.string.delete), tint = c.muted, modifier = Modifier.size(18.dp)) }
    }
}

@Composable
internal fun TonePill(text: String, tone: Color) {
    Text(text, style = MaterialTheme.typography.labelSmall, color = tone, maxLines = 2, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(tone.copy(alpha = 0.1f)).padding(horizontal = 6.dp, vertical = 2.dp))
}

/** A goal Kelo found in the conversations: what, and why it fits; 추가 makes it a goal, 아니에요 waves it off for good. */
@Composable
private fun SuggestionCard(s: GoalSuggestion, onAdd: () -> Unit, onNo: () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(14.dp))
            .padding(start = 14.dp, end = 8.dp, top = 12.dp, bottom = 4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(KeloIcons.Spark, contentDescription = null, tint = c.accent, modifier = Modifier.padding(top = 2.dp).size(18.dp))
            Text(s.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, color = c.ink, modifier = Modifier.weight(1f))
        }
        if (s.reason.isNotBlank()) Text(s.reason, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(start = 26.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = onNo) { Text(stringResource(R.string.goals_suggest_no), color = c.muted) }
            TextButton(onClick = onAdd) { Text(stringResource(R.string.goals_suggest_add), color = c.accent, fontWeight = FontWeight.SemiBold) }
        }
    }
}

/** "이 다짐에 묶인 예약 N개도 끌까요?" before a goal with schedules on is deleted: turn them off, keep them running, or not delete. */
@Composable
internal fun SchedulesQuestion(count: Int, onPause: () -> Unit, onKeep: () -> Unit, onCancel: () -> Unit) {
    val c = Kelo.colors
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onCancel,
        containerColor = c.card,
        title = { Text(pluralStringResource(R.plurals.goals_delete_schedules_q, count, count), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(stringResource(R.string.goals_delete_schedules_body), color = c.ink)
                PrimaryButton(stringResource(R.string.goals_delete_schedules_off), onClick = onPause, modifier = Modifier.fillMaxWidth())
                SecondaryButton(stringResource(R.string.goals_delete_schedules_keep), onClick = onKeep, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = { TextButton(onClick = onCancel) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}
