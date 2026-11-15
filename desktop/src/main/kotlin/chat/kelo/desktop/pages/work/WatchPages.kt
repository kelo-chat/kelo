// Ported from android/kelo .../work/WatchPages.kt (keep in step with it).
package chat.kelo.desktop.pages.work

import androidx.compose.foundation.background
import androidx.compose.foundation.ContextMenuArea
import androidx.compose.foundation.ContextMenuItem
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.rememberCoroutineScope
import chat.kelo.app.ui.icons.KeloIcons
import kotlinx.coroutines.launch
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
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
import chat.kelo.app.ui.theme.Kelo

@Composable
internal fun watchWord(state: String): String = stringResource(
    when (state) {
        "watching" -> R.string.work_watch_on
        "error" -> R.string.work_watch_error
        "done" -> R.string.work_watch_done
        else -> R.string.work_watch_off
    },
)

@Composable
internal fun watchTone(state: String): Color = when (state) {
    "watching" -> Kelo.colors.accent
    "error" -> Kelo.colors.danger
    "done" -> Kelo.colors.success
    else -> Kelo.colors.muted
}

/** A watcher on the timeline: its name, what it looks for, the last value it saw, and its switch. */
@Composable
internal fun WatcherRow(vm: WorkModel, w: Watcher, onOpen: () -> Unit) {
    val c = Kelo.colors
    val haptics = rememberHaptics()
    val desc = stringResource(R.string.work_watch_switch, w.name)
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onOpen).padding(top = 8.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(w.name, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                StatusChip(watchWord(w.state), watchTone(w.state))
            }
            if (w.description.isNotBlank()) Text(w.description, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
            w.lastValue?.takeIf { it.isNotBlank() }?.let {
                Text(stringResource(R.string.work_watch_last_value, it), style = MaterialTheme.typography.labelSmall, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            w.lastFiredAt?.let { Text(stringResource(R.string.work_watch_fired_at, relText(it)), style = MaterialTheme.typography.labelSmall, color = c.success) }
            if (w.state == "error") w.lastError?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = c.danger, maxLines = 2, overflow = TextOverflow.Ellipsis) }
        }
        Switch(
            checked = w.enabled, onCheckedChange = { haptics.tick(); vm.setWatcherEnabled(w, it) },
            modifier = Modifier.semantics { contentDescription = desc },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
}

/**
 * One watcher: what it checks and how often, its last check, a check now (without the agent; needed before it may be turned on),
 * its switch, its recent events, and removing it.
 */
@Composable
internal fun WatcherPage(vm: WorkModel, id: Long, onBack: () -> Unit) {
    val c = Kelo.colors
    val state by vm.watchers.collectAsState()
    LaunchedEffect(id) { if (state.data == null) vm.loadWatchers() }
    val w = state.data?.items?.firstOrNull { it.id == id }
    var events by remember(id) { mutableStateOf<List<WatcherEvent>?>(null) }
    var checking by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    LaunchedEffect(id, w?.dryRun?.at, w?.lastCheckedAt) { events = runCatching { vm.watcherEvents(id) }.getOrDefault(emptyList()) }
    AgentSubPage(w?.name ?: stringResource(R.string.work_watching), onBack) {
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (w == null) {
                if (state.failed) ErrorText(stringResource(R.string.connect_failed)) else SkeletonRows(3)
                return@Column
            }
            Panel {
                WatcherRow(vm, w, onOpen = {})
                if (!w.enabled && !w.canEnable) MutedText(stringResource(R.string.work_watch_needs_check), small = true)
                w.disabledReason?.takeIf { it.isNotBlank() && !w.enabled }?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = c.danger) }
            }
            Panel {
                w.url?.takeIf { it.isNotBlank() }?.let { Fact(stringResource(R.string.work_watch_url), it) }
                if (w.everyMinutes > 0) Fact(stringResource(R.string.work_watch_every), intervalText(w.everyMinutes))
                w.lastCheckedAt?.let { Fact(stringResource(R.string.work_watch_checked), dayClockText(it)) }
                w.nextCheckAt?.let { Fact(stringResource(R.string.work_watch_next), relText(it)) }
                if (w.once) Fact(stringResource(R.string.work_watch_once), stringResource(R.string.work_watch_once_yes))
                w.note?.takeIf { it.isNotBlank() }?.let { Fact(stringResource(R.string.work_watch_note), it) }
            }
            w.dryRun?.let { d ->
                Label(stringResource(R.string.work_watch_last_check))
                Panel {
                    Text(
                        d.detail?.ifBlank { null } ?: d.error?.ifBlank { null } ?: d.value.orEmpty(),
                        style = MaterialTheme.typography.bodyMedium, color = if (d.ok) c.ink else c.danger,
                    )
                    d.at?.let { Text(dayClockText(it), style = MaterialTheme.typography.labelSmall, color = c.muted) }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                PrimaryButton(
                    stringResource(if (checking) R.string.work_watch_checking else R.string.work_watch_check_now), enabled = !checking,
                    onClick = { checking = true; vm.dryRunWatcher(w) { checking = false } }, modifier = Modifier.weight(1f),
                )
            }
            Label(stringResource(R.string.work_watch_events))
            val ev = events
            when {
                ev == null -> SkeletonRows(2)
                ev.isEmpty() -> EmptyNote(stringResource(R.string.work_watch_events_empty))
                else -> Panel {
                    ev.forEachIndexed { i, e ->
                        if (i > 0) HorizontalDivider(color = c.line)
                        Column(Modifier.padding(vertical = 6.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(eventWord(e.kind), style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold,
                                    color = if (e.kind == "fired") c.success else if (e.kind == "error") c.danger else c.muted)
                                Text(if (e.at > 0) dayClockText(e.at) else "", style = MaterialTheme.typography.labelSmall, color = c.muted)
                            }
                            val line = e.detail?.ifBlank { null } ?: e.value
                            if (!line.isNullOrBlank()) Text(line, style = MaterialTheme.typography.bodySmall, color = c.ink, maxLines = 3, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
            TaskGrantsBlock(id, load = { vm.watcherGrants(id) }, revoke = { vm.revokeScheduleGrant(it) })
            LinkButton(stringResource(R.string.work_watch_delete), onClick = { confirmDelete = true }, danger = true)
        }
    }
    if (confirmDelete && w != null) {
        ConfirmDialog(stringResource(R.string.work_watch_delete_confirm, w.name), stringResource(R.string.delete), onDismiss = { confirmDelete = false }, onYes = {
            vm.deleteWatcher(w, onBack)
        })
    }
}

@Composable
private fun eventWord(kind: String): String = stringResource(
    when (kind) {
        "fired" -> R.string.work_watch_ev_fired
        "error" -> R.string.work_watch_ev_error
        "dry_run" -> R.string.work_watch_ev_check
        "enabled" -> R.string.work_watch_ev_on
        "disabled" -> R.string.work_watch_ev_off
        else -> R.string.work_watch_ev_check
    },
)

@Composable
private fun Fact(label: String, value: String) {
    val c = Kelo.colors
    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.padding(vertical = 2.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(0.35f))
        Text(value, style = MaterialTheme.typography.bodySmall, color = c.ink, modifier = Modifier.weight(0.65f))
    }
}

@Composable
private fun Label(text: String) = Text(text, style = MaterialTheme.typography.labelLarge, color = Kelo.colors.accent, modifier = Modifier.padding(top = 6.dp))

@Composable
private fun Panel(content: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) { content() }
}

/**
 * 작업 → 예약: a 보관함 entry at the top (with how many are kept there), a button to add one, and every schedule (click to change it;
 * its trash button or the right-click menu puts it in the archive at once, with 실행 취소 on the message that follows).
 */
@Composable
internal fun SchedulesPage(vm: WorkModel, onBack: () -> Unit, onNew: () -> Unit, onOpen: (Long) -> Unit, onArchive: () -> Unit = {}) {
    val c = Kelo.colors
    val schedules by vm.schedules.collectAsState()
    val state by vm.archive.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val archivedWords = stringResource(R.string.agent_schedule_deleted)
    val undoWord = stringResource(R.string.undo)
    LaunchedEffect(Unit) { vm.loadArchive() }
    val archive: (Long) -> Unit = { id ->
        vm.archiveSchedule(id)
        scope.launch {
            snackbar.currentSnackbarData?.dismiss()
            if (snackbar.showSnackbar(archivedWords, undoWord, duration = SnackbarDuration.Short) == SnackbarResult.ActionPerformed) {
                vm.restoreSchedule(id)
            }
        }
    }
    Box(Modifier.fillMaxSize()) {
        AgentSubPage(stringResource(R.string.work_schedules), onBack) {
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                // 보관함: easy to find, at the top
                val kept = state.data?.items?.size ?: 0
                Row(
                    Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp))
                        .clickable(role = Role.Button, onClick = onArchive).padding(horizontal = 14.dp, vertical = 12.dp),
                    verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(stringResource(R.string.schedules_archive_entry), style = MaterialTheme.typography.bodyLarge, color = c.ink, modifier = Modifier.weight(1f))
                    if (kept > 0) Text(kept.toString(), style = MaterialTheme.typography.bodyMedium, color = c.muted)
                    Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                }
                PrimaryButton(stringResource(R.string.work_schedule_add), onClick = onNew, modifier = Modifier.fillMaxWidth())
                val items = schedules.data?.items.orEmpty()
                when {
                    schedules.data == null && schedules.failed -> ErrorText(stringResource(R.string.connect_failed), small = true)
                    schedules.data == null -> SkeletonRows(2)
                    items.isEmpty() -> EmptyNote(stringResource(R.string.work_schedules_empty))
                    else -> Panel {
                        items.forEachIndexed { i, s ->
                            if (i > 0) HorizontalDivider(color = c.line)
                            key(s.id) {
                                TrashRow(onTrash = { archive(s.id) }) {
                                    Column(
                                        Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).clickable(role = Role.Button) { onOpen(s.id) }.padding(vertical = 8.dp),
                                        verticalArrangement = Arrangement.spacedBy(2.dp),
                                    ) {
                                        Text(s.name, style = MaterialTheme.typography.bodyLarge, color = if (s.enabled) c.ink else c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                        val sub = if (!s.enabled) stringResource(R.string.work_schedule_off) else scheduleText(s)
                                        if (sub.isNotBlank()) Text(sub, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        UndoSnackbar(snackbar, Modifier.align(Alignment.BottomCenter))
    }
}

/**
 * 보관한 예약: schedules put away, each restorable until the server removes it. Its trash button (or the right-click menu) deletes
 * it for good at once: the row leaves, the message that follows has 실행 취소 for a few seconds, and only then does the server delete it.
 */
@Composable
internal fun ArchivedSchedulesPage(vm: WorkModel, onBack: () -> Unit) {
    val c = Kelo.colors
    val state by vm.archive.collectAsState()
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val deletedWords = stringResource(R.string.schedule_deleted_forever)
    val undoWord = stringResource(R.string.undo)
    LaunchedEffect(Unit) { vm.loadArchive() }
    // the delete waits in the model, not in this page's scope, so leaving the page can't cancel it; closing the page sends it at once
    DisposableEffect(vm) { onDispose { vm.commitArchivedErases() } }
    val erase: (Long) -> Unit = { id ->
        vm.eraseArchivedSoon(id)
        scope.launch {
            snackbar.currentSnackbarData?.dismiss()
            if (snackbar.showSnackbar(deletedWords, undoWord, duration = SnackbarDuration.Short) == SnackbarResult.ActionPerformed) {
                vm.undoEraseArchived(id)
            } else {
                vm.eraseArchived(id)
            }
        }
    }
    Box(Modifier.fillMaxSize()) {
        AgentSubPage(stringResource(R.string.work_archive), onBack) {
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                val data = state.data
                when {
                    data == null && state.failed -> ErrorText(stringResource(R.string.connect_failed), small = true)
                    data == null -> SkeletonRows(2)
                    data.items.isEmpty() -> EmptyNote(stringResource(R.string.work_archive_empty))
                    else -> {
                        MutedText(stringResource(R.string.work_archive_note, data.keepDays), small = true)
                        Panel {
                            data.items.forEachIndexed { i, s ->
                                if (i > 0) HorizontalDivider(color = c.line)
                                key(s.id) {
                                    TrashRow(onTrash = { erase(s.id) }) {
                                        Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                                            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                                Text(s.name, style = MaterialTheme.typography.bodyLarge, color = c.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                                Text(scheduleText(s), style = MaterialTheme.typography.bodySmall, color = c.muted)
                                                s.purgeAt?.let { Text(stringResource(R.string.work_archive_until, relText(it)), style = MaterialTheme.typography.labelSmall, color = c.muted) }
                                            }
                                            SecondaryButton(stringResource(R.string.work_archive_restore), onClick = { vm.restoreSchedule(s.id) })
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        UndoSnackbar(snackbar, Modifier.align(Alignment.BottomCenter))
    }
}

/**
 * A list row with a trash button at its end and the same action on right-click (the phone swipes the row to the left instead).
 */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun TrashRow(onTrash: () -> Unit, content: @Composable () -> Unit) {
    val c = Kelo.colors
    val deleteWord = stringResource(R.string.delete)
    ContextMenuArea(items = { listOf(ContextMenuItem(deleteWord, onTrash)) }) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Box(Modifier.weight(1f)) { content() }
            IconButton(onClick = onTrash, modifier = Modifier.size(36.dp)) {
                Icon(KeloIcons.Trash, contentDescription = deleteWord, tint = c.danger, modifier = Modifier.size(18.dp))
            }
        }
    }
}

/** The page's own message line with 실행 취소. */
@Composable
private fun UndoSnackbar(state: SnackbarHostState, modifier: Modifier) {
    val c = Kelo.colors
    SnackbarHost(state, modifier.padding(16.dp).widthIn(max = 560.dp)) { data ->
        Snackbar(data, shape = RoundedCornerShape(8.dp), containerColor = c.ink, contentColor = c.bg, actionColor = c.bg)
    }
}
