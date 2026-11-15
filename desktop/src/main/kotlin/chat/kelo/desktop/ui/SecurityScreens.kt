package chat.kelo.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.draw.clip
import chat.kelo.desktop.core.AppContainer
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.AppVersion
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.stringResource

/**
 * KE2E: the server's key is not the one this device pinned (409 e2e_key_changed). Nothing more goes to that server until this
 * device is connected again with a new link; this covers the window until then (the phone's PairAgainScreen).
 */
@Composable
fun PairAgainScreen(onPairAgain: () -> Unit, modifier: Modifier = Modifier) {
    CoverScreen(KeloIcons.Shield, stringResource(R.string.e2e_key_changed_title), stringResource(R.string.desktop_e2e_key_changed_body),
        extra = null, button = stringResource(R.string.e2e_pair_again), onClick = onPairAgain, modifier = modifier)
}

/**
 * The server is older than this app needs (spec 5.18): X-Kelo-Server below the compiled-in min_server, no X-Kelo-Server at all, or no
 * KE2E key (GET /api/e2e/info). This app never talks to such a server in the clear; "Try again" asks once more.
 */
@Composable
fun ServerTooOldScreen(seen: AppVersion.ServerSeen?, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    CoverScreen(KeloIcons.Cloud, stringResource(R.string.e2e_server_old_title),
        stringResource(R.string.e2e_server_old_body, AppVersion.minServer),
        extra = AppVersion.versionOf(seen?.version)?.let { stringResource(R.string.e2e_server_old_version, it) },
        button = stringResource(R.string.e2e_retry), onClick = onRetry, modifier = modifier)
}

/** A screen over the whole window that takes every click (the pages under it are never hit), like UpdateRequiredScreen. */
@Composable
private fun CoverScreen(icon: ImageVector, title: String, body: String, extra: String?, button: String, onClick: () -> Unit, modifier: Modifier) {
    val c = Kelo.colors
    Box(
        modifier.fillMaxSize().background(c.bg).pointerInput(Unit) { awaitPointerEventScope { while (true) awaitPointerEvent() } },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(24.dp).widthIn(max = 460.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(icon, contentDescription = null, tint = c.accent, modifier = Modifier.size(40.dp))
            Text(title, color = c.ink, style = MaterialTheme.typography.titleLarge, textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = 16.dp).semantics { heading() })
            Text(body, color = c.muted, style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp))
            extra?.let { Text(it, color = c.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 8.dp)) }
            PrimaryButton(button, onClick = onClick, modifier = Modifier.widthIn(max = 420.dp).fillMaxWidth().padding(top = 24.dp))
        }
    }
}

/**
 * Keyguard: the server holds no key for the owner's passwords, keys and cards (423 {"code": "locked"}, or the agent status says
 * locked). The desktop holds no copy of the key (spec 2.4), so this only says why things wait and where to unlock: on an owner phone.
 * It stays until the server says it is open again.
 */
@Composable
fun LockedBanner(app: AppContainer, modifier: Modifier = Modifier) {
    val locked by app.sessions.locked.collectAsState()
    if (!locked) return
    val c = Kelo.colors
    val shape = RoundedCornerShape(14.dp)
    Row(
        modifier.widthIn(max = 560.dp).clip(shape).background(c.card).border(1.dp, c.danger.copy(alpha = 0.5f), shape).padding(14.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Icon(KeloIcons.Lock, contentDescription = null, tint = c.danger, modifier = Modifier.size(20.dp))
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(stringResource(R.string.keyguard_locked_title), color = c.ink, style = MaterialTheme.typography.titleSmall)
            Text(stringResource(R.string.desktop_keyguard_locked_body), color = c.muted, style = MaterialTheme.typography.bodySmall)
        }
    }
}
