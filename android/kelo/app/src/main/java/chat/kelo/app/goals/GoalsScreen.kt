package chat.kelo.app.goals

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.SwipeToDismissBoxState
import androidx.compose.material3.SwipeToDismissBoxValue
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.agent.dayText
import chat.kelo.app.core.Goal
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.Routes
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.shell.headerHeight
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.StaleHint
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import kotlinx.coroutines.launch
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset

/**
 * The 다짐 tab (goals): 해볼 만한 다짐 (goals Kelo found in the conversations: 추가 / 아니에요) on top, then 진행 중 (the nearest due
 * date first) and 끝남 (folded until opened); each goal has an optional tag, due date and progress, and shows how far its plan is.
 * The button at the bottom adds one (Kelo then starts on it in the chat); a tap on a goal opens its own page ([GoalDetailPage]).
 * A goal is removed by swiping it away, with an undo offered for a few seconds before it is deleted on the server.
 */
@Composable
fun GoalsScreen() {
    val sessions = LocalAppContainer.current.sessions
    val vm: GoalsViewModel = viewModel { GoalsViewModel(sessions) }
    val state by vm.state.collectAsStateWithLifecycle()
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { vm.load() }
    RefreshWhile(GoalsRules.refreshMs(state.goals)) { vm.load() }
    val c = Kelo.colors
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val removedText = stringResource(R.string.goals_removed)
    val undoText = stringResource(R.string.undo)
    var editing by remember { mutableStateOf<Goal?>(null) }
    var openedId by rememberSaveable { mutableStateOf<Long?>(null) }
    val opened = openedId?.let { id -> state.goals.firstOrNull { it.id == id } }
    val container = LocalAppContainer.current
    val addedText = stringResource(R.string.goals_suggest_added)
    var adding by rememberSaveable { mutableStateOf(false) }
    var showDone by rememberSaveable { mutableStateOf(false) }
    val today = remember { LocalDate.now() }
    // a goal with schedules on asks first whether to turn them off too; cancelling puts the swiped row back (a new swipe state)
    var askSchedules by remember { mutableStateOf<Goal?>(null) }
    var swipeReset by remember { mutableStateOf(0) }
    val startRemove: (Goal, Boolean) -> Unit = { g, pause ->
        vm.startRemove(g, pause)
        scope.launch {
            snackbar.currentSnackbarData?.dismiss()   // an earlier undo offer ends: that one is deleted now
            val r = try {
                snackbar.showSnackbar(removedText.format(g.title), actionLabel = undoText, duration = SnackbarDuration.Long)
            } catch (e: kotlinx.coroutines.CancellationException) {
                vm.commitRemove(g)   // left the page while the offer was up: the swipe stands
                throw e
            }
            if (r == SnackbarResult.ActionPerformed) vm.undoRemove(g) else vm.commitRemove(g)
        }
    }
    val remove: (Goal) -> Unit = { g ->
        haptics.tick()
        if (GoalsRules.asksAboutSchedules(g)) askSchedules = g else startRemove(g, false)
    }
    val toggle: (Goal) -> Unit = { g ->
        if (!g.isDone) haptics.confirm() else haptics.tick()
        vm.toggle(g)
    }

    val listState = rememberLazyListState()
    OnTabReselect(Routes.GOALS) { listState.animateScrollToItem(0) }
    Box(Modifier.fillMaxSize()) {
        LazyColumn(
            Modifier.fillMaxSize(), state = listState,
            contentPadding = PaddingValues(start = 8.dp, end = 12.dp, top = headerHeight() + 4.dp, bottom = bottomBarHeight() + 96.dp),
        ) {
            if (state.failed) item(key = "err") { ErrorText(stringResource(R.string.connect_failed), small = true, modifier = Modifier.padding(16.dp)) }
            if (state.shownSuggestions.isNotEmpty()) {
                item(key = "h:suggest") { GroupHeading(stringResource(R.string.goals_suggest_title), state.shownSuggestions.size) }
                items(state.shownSuggestions, key = { "s" + it.id }, contentType = { "suggestion" }) { s ->
                    Box(Modifier.animateItem()) {
                        SuggestionCard(s, onAdd = {
                            haptics.confirm()
                            vm.acceptSuggestion(s) { scope.launch { snackbar.showSnackbar(addedText.format(s.title)) } }
                        }, onNo = { haptics.tick(); vm.dismissSuggestion(s) })
                    }
                }
            }
            item(key = "h:open") { GroupHeading(stringResource(R.string.goals_open), state.tracking.size) }
            if (state.loaded && state.tracking.isEmpty()) {
                item(key = "empty") { MutedText(stringResource(R.string.goals_empty), small = true, modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)) }
            }
            goalRows(state.tracking, today, swipeReset, onToggle = toggle, onOpen = { openedId = it.id }, onDelete = remove)
            if (state.done.isNotEmpty()) {
                item(key = "h:done") {
                    GroupHeading(stringResource(R.string.goals_done), state.done.size) {
                        LinkButton(stringResource(if (showDone) R.string.goals_hide else R.string.goals_show), onClick = { showDone = !showDone })
                    }
                }
                if (showDone) goalRows(state.done, today, swipeReset, onToggle = toggle, onOpen = { openedId = it.id }, onDelete = remove)
            }
        }
        StaleHint(
            state.offline && snackbar.currentSnackbarData == null,
            Modifier.align(Alignment.BottomCenter).padding(bottom = bottomBarHeight() + 80.dp), onRetry = vm::load,
        )
        ExtendedFloatingActionButton(
            onClick = { adding = true }, containerColor = c.accent, contentColor = c.onAccent,
            icon = { Icon(KeloIcons.Plus, contentDescription = null) }, text = { Text(stringResource(R.string.goals_add)) },
            modifier = Modifier.align(Alignment.BottomEnd).padding(end = 16.dp, bottom = bottomBarHeight() + 16.dp),
        )
        SnackbarHost(
            snackbar,
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = bottomBarHeight() + 8.dp).padding(horizontal = 12.dp),
        ) { data ->
            Snackbar(data, shape = RoundedCornerShape(8.dp), containerColor = c.ink, contentColor = c.bg, actionColor = c.accent)
        }
    }
    HideShellChrome("goal-detail", opened != null)
    if (opened != null) {
        Box(Modifier.fillMaxSize().background(c.bg)) {
            GoalDetailPage(opened, onBack = { openedId = null; vm.load() }, onEdit = { editing = it }, onOpenChat = {
                openedId = null
                container.openRequests.value = chat.kelo.app.notify.OpenTarget.Chat
            })
        }
    }
    askSchedules?.let { g ->
        ScheduleQuestion(
            g.schedulesOn,
            onAnswer = { pause -> askSchedules = null; startRemove(g, pause) },
            onCancel = { askSchedules = null; swipeReset++ },
        )
    }
    if (adding) GoalSheet(null, onClose = { adding = false }) { title, note, tag, done -> vm.add(title, note, tag) { done() } }
    editing?.let { g -> GoalSheet(g, onClose = { editing = null }) { title, note, tag, done -> vm.update(g, title, note, tag) { done() } } }
}

@Composable
private fun GroupHeading(text: String, count: Int, trailing: @Composable () -> Unit = {}) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 16.dp, bottom = 4.dp).semantics { heading() },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = c.accent)
        Text(" · $count", style = MaterialTheme.typography.labelLarge, color = c.muted, modifier = Modifier.weight(1f))
        trailing()
    }
}

private fun LazyListScope.goalRows(
    goals: List<Goal>, today: LocalDate, swipeReset: Int, onToggle: (Goal) -> Unit, onOpen: (Goal) -> Unit, onDelete: (Goal) -> Unit,
) {
    items(goals, key = { it.id }, contentType = { "goal" }) { g ->
        Box(Modifier.animateItem()) { SwipeAway(swipeReset, onSwiped = { onDelete(g) }) { GoalRow(g, today, onToggle, onOpen) } }
    }
}

/** Before a goal with schedules on goes: turn those schedules off too, or keep them running without the goal. Cancel keeps the goal. */
@Composable
private fun ScheduleQuestion(count: Int, onAnswer: (pause: Boolean) -> Unit, onCancel: () -> Unit) {
    val c = Kelo.colors
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onCancel,
        containerColor = c.card,
        title = { Text(pluralStringResource(R.plurals.goals_delete_schedules_q, count, count), color = c.ink, style = MaterialTheme.typography.titleMedium) },
        text = { Text(stringResource(R.string.goals_delete_schedules_body), color = c.muted, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = {
            Column(horizontalAlignment = Alignment.End) {
                TextButton(onClick = { onAnswer(true) }) { Text(stringResource(R.string.goals_delete_schedules_off), color = c.danger) }
                TextButton(onClick = { onAnswer(false) }) { Text(stringResource(R.string.goals_delete_schedules_keep), color = c.ink) }
                TextButton(onClick = onCancel) { Text(stringResource(R.string.cancel), color = c.muted) }
            }
        },
    )
}

/** A row that can be swiped toward the start to remove it; the bin shows on red under it while it moves. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SwipeAway(reset: Int, onSwiped: () -> Unit, content: @Composable () -> Unit) {
    val c = Kelo.colors
    val swiped by rememberUpdatedState(onSwiped)
    // not saved: a row brought back by "실행 취소" must come back in place, not restored as already swiped away
    val density = androidx.compose.ui.platform.LocalDensity.current
    val state = remember(reset) { SwipeToDismissBoxState(SwipeToDismissBoxValue.Settled, density, positionalThreshold = { it * 0.4f }) }
    LaunchedEffect(state.currentValue) {
        if (state.currentValue == SwipeToDismissBoxValue.EndToStart) swiped()
    }
    SwipeToDismissBox(
        state = state,
        enableDismissFromStartToEnd = false,
        backgroundContent = {
            Box(
                Modifier.fillMaxSize().clip(RoundedCornerShape(12.dp)).background(c.danger).padding(end = 18.dp),
                contentAlignment = Alignment.CenterEnd,
            ) { Icon(KeloIcons.Trash, contentDescription = null, tint = c.onDanger) }
        },
    ) {
        Box(Modifier.background(c.bg)) { content() }
    }
}

/** One goal: its box, the title, and under it the tag, the due date and the progress, when it has them. */
@Composable
private fun GoalRow(g: Goal, today: LocalDate, onToggle: (Goal) -> Unit, onOpen: (Goal) -> Unit) {
    val c = Kelo.colors
    val doneLabel = stringResource(R.string.goals_mark_done)
    val scope = rememberCoroutineScope()
    val reduce = LocalReduceMotion.current
    val bump = remember { Animatable(1f) }
    val meta = remember(g.note) { GoalMetaCodec.parse(g.note) }
    // shown checked at once; the server's answer then moves the goal to its section
    var checked by remember(g.status) { mutableStateOf(g.isDone) }
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button) { onOpen(g) }.padding(vertical = 8.dp, horizontal = 4.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Checkbox(
            checked = checked,
            onCheckedChange = {
                checked = !g.isDone
                if (!g.isDone && !reduce) scope.launch {
                    bump.animateTo(1.2f, tween(Motion.PRESS_MS, easing = Motion.Standard))
                    bump.animateTo(1f, Motion.bump())
                }
                onToggle(g)
            },
            modifier = Modifier.graphicsLayer { scaleX = bump.value; scaleY = bump.value }.semantics { contentDescription = doneLabel },
            colors = CheckboxDefaults.colors(checkedColor = c.success, uncheckedColor = c.muted, checkmarkColor = c.onSuccess),
        )
        Column(Modifier.weight(1f).padding(top = 12.dp, end = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(
                g.title, style = MaterialTheme.typography.bodyLarge, color = if (checked) c.muted else c.ink,
                textDecoration = if (checked) TextDecoration.LineThrough else null, maxLines = 3, overflow = TextOverflow.Ellipsis,
            )
            if (!g.isDone && g.nextStep != null) Text(stringResource(R.string.goals_next, g.nextStep), style = MaterialTheme.typography.bodySmall,
                color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (meta.note.isNotBlank()) Text(meta.note, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
                if (g.category.isNotBlank()) Pill("#" + g.category, c.muted)
                if (g.plan.total > 0) Pill(stringResource(R.string.goals_plan_count, g.plan.done, g.plan.total), c.success)
                when (GoalsRules.activity(g)) {
                    GoalActivity.WORKING -> Pill(stringResource(R.string.goals_working), c.success)
                    GoalActivity.ASKING -> Pill(stringResource(R.string.goals_asking), c.accent)
                    null -> {}
                }
                meta.due?.let { d ->
                    val late = !g.isDone && GoalsRules.overdue(d, today)
                    Pill(stringResource(if (late) R.string.goals_due_late else R.string.goals_due_by, dayText(d)), if (late) c.danger else c.accent)
                }
            }
            meta.progress?.let { p ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    LinearProgressIndicator(
                        progress = { p / 100f }, modifier = Modifier.weight(1f).height(4.dp).clip(RoundedCornerShape(2.dp)),
                        color = c.success, trackColor = c.line, drawStopIndicator = {},
                    )
                    Text(stringResource(R.string.goals_progress_value, p), style = MaterialTheme.typography.labelSmall, color = c.muted)
                }
            }
        }
    }
}

/** While the screen is in front, fetches again every [ms] (null: never), so "Kelo is working" / "waiting for you" follow the server. */
@Composable
internal fun RefreshWhile(ms: Long?, onRefresh: () -> Unit) {
    val lifecycle = androidx.lifecycle.compose.LocalLifecycleOwner.current.lifecycle
    val refresh by androidx.compose.runtime.rememberUpdatedState(onRefresh)
    LaunchedEffect(ms, lifecycle) {
        if (ms == null) return@LaunchedEffect
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) {
            while (true) {
                kotlinx.coroutines.delay(ms)
                refresh()
            }
        }
    }
}

@Composable
private fun Pill(text: String, tone: Color) {
    Text(
        text, style = MaterialTheme.typography.labelSmall, color = tone, maxLines = 1,
        modifier = Modifier.clip(RoundedCornerShape(6.dp)).background(tone.copy(alpha = 0.1f)).padding(horizontal = 6.dp, vertical = 2.dp),
    )
}

/** Adding or editing a goal: the title, a note, a tag of one's own, an optional due date and the progress. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun GoalSheet(goal: Goal?, onClose: () -> Unit, onSave: (title: String, note: String, tag: String, done: () -> Unit) -> Unit) {
    val c = Kelo.colors
    val meta = remember(goal?.id) { GoalMetaCodec.parse(goal?.note.orEmpty()) }
    var title by rememberSaveable(goal?.id) { mutableStateOf(goal?.title.orEmpty()) }
    var note by rememberSaveable(goal?.id) { mutableStateOf(meta.note) }
    var tag by rememberSaveable(goal?.id) { mutableStateOf(goal?.category.orEmpty()) }
    var dueDay by rememberSaveable(goal?.id) { mutableStateOf(meta.due?.toEpochDay()) }
    var progress by rememberSaveable(goal?.id) { mutableStateOf(meta.progress) }
    var picking by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onClose, sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(), containerColor = c.card) {
        Column(
            Modifier.fillMaxWidth().imePadding().verticalScroll(rememberScrollState()).padding(start = 20.dp, end = 20.dp, bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(stringResource(if (goal == null) R.string.goals_add else R.string.goals_edit), style = MaterialTheme.typography.titleLarge, color = c.ink)
            KeloTextField(title, { title = it }, stringResource(R.string.goals_title_placeholder), maxLength = 200)
            KeloTextField(note, { note = it }, stringResource(R.string.goals_note_placeholder), singleLine = false, maxLength = 1800)
            KeloTextField(tag, { tag = it }, stringResource(R.string.goals_tag_placeholder), maxLength = 40)
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(KeloIcons.Calendar, contentDescription = null, tint = c.muted, modifier = Modifier.size(20.dp))
                val due = dueDay?.let { LocalDate.ofEpochDay(it) }
                Text(
                    due?.let { stringResource(R.string.goals_due_by, dayText(it)) } ?: stringResource(R.string.goals_due_none),
                    style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f).padding(start = 10.dp),
                )
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
                Slider(
                    value = (progress ?: 0).toFloat(), onValueChange = { progress = (Math.round(it / 10f) * 10).coerceIn(0, 100) },
                    valueRange = 0f..100f, steps = 9,
                    colors = SliderDefaults.colors(thumbColor = c.accent, activeTrackColor = c.accent, inactiveTrackColor = c.line),
                )
            }
            PrimaryButton(stringResource(if (goal == null) R.string.goals_add_button else R.string.save), enabled = title.isNotBlank() && !saving, onClick = {
                saving = true
                val encoded = GoalMetaCodec.encode(GoalMeta(note, dueDay?.let { LocalDate.ofEpochDay(it) }, progress))
                onSave(title.trim(), encoded, GoalsRules.tag(tag)) { saving = false; onClose() }
            })
        }
    }
    LaunchedEffect(saving) { if (saving) { kotlinx.coroutines.delay(8_000); saving = false } }
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

/** A goal Kelo found in the conversations: what, and why it fits; 추가 makes it a goal, 아니에요 waves it off for good. */
@Composable
private fun SuggestionCard(s: GoalSuggestion, onAdd: () -> Unit, onNo: () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().padding(start = 8.dp, end = 4.dp, top = 4.dp, bottom = 4.dp).clip(RoundedCornerShape(14.dp))
            .background(c.card).padding(start = 14.dp, end = 8.dp, top = 12.dp, bottom = 4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(KeloIcons.Spark, contentDescription = null, tint = c.accent, modifier = Modifier.padding(top = 2.dp).size(18.dp))
            Text(s.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, color = c.ink, modifier = Modifier.weight(1f))
        }
        if (s.reason.isNotBlank()) Text(s.reason, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(start = 26.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            TextButton(onClick = onNo) { Text(stringResource(R.string.goals_suggest_no), color = c.muted, maxLines = 1) }
            TextButton(onClick = onAdd) { Text(stringResource(R.string.goals_suggest_add), color = c.accent, fontWeight = FontWeight.SemiBold, maxLines = 1) }
        }
    }
}
