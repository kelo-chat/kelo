@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

package chat.kelo.app.work

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.agent.AgentViewModel
import chat.kelo.app.agent.BrowserTabPage
import chat.kelo.app.agent.ScheduleEditPage
import chat.kelo.app.agent.TaskPage
import chat.kelo.app.agent.dayLabel
import chat.kelo.app.notify.OpenTarget
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.shell.LocalShellChrome
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.Routes
import chat.kelo.app.shell.ShellBarActions
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.shell.headerHeight
import chat.kelo.app.ui.PushHost
import chat.kelo.app.ui.components.ChoiceChip
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.filter
import java.time.LocalDate

/** The 작업 tab's pages over its timeline, written as short strings so the stack survives a rotation. */
internal object WorkPages {
    const val NEW_SCHEDULE = "schedule:new"
    const val BROWSERS = "browsers"
    const val ARCHIVE = "archive"
    /** 보관한 예약 (reached from 예약's 보관함 entry) */
    const val ARCHIVED = "archived"
    fun watch(id: Long) = "watch:$id"
    fun task(id: Long) = "task:$id"
    fun job(id: String) = "job:$id"
    fun schedule(id: Long) = "schedule:$id"
}

/**
 * The 작업 tab: one timeline of everything the agent does. 지금 하는 일 (what runs now, background jobs nested under the task that
 * handed them off, each with its live browser one tap away), 답이 필요해요 (approvals, payments that need this phone's card, jobs
 * stopped for the user), 다가오는 일 (the schedules as an agenda by day and time) and 지난 일 (finished work by day, with a filter).
 * The bar's menu opens the browser tabs that are open, a new browser job and a new schedule. [start] is a place a notification
 * asked for; [onOpenLive] shows a browser tab live over everything.
 */
@Composable
fun WorkScreen(start: OpenTarget?, onStartUsed: () -> Unit, onOpenLive: (String) -> Unit, onClose: () -> Unit = {}) {
    val container = LocalAppContainer.current
    val vm: AgentViewModel = viewModel { AgentViewModel(container.sessions) }
    val chrome = LocalShellChrome.current
    var stack by rememberSaveable { mutableStateOf(listOf<String>()) }
    fun setStack(s: List<String>) { stack = s; chrome.hide(CHROME_KEY, s.isNotEmpty()) }
    fun push(p: String) = setStack(stack + p)
    val back: () -> Unit = { setStack(stack.dropLast(1)) }
    HideShellChrome(CHROME_KEY, stack.isNotEmpty())
    val list = rememberLazyListState()
    var jumpTo by remember { mutableStateOf<String?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val context = LocalContext.current

    LaunchedEffect(start) {
        when (start) {
            is OpenTarget.Task -> setStack(listOf(WorkPages.task(start.id)))
            is OpenTarget.Schedule -> setStack(listOf(WorkPages.schedule(start.id)))
            OpenTarget.Approvals -> { setStack(emptyList()); jumpTo = SECTION_ASKS }
            OpenTarget.Schedules -> { setStack(emptyList()); jumpTo = SECTION_UPCOMING }
            OpenTarget.Activity -> { setStack(emptyList()); jumpTo = SECTION_NOW }
            OpenTarget.Watchers -> { setStack(emptyList()); jumpTo = SECTION_WATCH }
            else -> {}
        }
        if (start != null) onStartUsed()
    }
    LaunchedEffect(vm) {
        vm.messages.collect { m ->
            snackbar.currentSnackbarData?.dismiss()
            snackbar.showSnackbar(if (m.detail != null) context.getString(m.text, m.detail) else context.getString(m.text, *m.args.toTypedArray()))
        }
    }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { vm.loadWork() }
    // new things show up while the tab is open: often while the agent works, now and then otherwise
    val status by container.agentActivity.status.collectAsStateWithLifecycle()
    val following by container.agentActivity.following.collectAsStateWithLifecycle()
    val busy = status?.busy == true || following || (status?.pendingApprovals ?: 0) > 0
    LaunchedEffect(busy) {
        while (true) {
            delay(if (busy) LIVE_POLL_MS else IDLE_POLL_MS)
            vm.refreshLive()
        }
    }
    OnTabReselect(Routes.WORK) { if (stack.isNotEmpty()) back() else list.animateScrollToItem(0) }

    Box(Modifier.fillMaxSize()) {
        PushHost(state = stack, depth = { it.size }, back = if (stack.isEmpty()) null else stack.dropLast(1), onBack = back) { shown ->
            val page = shown.lastOrNull()
            when {
                page == null -> androidx.compose.foundation.layout.Column(Modifier.fillMaxSize().background(Kelo.colors.bg).statusBarsPadding()) {
                    chat.kelo.app.ui.components.BackTitleBar(stringResource(R.string.tab_work), onClose)
                    Box(Modifier.weight(1f)) {
                        Timeline(vm, list, jumpTo, onJumped = { jumpTo = null }, push = ::push, onOpenLive = onOpenLive)
                    }
                }
                page.startsWith("task:") -> TaskPage(vm, page.removePrefix("task:").toLongOrNull() ?: 0, back, onOpenTab = onOpenLive)
                page.startsWith("job:") -> JobPage(vm, page.removePrefix("job:"), back, onOpenLive = onOpenLive,
                    onOpenTask = { push(WorkPages.task(it)) })
                page == WorkPages.NEW_SCHEDULE -> ScheduleEditPage(vm, null, back)
                page.startsWith("schedule:") -> ScheduleEditPage(vm, page.removePrefix("schedule:").toLongOrNull(), back)
                page == WorkPages.BROWSERS -> OpenBrowsersPage(vm, back, onOpenLive)
                page == WorkPages.ARCHIVE -> SchedulesPage(vm, back, onNew = { push(WorkPages.NEW_SCHEDULE) }, onOpen = { push("schedule:$it") },
                    onArchive = { push(WorkPages.ARCHIVED) })
                page == WorkPages.ARCHIVED -> ArchivedSchedulesPage(vm, back)
                page.startsWith("watch:") -> WatcherPage(vm, page.removePrefix("watch:").toLongOrNull() ?: 0, back)
                else -> Timeline(vm, list, jumpTo, onJumped = { jumpTo = null }, push = ::push, onOpenLive = onOpenLive)
            }
        }
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).padding(bottom = if (stack.isEmpty()) bottomBarHeight() + 8.dp else 16.dp).padding(horizontal = 12.dp)) { data ->
            Snackbar(data, shape = RoundedCornerShape(8.dp), containerColor = Kelo.colors.ink, contentColor = Kelo.colors.bg)
        }
    }
}

private const val CHROME_KEY = "work-sub"
private const val LIVE_POLL_MS = 4_000L
private const val IDLE_POLL_MS = 20_000L
private const val SEEN_AFTER_MS = 1_500L
internal const val SECTION_NOW = "sec:now"
internal const val SECTION_ASKS = "sec:asks"
internal const val SECTION_UPCOMING = "sec:upcoming"
internal const val SECTION_WATCH = "sec:watch"
internal const val SECTION_PAST = "sec:past"

@Composable
private fun Timeline(
    vm: AgentViewModel, list: androidx.compose.foundation.lazy.LazyListState, jumpTo: String?, onJumped: () -> Unit,
    push: (String) -> Unit, onOpenLive: (String) -> Unit,
) {
    val tasks by vm.tasks.collectAsStateWithLifecycle()
    val workers by vm.workers.collectAsStateWithLifecycle()
    val perms by vm.perms.collectAsStateWithLifecycle()
    val schedules by vm.schedules.collectAsStateWithLifecycle()
    val browser by vm.browser.collectAsStateWithLifecycle()
    val watchers by vm.watchers.collectAsStateWithLifecycle()
    val followups by vm.followups.collectAsStateWithLifecycle()
    val followupBusy by vm.followupBusy.collectAsStateWithLifecycle()
    var filter by rememberSaveable { mutableStateOf(PastFilter.ALL) }
    var showPaused by rememberSaveable { mutableStateOf(false) }
    val today = remember { LocalDate.now() }
    val taskItems = tasks.data?.items.orEmpty()
    val workerItems = workers.data?.items.orEmpty()
    val nested = remember(taskItems, workerItems) { Timeline.nest(taskItems, workerItems) }
    val now = remember(nested) { Timeline.now(nested) }
    val entries by vm.entries.collectAsStateWithLifecycle()
    val container = chat.kelo.app.LocalAppContainer.current
    val asks = remember(perms.data?.pending, workerItems, followups.data, entries.data) {
        Timeline.asks(perms.data?.pending.orEmpty(), workerItems, followups.data?.items.orEmpty(), entries.data.orEmpty())
    }
    val plan = remember(schedules.data) { Agenda.build(schedules.data?.items.orEmpty()) }
    val past = remember(nested, filter) { Timeline.past(nested, filter, today) }
    val tabs = browser.data?.items.orEmpty()
    val answering = perms.data?.answering.orEmpty()

    // looked at: the finished ones count as seen (their dots stay for this visit)
    val topDone = taskItems.firstOrNull { !it.running }?.id
    LaunchedEffect(topDone) {
        if (topDone == null) return@LaunchedEffect
        delay(SEEN_AFTER_MS)
        vm.markSeen(topDone)
    }
    // more of the past when its end comes into view
    LaunchedEffect(list) {
        snapshotFlow { list.layoutInfo.visibleItemsInfo.lastOrNull()?.index to list.layoutInfo.totalItemsCount }
            .distinctUntilChanged()
            .filter { (last, total) -> last != null && total > 0 && last >= total - 3 }
            .collect { vm.loadMoreTasks() }
    }
    // a notification asked for one section
    LaunchedEffect(jumpTo, tasks.data != null) {
        val key = jumpTo ?: return@LaunchedEffect
        if (tasks.data == null) return@LaunchedEffect
        val i = list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == key }?.index ?: sectionIndex(list, key)
        if (i != null) list.animateScrollToItem(i)
        onJumped()
    }

    LazyColumn(
        Modifier.fillMaxSize(), state = list,
        contentPadding = PaddingValues(start = 12.dp, end = 16.dp, top = headerHeight() + 8.dp, bottom = bottomBarHeight() + 32.dp),
    ) {
        fun asksSection() {
            // ---- 답이 필요해요
            item(key = SECTION_ASKS, contentType = "section") { SectionTitle(stringResource(R.string.work_asks), asks.size, attention = asks.isNotEmpty()) }
            if (perms.data != null && asks.isEmpty()) item(key = "asks:empty") { RailNote { EmptyLine(stringResource(R.string.work_asks_empty)) } }
            asks.forEach { a ->
                item(key = a.key, contentType = "ask") {
                    RailItem(RailMarker.ASK, label = null) {
                        when (a) {
                            is Ask.Approval -> ApprovalAsk(vm, a, working = a.approval.id in answering)
                            is Ask.Job -> JobAsk(vm, a.worker, onOpen = { push(WorkPages.job(a.worker.id)) }, onOpenLive = onOpenLive)
                            is Ask.Hold -> HoldAsk(vm, a.followup, busy = a.followup.id in followupBusy)
                            is Ask.Entry -> EntryAsk(a, onOpen = { container.openRequests.value = chat.kelo.app.notify.OpenTarget.Entry(a.request.id) },
                                onCancel = { vm.cancelEntry(a.request) { kind -> container.agentActivity.entryClosed(kind) } })
                        }
                    }
                }
            }
        }
        // ---- the tab's title and its shortcuts (open browsers, a new browser job, a new schedule, archived schedules)
        item(key = "title", contentType = "title") {
            androidx.compose.foundation.layout.Column {
                WorkShortcuts(
                    onBrowsers = { push(WorkPages.BROWSERS) }, onSchedules = { push(WorkPages.ARCHIVE) },
                )
            }
        }
        if (asks.isNotEmpty()) asksSection()
        // ---- 지금 하는 일
        item(key = SECTION_NOW, contentType = "section") { SectionTitle(stringResource(R.string.work_now), now.count) }
        when {
            tasks.data == null && tasks.failed -> item(key = "now:err") { RailNote { ErrorText(stringResource(R.string.connect_failed), small = true) } }
            tasks.data == null -> item(key = "now:load") { RailNote { SkeletonRows(2) } }
            now.empty -> item(key = "now:empty") { RailNote { EmptyLine(stringResource(R.string.work_now_empty)) } }
        }
        now.tasks.forEach { node ->
            item(key = "now:" + node.key, contentType = "now") {
                RailItem(RailMarker.LIVE, label = null) {
                    NowTaskCard(vm, node, tabs, workerItems, onOpen = { push(WorkPages.task(node.task.id)) },
                        onOpenJob = { push(WorkPages.job(it)) }, onOpenTask = { push(WorkPages.task(it)) }, onOpenLive = onOpenLive)
                }
            }
        }
        now.jobs.forEach { job ->
            item(key = "now:" + job.key, contentType = "now") {
                RailItem(RailMarker.LIVE, label = null) {
                    JobCard(vm, job, onOpen = { push(WorkPages.job(job.worker.id)) }, onOpenLive = onOpenLive)
                }
            }
        }
        perms.data?.taskGrants.orEmpty().forEach { g ->
            item(key = "grant:" + g.id, contentType = "grant") {
                RailItem(RailMarker.LIVE, label = null) { TaskGrantCard(g, onRevoke = { vm.revokeTaskPermission(g) }) }
            }
        }

        // ---- 답이 필요해요 (here while nothing waits; at the top, above 지금 하는 일, while something does)
        if (asks.isEmpty()) asksSection()

        // ---- 지켜보는 중 (only when the server has watchers and there are some)
        val watching = Watching.order(watchers.data?.items.orEmpty())
        if (watching.isNotEmpty()) {
            item(key = SECTION_WATCH, contentType = "section") { SectionTitle(stringResource(R.string.work_watching), watching.count { it.enabled }) }
            watching.forEach { w ->
                item(key = "watch:" + w.id, contentType = "watch") {
                    RailItem(if (w.enabled) RailMarker.LIVE else RailMarker.PAUSED, label = null) { WatcherRow(vm, w, onOpen = { push(WorkPages.watch(w.id)) }) }
                }
            }
        }

        // ---- 다가오는 일
        item(key = SECTION_UPCOMING, contentType = "section") {
            SectionTitle(stringResource(R.string.work_upcoming), plan.days.sumOf { it.entries.size } + plan.unscheduled.size)
        }
        when {
            schedules.data == null && schedules.failed -> item(key = "up:err") { RailNote { ErrorText(stringResource(R.string.connect_failed), small = true) } }
            schedules.data == null -> item(key = "up:load") { RailNote { SkeletonRows(2) } }
            plan.empty -> item(key = "up:empty") { RailNote { EmptyLine(stringResource(R.string.agent_schedules_empty)) } }
        }
        agendaItems(plan, today, showPaused, onTogglePaused = { showPaused = !showPaused }, onOpen = { push(WorkPages.schedule(it)) },
            onResume = { vm.resumeSchedule(it) })

        // ---- 지난 일
        item(key = SECTION_PAST, contentType = "section") { SectionTitle(stringResource(R.string.work_past), null) }
        item(key = "past:filters", contentType = "filters") {
            // wraps to a second line instead of scrolling sideways, so no chip is ever cut at the edge
            androidx.compose.foundation.layout.FlowRow(
                Modifier.fillMaxWidth().padding(start = RAIL_WIDTH, bottom = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                PastFilter.entries.forEach { f -> ChoiceChip(filterLabel(f), selected = filter == f, onClick = { filter = f }) }
            }
        }
        if (tasks.data != null && past.isEmpty()) item(key = "past:empty") { RailNote { EmptyLine(stringResource(R.string.agent_activity_empty)) } }
        past.forEach { g ->
            item(key = "past:day:" + g.label.toString(), contentType = "day") { DayCaption(dayLabel(g.label)) }
            g.items.forEach { node ->
                item(key = "past:" + node.key, contentType = "past") {
                    RailItem(if (node.problem) RailMarker.FAILED else RailMarker.DONE, label = node.task.startedAt.takeIf { it > 0 }?.let { chat.kelo.app.agent.clockText(it) }) {
                        PastRow(node, onOpen = { push(WorkPages.task(node.task.id)) }, onOpenJob = { push(WorkPages.job(it)) })
                    }
                }
            }
        }
        if (tasks.data?.loadingMore == true) {
            item(key = "past:more") {
                Box(Modifier.fillMaxWidth().padding(16.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(Modifier.size(22.dp), color = Kelo.colors.muted, strokeWidth = 2.dp)
                }
            }
        }
    }
}

/** The shortcuts under the tab's title, as small chips that wrap onto a second line in long languages. */
@OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
@Composable
private fun WorkShortcuts(onBrowsers: () -> Unit, onSchedules: () -> Unit) {
    androidx.compose.foundation.layout.FlowRow(
        Modifier.fillMaxWidth().padding(start = 4.dp, top = 4.dp), horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        ShortcutChip(KeloIcons.Globe, stringResource(R.string.work_open_browsers), onBrowsers)
        ShortcutChip(KeloIcons.Calendar, stringResource(R.string.work_schedules), onSchedules)
    }
}

@Composable
private fun ShortcutChip(icon: androidx.compose.ui.graphics.vector.ImageVector, text: String, onClick: () -> Unit) {
    val c = Kelo.colors
    androidx.compose.material3.OutlinedButton(
        onClick = onClick, shape = RoundedCornerShape(8.dp), border = androidx.compose.foundation.BorderStroke(1.dp, c.line),
        colors = androidx.compose.material3.ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink),
        contentPadding = PaddingValues(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Icon(icon, contentDescription = null, tint = c.ink, modifier = Modifier.size(16.dp).padding(end = 2.dp))
        Text(text, style = MaterialTheme.typography.bodySmall)
    }
}

/** Where a section's heading is in the list (counting the items before it), for a jump the first time it is not on screen. */
private fun sectionIndex(list: androidx.compose.foundation.lazy.LazyListState, key: String): Int? =
    list.layoutInfo.visibleItemsInfo.firstOrNull { it.key == key }?.index ?: when (key) {
        SECTION_NOW -> 0
        else -> null
    }

/** The agenda: a caption per day, each run with its time on the rail; switched-off schedules folded at the end. */
private fun LazyListScope.agendaItems(
    plan: Agenda.Plan, today: LocalDate, showPaused: Boolean, onTogglePaused: () -> Unit, onOpen: (Long) -> Unit, onResume: (Long) -> Unit,
) {
    plan.days.forEach { day ->
        item(key = "up:day:" + day.date, contentType = "day") { DayCaption(agendaDayLabel(day.date, today)) }
        day.entries.forEach { e ->
            item(key = "up:" + day.date + ":" + e.schedule.id, contentType = "agenda") {
                RailItem(RailMarker.PLANNED, label = e.at?.let { chat.kelo.app.agent.clockText(it) }) { AgendaRow(e.schedule) { onOpen(e.schedule.id) } }
            }
        }
    }
    if (plan.unscheduled.isNotEmpty()) {
        item(key = "up:unscheduled", contentType = "day") { DayCaption(androidx.compose.ui.res.stringResource(R.string.work_upcoming_unscheduled)) }
        plan.unscheduled.forEach { s ->
            item(key = "up:u:" + s.id, contentType = "agenda") { RailItem(RailMarker.PLANNED, label = null) { AgendaRow(s) { onOpen(s.id) } } }
        }
    }
    // paused by a run: always shown (they want a look); switched off by hand: folded
    plan.paused.filter { it.paused }.forEach { s ->
        item(key = "up:pp:" + s.id, contentType = "agenda") { RailItem(RailMarker.ASK, label = null) { AgendaRow(s, onResume = { onResume(s.id) }) { onOpen(s.id) } } }
    }
    val off = plan.paused.filter { !it.paused }
    if (off.isNotEmpty()) {
        item(key = "up:paused", contentType = "paused") { PausedToggle(off.size, showPaused, onTogglePaused) }
        if (showPaused) off.forEach { s ->
            item(key = "up:p:" + s.id, contentType = "agenda") { RailItem(RailMarker.PAUSED, label = null) { AgendaRow(s) { onOpen(s.id) } } }
        }
    }
}

@Composable
private fun filterLabel(f: PastFilter): String = stringResource(
    when (f) {
        PastFilter.ALL -> R.string.work_filter_all
        PastFilter.DONE -> R.string.work_filter_done
        PastFilter.PROBLEMS -> R.string.work_filter_problems
        PastFilter.AUTOMATIC -> R.string.work_filter_automatic
    },
)

@Composable
private fun agendaDayLabel(date: LocalDate, today: LocalDate): String = when (Agenda.whenOf(date, today)) {
    Agenda.When.TODAY -> stringResource(R.string.work_day_today, chat.kelo.app.agent.dayText(date))
    Agenda.When.TOMORROW -> stringResource(R.string.work_day_tomorrow, chat.kelo.app.agent.dayText(date))
    Agenda.When.LATER -> chat.kelo.app.agent.dayText(date)
}

/** A browser tab live, over everything (from a chat card, a notification or a task). */
@Composable
fun LiveOverlay(tabId: String, takeControl: Boolean = false, picture: String? = null, onClose: () -> Unit) {
    val container = LocalAppContainer.current
    val vm: AgentViewModel = viewModel(key = "live-overlay") { AgentViewModel(container.sessions) }
    Box(Modifier.fillMaxSize().background(Kelo.colors.bg)) { BrowserTabPage(vm, tabId, onClose, takeControl, picture) }
}
