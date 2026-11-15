package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.CleanupGroup
import chat.kelo.app.my.CleanupItem
import chat.kelo.app.my.CleanupList
import chat.kelo.app.my.CleanupResult
import chat.kelo.app.my.Resources
import chat.kelo.app.my.StorageRules
import chat.kelo.app.my.StorageState
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeyBox
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

/* 마이 → Kelo 서버 자원 → 정리하기 and the resources page's storage warning (android/kelo .../my/CleanupPage.kt, ResourcesPage.kt). */

@Composable
private fun groupTitle(g: CleanupGroup): String = when (g.id) {
    "uploads" -> stringResource(R.string.storage_group_uploads)
    "images" -> stringResource(R.string.storage_group_images)
    "browser_history" -> stringResource(R.string.storage_group_browser)
    "results" -> stringResource(R.string.storage_group_results)
    "backups" -> stringResource(R.string.storage_group_backups)
    else -> g.name.ifBlank { g.id }
}

@Composable
private fun groupNote(g: CleanupGroup, days: Int): String? = when (g.id) {
    "uploads" -> stringResource(R.string.storage_group_uploads_desc, days)
    "images" -> stringResource(R.string.storage_group_images_desc)
    "browser_history" -> stringResource(R.string.storage_group_browser_desc)
    "results" -> stringResource(R.string.storage_group_results_desc, days)
    "backups" -> stringResource(R.string.storage_group_backups_desc)
    else -> null
}

@Composable
private fun itemNote(i: CleanupItem): String? = when {
    i.keepsText -> stringResource(R.string.storage_item_text_kept)
    i.trashed -> stringResource(R.string.storage_item_trashed)
    i.kind == "update" -> stringResource(R.string.storage_backup_update)
    i.kind == "memory" -> stringResource(R.string.storage_backup_memory)
    i.kind == "migration" -> stringResource(R.string.storage_backup_migration)
    else -> null
}

/**
 * What can safely be removed, grouped with sizes and dates. Nothing is chosen at first; the user ticks items (or a whole group),
 * presses 지우기 and confirms. Only then does the server delete, and only items on its own current list.
 */
@Composable
fun CleanupPage(app: AppContainer, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var list by remember { mutableStateOf<CleanupList?>(null) }
    var failed by remember { mutableStateOf<Int?>(null) }
    var attempt by remember { mutableIntStateOf(0) }
    var selected by remember { mutableStateOf(setOf<String>()) }
    var busy by remember { mutableStateOf(false) }
    var confirm by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<CleanupResult?>(null) }
    var error by remember { mutableStateOf<Int?>(null) }

    LaunchedEffect(attempt) {
        failed = null
        try {
            val l = StorageRules.list(app.sessions.requireApi())
            list = l
            selected = StorageRules.keep(selected, l)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            failed = if (e is ApiError && e.status == 403) R.string.storage_owner_only else R.string.storage_load_failed
        }
    }

    fun delete() {
        val ids = selected.toList()
        busy = true
        error = null
        scope.launch {
            try {
                val r = StorageRules.remove(app.sessions.requireApi(), ids)
                result = r
                val fresh = CleanupList(r.groups, r.total, list?.olderThanDays ?: 14)
                list = fresh
                selected = StorageRules.keep(selected - r.removed.toSet(), fresh)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                error = R.string.storage_delete_failed
            } finally {
                busy = false
            }
        }
    }

    SubPage(stringResource(R.string.storage_cleanup_title), onBack) {
        val l = list
        if (l == null) {
            val why = failed
            if (why != null) {
                ErrorText(stringResource(why))
                if (why != R.string.storage_owner_only) SecondaryButton(stringResource(R.string.retry), onClick = { attempt++ })
            } else {
                MutedText(stringResource(R.string.loading), small = true)
            }
            return@SubPage
        }
        result?.let { r ->
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.successSoft).padding(14.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(stringResource(R.string.storage_deleted, storageSize(r.freed)), color = c.successInk, style = MaterialTheme.typography.bodyMedium)
                if (r.skipped.isNotEmpty()) Text(stringResource(R.string.storage_skipped), color = c.successInk, style = MaterialTheme.typography.bodySmall)
            }
        }
        MutedText(stringResource(R.string.storage_cleanup_intro), small = true)
        val groups = StorageRules.shown(l)
        if (groups.isEmpty()) {
            MyCard { MutedText(stringResource(R.string.storage_nothing)) }
            return@SubPage
        }
        groups.forEach { g ->
            MyCard {
                CheckRow(StorageRules.groupChecked(selected, g), !busy, { selected = StorageRules.toggleGroup(selected, g) }) {
                    Text(groupTitle(g), style = MaterialTheme.typography.titleSmall, color = c.ink, fontWeight = FontWeight.SemiBold)
                    Text(storageSize(g.bytes), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                groupNote(g, l.olderThanDays)?.let { MutedText(it, small = true) }
                g.items.forEach { i ->
                    KeyBox(first = false) {
                        CheckRow(i.id in selected, !busy, { selected = StorageRules.toggle(selected, i.id) }) {
                            val note = itemNote(i)   // a backup with no name shows its kind instead
                            Text(TokenText.fit(i.name.ifBlank { note ?: i.id }), style = MaterialTheme.typography.bodyMedium, color = c.ink, maxLines = 1,
                                overflow = TextOverflow.Ellipsis)
                            val line = stringResource(R.string.storage_item_line, storageSize(i.bytes), fullDate(i.date))
                            Text(if (note != null && i.name.isNotBlank()) stringResource(R.string.storage_item_line, line, note) else line,
                                style = MaterialTheme.typography.bodySmall, color = c.muted)
                        }
                    }
                }
            }
        }
        MutedText(stringResource(R.string.storage_selected, selected.size, storageSize(StorageRules.selectedBytes(l, selected))), small = true)
        error?.let { ErrorText(stringResource(it), small = true) }
        PrimaryButton(stringResource(R.string.storage_delete_button), onClick = { confirm = true }, enabled = selected.isNotEmpty() && !busy)
    }

    if (confirm) {
        ConfirmDialog(
            title = stringResource(R.string.storage_confirm_title),
            text = stringResource(R.string.storage_confirm_body, storageSize(StorageRules.selectedBytes(list, selected))),
            yes = stringResource(R.string.storage_delete_button), onYes = { delete() }, onDismiss = { confirm = false },
        )
    }
}

@Composable
private fun CheckRow(checked: Boolean, enabled: Boolean, onToggle: () -> Unit, content: @Composable () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().heightIn(min = 40.dp).clip(RoundedCornerShape(8.dp))
            .toggleable(value = checked, enabled = enabled, role = Role.Checkbox) { onToggle() },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Checkbox(checked = checked, onCheckedChange = null, enabled = enabled,
            colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent, uncheckedColor = c.muted))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) { content() }
    }
}

/** The warning box on the resources page: red for 95% and a nearly full server disk, quieter for 80%; 정리하기 for the owner. */
@Composable
fun StorageWarning(state: StorageState, res: Resources, partName: @Composable (String) -> String, onCleanup: (() -> Unit)?) {
    val c = Kelo.colors
    val urgent = StorageRules.urgent(state)
    val percent = res.alert.percent ?: 0
    val text = when (state) {
        StorageState.HOST_LOW -> stringResource(R.string.storage_host_low, storageSize(res.host.diskFree ?: 0))
        StorageState.CRITICAL -> stringResource(R.string.storage_critical, percent)
        else -> stringResource(R.string.storage_warn, percent)
    }
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(if (urgent) c.dangerSoft else c.neutralSoft).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text, color = if (urgent) c.dangerInk else c.warn, style = MaterialTheme.typography.bodyMedium)
        val top = res.alert.top
        val size = top?.let { res.disk.parts[it] }
        if (top != null && size != null) {
            Text(stringResource(R.string.storage_top, partName(top), storageSize(size)), color = if (urgent) c.dangerInk else c.neutralInk,
                style = MaterialTheme.typography.bodySmall)
        }
        if (onCleanup != null) PrimaryButton(stringResource(R.string.storage_clean_up), onClick = onCleanup)
    }
}
