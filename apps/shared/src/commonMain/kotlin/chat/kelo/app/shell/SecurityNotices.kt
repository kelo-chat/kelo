package chat.kelo.app.shell

import chat.kelo.app.ui.text.*

import chat.kelo.app.res.*

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import chat.kelo.app.ui.platform.LocalAppBackground
import chat.kelo.app.ui.platform.LocalBackHandler
import chat.kelo.app.ui.platform.formFactor
import chat.kelo.app.core.AppVersion
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import androidx.compose.material3.TextButton
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import chat.kelo.app.my.serverStepText
import kotlinx.coroutines.flow.MutableStateFlow
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/**
 * KE2E: the server's key is not the one this device pinned (409 e2e_key_changed, or the live view's close 4409). Nothing more goes to
 * that server until the device is paired again with a new link or QR code from the server machine; this screen covers the app until then.
 */
@Composable
fun PairAgainScreen(onPairAgain: () -> Unit, modifier: Modifier = Modifier) {
    // the computer's text names the link it is joined with; the phone's names the QR code
    val body = formFactor(phone = Res.string.e2e_key_changed_body, desktop = Res.string.desktop_e2e_key_changed_body)
    CoverScreen(KeloIcons.Shield, string(Res.string.e2e_key_changed_title), string(body), extra = null,
        button = string(Res.string.e2e_pair_again), onClick = onPairAgain, modifier = modifier)
}

/**
 * The server is older than this app needs (spec 5.18): X-Kelo-Server below the compiled-in min_server, no X-Kelo-Server at all, or no
 * KE2E key (GET /api/e2e/info). This app never talks to such a server in the clear, so this screen covers the app until the server
 * is updated. "Update the server" shows when the server can update itself for this device ([updater], docs/version-notices.md part 2:
 * it follows the update and closes the screen by asking the version again); otherwise the short text and a link to the update guide.
 * "Try again" asks once more.
 */
@Composable
fun ServerTooOldScreen(seen: AppVersion.ServerSeen?, onRetry: () -> Unit, modifier: Modifier = Modifier, updater: ServerOldUpdater? = null) {
    val phase by (updater?.phase ?: remember { MutableStateFlow<ServerOldRules.Phase>(ServerOldRules.Phase.Manual) }).collectAsState()
    LaunchedEffect(updater) { updater?.probe() }
    val uri = LocalUriHandler.current
    val running = phase as? ServerOldRules.Phase.Running
    val body = string(Res.string.e2e_server_old_body, AppVersion.minServer)
    val note = when (val p = phase) {
        is ServerOldRules.Phase.Running -> if (p.waiting) string(Res.string.server_update_waiting) else "${serverStepText(p.step)} ${p.percent}%"
        ServerOldRules.Phase.Done -> string(Res.string.server_update_done_plain)
        is ServerOldRules.Phase.Failed -> p.detail ?: string(Res.string.server_old_failed)
        ServerOldRules.Phase.Manual -> string(Res.string.server_old_manual)
        else -> null
    }
    val update = if (phase is ServerOldRules.Phase.Ready || phase is ServerOldRules.Phase.Failed) string(Res.string.server_old_update) else null
    CoverScreen(KeloIcons.Cloud, string(Res.string.e2e_server_old_title), body,
        extra = listOfNotNull(AppVersion.versionOf(seen?.version)?.let { string(Res.string.e2e_server_old_version, it) }, note).joinToString("\n"),
        button = update ?: string(Res.string.e2e_retry), onClick = if (update != null) ({ updater?.update(); Unit }) else onRetry,
        second = if (update != null) string(Res.string.e2e_retry) else null, onSecond = onRetry,
        link = if (phase is ServerOldRules.Phase.Manual || phase is ServerOldRules.Phase.Failed) string(Res.string.server_old_guide) else null,
        onLink = { uri.openUri(ServerOldRules.INSTALL_URL) },
        busy = running != null || phase is ServerOldRules.Phase.Done, modifier = modifier)
}

/** A screen over the whole window that takes every click (the pages under it are never hit); the back gesture leaves the app. */
@Composable
private fun CoverScreen(
    icon: ImageVector, title: String, body: String, extra: String?, button: String, onClick: () -> Unit, modifier: Modifier,
    second: String? = null, onSecond: () -> Unit = {}, link: String? = null, onLink: () -> Unit = {}, busy: Boolean = false,
) {
    val c = Kelo.colors
    val leave = LocalAppBackground.current
    LocalBackHandler.current { leave() }
    Box(
        modifier.fillMaxSize().background(c.bg).windowInsetsPadding(WindowInsets.safeDrawing)
            .pointerInput(Unit) { awaitPointerEventScope { while (true) awaitPointerEvent() } },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(icon, contentDescription = null, tint = c.accent, modifier = Modifier.size(40.dp))
            Text(title, color = c.ink, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 16.dp).semantics { heading() })
            Text(body, color = c.muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp))
            extra?.takeIf { it.isNotEmpty() }?.let {
                Text(it, color = c.muted, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center,
                    modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp).semantics { liveRegion = LiveRegionMode.Polite })
            }
            PrimaryButton(button, onClick, enabled = !busy, modifier = Modifier.widthIn(max = 420.dp).padding(top = 24.dp))
            second?.let { SecondaryButton(it, onSecond, enabled = !busy, modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp)) }
            link?.let { TextButton(onLink, modifier = Modifier.widthIn(max = 420.dp).padding(top = 4.dp)) { Text(it, color = c.accent, textAlign = TextAlign.Center) } }
        }
    }
}
