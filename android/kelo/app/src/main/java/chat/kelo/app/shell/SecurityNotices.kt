package chat.kelo.app.shell

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
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.AppVersion
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/**
 * KE2E: the server's key is not the one this phone pinned (409 e2e_key_changed, or the live view's close 4409). Nothing more goes to
 * that server until the phone is paired again with the new QR code from the server machine; this screen covers the app until then.
 */
@Composable
fun PairAgainScreen(onPairAgain: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val activity = androidx.compose.ui.platform.LocalContext.current as? android.app.Activity
    androidx.activity.compose.BackHandler { activity?.moveTaskToBack(true) }
    androidx.compose.material3.Surface(modifier.fillMaxSize(), color = c.bg) {
        Column(
            Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(KeloIcons.Shield, contentDescription = null, tint = c.accent, modifier = Modifier.size(40.dp))
            Text(stringResource(R.string.e2e_key_changed_title), color = c.ink, style = MaterialTheme.typography.titleLarge,
                textAlign = TextAlign.Center, modifier = Modifier.padding(top = 16.dp).semantics { heading() })
            Text(stringResource(R.string.e2e_key_changed_body), color = c.muted, style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp))
            PrimaryButton(stringResource(R.string.e2e_pair_again), onPairAgain, modifier = Modifier.widthIn(max = 420.dp).padding(top = 24.dp))
        }
    }
}

/**
 * The server is older than this app needs (spec 5.18): X-Kelo-Server below the compiled-in min_server, no X-Kelo-Server at all, or no
 * KE2E key (GET /api/e2e/info). This app never talks to such a server in the clear, so this screen covers the app until the server
 * is updated; "Try again" asks once more.
 */
@Composable
fun ServerTooOldScreen(seen: AppVersion.ServerSeen?, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val activity = androidx.compose.ui.platform.LocalContext.current as? android.app.Activity
    androidx.activity.compose.BackHandler { activity?.moveTaskToBack(true) }
    androidx.compose.material3.Surface(modifier.fillMaxSize(), color = c.bg) {
        Column(
            Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.safeDrawing).verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(KeloIcons.Cloud, contentDescription = null, tint = c.accent, modifier = Modifier.size(40.dp))
            Text(stringResource(R.string.e2e_server_old_title), color = c.ink, style = MaterialTheme.typography.titleLarge,
                textAlign = TextAlign.Center, modifier = Modifier.padding(top = 16.dp).semantics { heading() })
            Text(stringResource(R.string.e2e_server_old_body, AppVersion.minServer), color = c.muted, style = MaterialTheme.typography.bodyMedium,
                textAlign = TextAlign.Center, modifier = Modifier.widthIn(max = 420.dp).padding(top = 8.dp))
            AppVersion.versionOf(seen?.version)?.let { v ->
                Text(stringResource(R.string.e2e_server_old_version, v), color = c.muted, style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 8.dp))
            }
            PrimaryButton(stringResource(R.string.e2e_retry), onRetry, modifier = Modifier.widthIn(max = 420.dp).padding(top = 24.dp))
        }
    }
}
