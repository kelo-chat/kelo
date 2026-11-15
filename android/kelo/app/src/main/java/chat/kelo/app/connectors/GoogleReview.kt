package chat.kelo.app.connectors

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import kotlinx.coroutines.launch
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.theme.Kelo

/** The neutral "준비 중" pill of a Google row whose sign-in waits for Google's review (in place of "연결하기"). */
@Composable
fun ReviewPill(modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Text(
        stringResource(R.string.google_review_pill),
        modifier = modifier.clip(RoundedCornerShape(99.dp)).background(c.neutralSoft).padding(horizontal = 9.dp, vertical = 3.dp),
        color = c.neutralInk, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp), maxLines = 1,
    )
}

/**
 * Why there is no Google sign-in button yet, in Kelo's words (the server's [Connector.note], else the app's own), and the support link
 * once the operator has set one ([Connector.supportUrl]; nothing is shown while it is empty). Below it, the way to ask to be let in
 * early ([BetaRequest]); [onOpened] reloads the screen once that request is approved.
 */
@Composable
fun ReviewNote(c: Connector, onOpened: () -> Unit = {}) {
    val context = LocalContext.current
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        BoldLine(stringResource(R.string.google_review_title))
        MutedText(c.note.ifBlank { stringResource(R.string.google_review_note) }, small = true)
        if (c.supportUrl.startsWith("https://")) {
            LinkButton(stringResource(R.string.google_review_support), onClick = { openInBrowser(context, c.supportUrl) })
        }
        BetaRequest(c, onOpened)
    }
}

/**
 * 운영자 설정 → Google OAuth: "모두에게 Google 로그인 열기". Off (the default) keeps new Google sign-ins closed for everyone while the
 * client waits for Google's verification; accounts already connected keep working either way.
 */
@Composable
internal fun GoogleOpenSwitch(on: Boolean, onChanged: (OAuthApp) -> Unit) {
    val sessions = chat.kelo.app.LocalAppContainer.current.sessions
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    val c = Kelo.colors
    var busy by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    var err by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf("") }
    val title = stringResource(R.string.google_review_open_switch)
    androidx.compose.foundation.layout.Row(
        Modifier.padding(top = 6.dp), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(stringResource(R.string.google_review_open_warning), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        androidx.compose.material3.Switch(
            checked = on, enabled = !busy,
            onCheckedChange = { v ->
                busy = true; err = ""
                scope.launch {
                    try {
                        onChanged(ConnectorCalls.setGoogleOpen(sessions.requireApi(), v))
                    } catch (e: chat.kelo.app.core.ApiError) {
                        err = e.detail
                    } finally {
                        busy = false
                    }
                }
            },
            modifier = Modifier.semantics { contentDescription = title },
            colors = androidx.compose.material3.SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
    if (err.isNotEmpty()) chat.kelo.app.ui.components.ErrorText(err, small = true)
}
