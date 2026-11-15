package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.ServerUpdateJob
import chat.kelo.app.my.ServerUpdateRules
import chat.kelo.app.my.ServerUpdateStatus
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeyBox
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SubPage
import chat.kelo.desktop.ui.SwitchRow
import kotlinx.coroutines.launch

/*
 * 마이 → 서버 업데이트 (owner only) and its card at the top of My home, as on the phone
 * (android/kelo .../my/ServerUpdatePage.kt). The rules are the Android file my/ServerUpdate.kt itself (sharedFiles).
 */

/** What the running job is doing, in words. */
@Composable
fun serverStepText(step: ServerUpdateRules.Step): String = stringResource(
    when (step) {
        ServerUpdateRules.Step.PREFLIGHT -> R.string.server_update_step_preflight
        ServerUpdateRules.Step.DOWNLOAD -> R.string.server_update_step_download
        ServerUpdateRules.Step.VERIFY -> R.string.server_update_step_verify
        ServerUpdateRules.Step.BACKUP -> R.string.server_update_step_backup
        ServerUpdateRules.Step.RESTART -> R.string.server_update_step_restart
        ServerUpdateRules.Step.HEALTH -> R.string.server_update_step_health
        ServerUpdateRules.Step.ROLLBACK -> R.string.server_update_step_rollback
        ServerUpdateRules.Step.SELF_UPDATE -> R.string.server_update_step_self_update
        ServerUpdateRules.Step.DONE -> R.string.server_update_step_done
        ServerUpdateRules.Step.WORKING -> R.string.server_update_step_working
    },
)

/** The highlighted card at the top of My home (a newer release, an update running, or one that failed or was undone today). */
@Composable
fun ServerUpdateCard(card: ServerUpdateRules.Card, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val (soft, ink) = when (card) {
        is ServerUpdateRules.Card.Available, is ServerUpdateRules.Card.Running -> c.infoSoft to c.infoInk
        ServerUpdateRules.Card.Failed, ServerUpdateRules.Card.RolledBack -> c.neutralSoft to c.neutralInk
        ServerUpdateRules.Card.RollbackFailed -> c.dangerSoft to c.dangerInk
    }
    val title = when (card) {
        is ServerUpdateRules.Card.Available -> stringResource(R.string.server_update_card_available, card.version)
        is ServerUpdateRules.Card.Running -> stringResource(
            if (card.rollback) R.string.server_update_card_going_back else R.string.server_update_card_running, card.percent,
        )
        ServerUpdateRules.Card.Failed -> stringResource(R.string.server_update_card_failed)
        ServerUpdateRules.Card.RolledBack -> stringResource(R.string.server_update_card_rolled_back)
        ServerUpdateRules.Card.RollbackFailed -> stringResource(R.string.server_update_card_rollback_failed)
    }
    val sub = if (card is ServerUpdateRules.Card.Running) serverStepText(card.step) else stringResource(R.string.server_update_card_open)
    val icon = when (card) {
        is ServerUpdateRules.Card.Available -> KeloIcons.ArrowDown
        is ServerUpdateRules.Card.Running -> KeloIcons.Retry
        else -> KeloIcons.Alert
    }
    val shape = RoundedCornerShape(12.dp)
    Column(
        modifier.fillMaxWidth().clip(shape).background(soft).clickable(role = Role.Button, onClick = onClick)
            .semantics { liveRegion = LiveRegionMode.Polite }
            .padding(start = 16.dp, end = 12.dp, top = 12.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = ink, modifier = Modifier.size(20.dp))
            Column(Modifier.weight(1f).padding(start = 10.dp, end = 4.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, color = ink, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
                Text(sub, color = ink, style = MaterialTheme.typography.bodySmall)
            }
            Icon(KeloIcons.ChevronRight, contentDescription = null, tint = ink, modifier = Modifier.size(20.dp))
        }
        if (card is ServerUpdateRules.Card.Running) {
            LinearProgressIndicator(
                progress = { card.percent / 100f }, color = c.accent, trackColor = c.line,
                modifier = Modifier.fillMaxWidth().padding(start = 30.dp, top = 6.dp, end = 4.dp),
            )
        }
    }
}

/**
 * 서버 업데이트: the version the server runs; a newer release with its date, what's new and [Update]; while an update runs its
 * step and progress; how the last one ended; "go back to <version>"; the nightly automatic update. A server built from source
 * shows only its version and the server's message. [st] and the following ([waiting], [gaveUp]) live in MyPane.
 */
@Composable
fun ServerUpdatePage(
    app: AppContainer, st: ServerUpdateStatus?, waiting: Boolean, gaveUp: Boolean, onStatus: (ServerUpdateStatus?) -> Unit, onBack: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var busy by remember { mutableStateOf(false) }
    var checking by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var confirmUpdate by remember { mutableStateOf(false) }
    var confirmBack by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        if (ServerUpdateRules.running(st)) return@LaunchedEffect   // MyPane is following it already
        runCatching { ServerUpdateRules.load(app.sessions.requireApi()) }.onSuccess(onStatus)
    }

    fun act(block: suspend () -> ServerUpdateStatus?) {
        if (busy) return
        busy = true
        error = null
        scope.launch {
            try {
                onStatus(block())
            } catch (e: ApiError) {
                error = e.detail
            } finally {
                busy = false
            }
        }
    }

    SubPage(stringResource(R.string.server_update_title), onBack) {
        val s = st ?: return@SubPage
        val nowS = System.currentTimeMillis() / 1000.0
        val running = ServerUpdateRules.running(s)
        MyCard {
            LabelLine(stringResource(R.string.server_update_current), s.current?.takeIf { it.isNotBlank() } ?: "—")
            if (s.state == ServerUpdateRules.LOCAL) {
                MutedText(s.message?.takeIf { it.isNotBlank() } ?: stringResource(R.string.server_update_local_fallback), small = true)
                return@MyCard
            }
            when {
                running -> KeyBox(first = false) { Running(s.job, waiting, gaveUp) }
                ServerUpdateRules.canUpdate(s) -> KeyBox(first = false) {
                    val latest = s.latest!!
                    LabelLine(stringResource(R.string.server_update_new_version), latest.version)
                    ServerUpdateRules.releaseSeconds(latest.date)?.let { MutedText(stringResource(R.string.server_update_released, fullDate(it)), small = true) }
                    latest.notes?.trim()?.takeIf { it.isNotEmpty() }?.let { notes ->
                        Text(stringResource(R.string.server_update_whats_new), style = MaterialTheme.typography.titleSmall, color = c.ink,
                            modifier = Modifier.padding(top = 4.dp))
                        Text(notes, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    }
                    Box(Modifier.padding(top = 6.dp)) {
                        PrimaryButton(stringResource(R.string.server_update_button), onClick = { confirmUpdate = true }, enabled = !busy)
                    }
                }
                !s.updateAvailable -> MutedText(stringResource(R.string.server_update_up_to_date), small = true)
            }
            if (!running) {
                KeyBox(first = false) {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        MutedText(
                            when {
                                checking -> stringResource(R.string.server_update_checking)
                                s.checkedAt == null || s.checkedAt <= 0.0 -> stringResource(R.string.server_update_never_checked)
                                else -> stringResource(R.string.server_update_checked, agoText(s.checkedAt))
                            },
                            small = true, modifier = Modifier.weight(1f),
                        )
                        SecondaryButton(stringResource(R.string.server_update_check_now), enabled = !busy && !checking, onClick = {
                            checking = true
                            act { try { ServerUpdateRules.check(app.sessions.requireApi()) } finally { checking = false } }
                        })
                    }
                }
            }
        }

        ServerUpdateRules.result(s, nowS)?.let { ResultCard(it) }

        if (ServerUpdateRules.canRollback(s)) {
            LinkButton(stringResource(R.string.server_update_go_back, s.rollback?.version.orEmpty()), onClick = { confirmBack = true })
        }

        if (ServerUpdateRules.showAuto(s)) {
            MyCard {
                SwitchRow(
                    stringResource(R.string.server_update_auto_title), s.autoUpdate, enabled = !busy,
                    subtitle = stringResource(R.string.server_update_auto_body),
                    onChange = { on -> act { ServerUpdateRules.setAuto(app.sessions.requireApi(), on) } },
                )
                ServerUpdateRules.tonight(s)?.let {
                    Text(stringResource(R.string.server_update_auto_tonight, it), color = c.accent, style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(horizontal = 12.dp))
                }
            }
        }
        error?.let { ErrorText(it, small = true) }
    }

    if (confirmUpdate) {
        AskDialog(
            title = stringResource(R.string.server_update_confirm_title, st?.latest?.version.orEmpty()),
            body = stringResource(R.string.server_update_confirm_body),
            yes = stringResource(R.string.server_update_button), onDismiss = { confirmUpdate = false },
            onYes = { act { ServerUpdateRules.start(app.sessions.requireApi(), rollback = false) } },
        )
    }
    if (confirmBack) {
        AskDialog(
            title = stringResource(R.string.server_update_go_back_title, st?.rollback?.version.orEmpty()),
            body = stringResource(R.string.server_update_go_back_body),
            yes = stringResource(R.string.server_update_go_back_yes), onDismiss = { confirmBack = false }, danger = true,
            onYes = { act { ServerUpdateRules.start(app.sessions.requireApi(), rollback = true) } },
        )
    }
}

@Composable
private fun LabelLine(label: String, value: String) {
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, color = c.muted)
        Text(value, style = MaterialTheme.typography.titleMedium, color = c.ink, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun Running(job: ServerUpdateJob?, waiting: Boolean, gaveUp: Boolean) {
    val c = Kelo.colors
    val pct = ServerUpdateRules.percent(job)
    Column(Modifier.semantics { liveRegion = LiveRegionMode.Polite }, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val versions = listOfNotNull(job?.from?.takeIf { it.isNotBlank() }, job?.to?.takeIf { it.isNotBlank() })
        if (versions.size == 2) Text("${versions[0]} → ${versions[1]}", style = MaterialTheme.typography.bodySmall, color = c.muted)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(serverStepText(ServerUpdateRules.step(job)), style = MaterialTheme.typography.bodyLarge, color = c.ink, modifier = Modifier.weight(1f))
            Text("$pct%", style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        LinearProgressIndicator(progress = { pct / 100f }, color = c.accent, trackColor = c.line, modifier = Modifier.fillMaxWidth())
        MutedText(stringResource(R.string.server_update_running_note), small = true)
        when {
            gaveUp -> ErrorText(stringResource(R.string.server_update_gave_up), small = true)
            waiting -> Text(stringResource(R.string.server_update_waiting), style = MaterialTheme.typography.bodySmall, color = c.accent)
        }
    }
}

@Composable
private fun ResultCard(r: ServerUpdateRules.Result) {
    val c = Kelo.colors
    val (soft, ink) = when (r) {
        is ServerUpdateRules.Result.Updated, is ServerUpdateRules.Result.WentBack -> c.successSoft to c.successInk
        is ServerUpdateRules.Result.Failed, is ServerUpdateRules.Result.RolledBack -> c.neutralSoft to c.neutralInk
        is ServerUpdateRules.Result.RollbackFailed -> c.dangerSoft to c.dangerInk
    }
    val title = when (r) {
        is ServerUpdateRules.Result.Updated ->
            r.version?.let { stringResource(R.string.server_update_done, it) } ?: stringResource(R.string.server_update_done_plain)
        is ServerUpdateRules.Result.WentBack ->
            r.version?.let { stringResource(R.string.server_update_went_back, it) } ?: stringResource(R.string.server_update_done_plain)
        is ServerUpdateRules.Result.Failed -> stringResource(if (r.goBack) R.string.server_update_go_back_failed else R.string.server_update_failed)
        is ServerUpdateRules.Result.RolledBack ->
            r.version?.let { stringResource(R.string.server_update_rolled_back, it) } ?: stringResource(R.string.server_update_rolled_back_plain)
        is ServerUpdateRules.Result.RollbackFailed -> stringResource(R.string.server_update_rollback_failed)
    }
    val data = when (r) {
        is ServerUpdateRules.Result.RolledBack -> stringResource(if (r.dataRestored) R.string.server_update_data_restored else R.string.server_update_data_kept)
        is ServerUpdateRules.Result.WentBack -> if (r.dataRestored) stringResource(R.string.server_update_data_restored) else null
        else -> null
    }
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(soft).semantics { liveRegion = LiveRegionMode.Polite }.padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (r.auto) Text(stringResource(R.string.server_update_auto_tag), style = MaterialTheme.typography.labelMedium, color = ink)
        Text(title, style = MaterialTheme.typography.bodyLarge, color = ink, fontWeight = FontWeight.SemiBold)
        data?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = ink) }
        r.error?.let { Text(stringResource(R.string.server_update_error_detail, it), style = MaterialTheme.typography.bodySmall, color = ink.copy(alpha = 0.8f)) }
    }
}

@Composable
private fun AskDialog(title: String, body: String, yes: String, onYes: () -> Unit, onDismiss: () -> Unit, danger: Boolean = false) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.card,
        title = { Text(title, color = c.ink, style = MaterialTheme.typography.titleMedium) },
        text = { Text(body, color = c.muted, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = { TextButton(onClick = { onDismiss(); onYes() }) { Text(yes, color = if (danger) c.danger else c.accent) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}
