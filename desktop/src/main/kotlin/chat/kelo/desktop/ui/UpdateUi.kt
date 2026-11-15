package chat.kelo.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.UpdateRules
import chat.kelo.desktop.core.UpdateState
import chat.kelo.desktop.core.UpdateState.Phase
import chat.kelo.desktop.i18n.stringResource
import kotlinx.coroutines.launch

/**
 * "새 버전 1.0.3가 있어요 · 75.3 MB": a small pill over the page, once per version (closing it, or 받기, puts it away; My keeps the
 * card). 받기 starts the download and opens My, where the progress shows.
 */
@Composable
fun UpdateBanner(app: AppContainer, onOpenMy: () -> Unit, modifier: Modifier = Modifier) {
    val s by app.updates.state.collectAsState()
    val seen by app.settings.dismissedUpdate.collectAsState()
    val b = s.build ?: return
    if (s.phase != Phase.AVAILABLE || b.version == seen) return
    val c = Kelo.colors
    val shape = RoundedCornerShape(50)
    Row(
        modifier.padding(horizontal = 16.dp, vertical = 12.dp).widthIn(max = 560.dp).shadow(3.dp, shape, clip = false).clip(shape)
            .background(c.card).border(1.dp, c.line, shape).padding(start = 16.dp, end = 4.dp, top = 2.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            stringResource(R.string.update_available, b.version, UpdateRules.humanSize(b.size)), color = c.ink,
            style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f, fill = false),
        )
        LinkButton(stringResource(R.string.update_get), onClick = {
            app.settings.setDismissedUpdate(b.version)
            app.scope.launch { app.updates.download() }
            onOpenMy()
        }, modifier = Modifier.padding(start = 8.dp))
        IconButton(onClick = { app.settings.setDismissedUpdate(b.version) }) {
            Icon(KeloIcons.Close, contentDescription = stringResource(R.string.close), tint = c.muted, modifier = Modifier.size(16.dp))
        }
    }
}

/** My's card while a newer build is known: the offer, the download's progress, the check, and what went wrong. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun UpdateCard(app: AppContainer, modifier: Modifier = Modifier) {
    val s by app.updates.state.collectAsState()
    val b = s.build ?: return
    if (!s.newer) return
    val c = Kelo.colors
    val get = { app.scope.launch { app.updates.download() }; Unit }
    Column(
        modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 8.dp).clip(RoundedCornerShape(12.dp)).background(c.card)
            .border(1.dp, c.line, RoundedCornerShape(12.dp)).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(stringResource(R.string.update_available, b.version, UpdateRules.humanSize(b.size)), color = c.ink, style = MaterialTheme.typography.bodyLarge)
        when (s.phase) {
            Phase.DOWNLOADING -> {
                LinearProgressIndicator(progress = { s.progress }, modifier = Modifier.fillMaxWidth(), color = c.accent, trackColor = c.bubble)
                MutedText(stringResource(R.string.update_downloading, (s.progress * 100).toInt()), small = true)
            }
            Phase.VERIFYING -> {
                LinearProgressIndicator(modifier = Modifier.fillMaxWidth(), color = c.accent, trackColor = c.bubble)
                MutedText(stringResource(R.string.update_verifying), small = true)
            }
            Phase.MISMATCH, Phase.DOWNLOAD_FAILED -> {
                ErrorText(stringResource(if (s.phase == Phase.MISMATCH) R.string.update_mismatch else R.string.update_failed), small = true)
                Box { SecondaryButton(stringResource(R.string.update_retry), onClick = get) }
            }
            Phase.OPENED -> {
                MutedText(stringResource(R.string.desktop_update_quit_note), small = true)
                Box { SecondaryButton(stringResource(R.string.update_install), onClick = { app.scope.launch { app.updates.reopen() } }) }
            }
            Phase.OPEN_FAILED -> ErrorText(stringResource(R.string.desktop_update_open_failed, s.path.orEmpty()), small = true)
            else -> FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) { PrimaryButton(stringResource(R.string.update_get), onClick = get) }
        }
    }
}

/** The value under My's "업데이트 확인" row. */
@Composable
fun updateRowValue(s: UpdateState): String? = when (s.phase) {
    Phase.CHECKING -> stringResource(R.string.update_checking)
    Phase.LATEST -> stringResource(R.string.update_latest)
    Phase.CHECK_FAILED -> stringResource(R.string.update_check_failed)
    Phase.IDLE -> null
    else -> s.build?.let { stringResource(R.string.update_new_value, it.version) }
}

/**
 * The server says this version is too old (`min_app_version.desktop`): this covers the whole window until Kelo is updated (the phone's
 * UpdateRequiredScreen). 업데이트 starts this app's own download at once (checked, then the installer opens); while it runs, and when
 * it is done or went wrong, the same card as My shows here. The Microsoft Store copy opens the Store's updates page instead.
 */
@Composable
fun UpdateRequiredScreen(app: AppContainer, modifier: Modifier = Modifier) {
    val s by app.updates.state.collectAsState()
    val c = Kelo.colors
    Box(
        modifier.fillMaxSize().background(c.bg)
            // nothing underneath can be clicked or scrolled while this shows: this layer takes every pointer event (the pages under
            // it are never hit); nothing is consumed, so the button on it still gets its click
            .pointerInput(Unit) { awaitPointerEventScope { while (true) awaitPointerEvent() } },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(24.dp).widthIn(max = 460.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(KeloIcons.ArrowDown, contentDescription = null, tint = c.accent, modifier = Modifier.size(40.dp))
            Text(stringResource(R.string.update_required_title), color = c.ink, style = MaterialTheme.typography.titleLarge,
                textAlign = TextAlign.Center, modifier = Modifier.padding(top = 16.dp).semantics { heading() })
            Text(stringResource(R.string.update_required_body), color = c.muted, style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp))
            app.updates.currentVersion?.let {
                Text(stringResource(R.string.my_version, it), color = c.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp))
            }
            // a download under way (or done, failed) shows the same card as My; otherwise one plain button
            if (s.phase in setOf(Phase.DOWNLOADING, Phase.VERIFYING, Phase.MISMATCH, Phase.DOWNLOAD_FAILED, Phase.OPENED, Phase.OPEN_FAILED)) {
                UpdateCard(app, Modifier.widthIn(max = 420.dp).padding(top = 16.dp))
            } else {
                val checking = s.phase == Phase.CHECKING
                PrimaryButton(
                    stringResource(if (checking) R.string.update_checking else R.string.update_now),
                    onClick = { app.scope.launch { app.updates.updateNow() } }, enabled = !checking,
                    modifier = Modifier.widthIn(max = 420.dp).fillMaxWidth().padding(top = 24.dp),
                )
                if (s.phase == Phase.CHECK_FAILED) ErrorText(stringResource(R.string.update_check_failed), Modifier.padding(top = 8.dp), small = true)
            }
        }
    }
}
