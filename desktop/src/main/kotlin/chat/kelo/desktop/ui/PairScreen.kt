package chat.kelo.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.PairingClient
import chat.kelo.app.core.PairingLink
import chat.kelo.app.core.PairingLinks
import chat.kelo.app.core.Profile
import chat.kelo.app.core.ServerAddress
import chat.kelo.app.core.Session
import chat.kelo.desktop.core.KeySignIn
import chat.kelo.app.ui.character.KeloCharacter
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.Platform
import chat.kelo.desktop.i18n.stringResource
import kotlinx.coroutines.launch

/** What the connect screen can show after a try. */
private sealed interface PairProblem {
    data object NotALink : PairProblem
    data object HttpsOnly : PairProblem
    data object BadAddress : PairProblem
    data object WrongKey : PairProblem
    /** The server offers no KE2E (or is older than min_server): it must be updated before this app can join it (spec 5.18) */
    data object ServerOld : PairProblem
    data class Failed(val detail: String) : PairProblem
}

/**
 * Connecting this desktop to a Kelo server: the pairing link from a signed-in device (마이 → 내 기기 → 새 기기 연결), or from
 * `assistant phone add` on the computer the server runs on, pasted here. Or the server address and its access key. Before
 * anything is sent the app asks once whether this is the user's own server (the link may have come from anyone).
 */
@Composable
fun PairScreen(app: AppContainer, onSignedIn: (Profile?) -> Unit, initialLink: String = "") {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var link by remember { mutableStateOf(initialLink) }
    var withKey by remember { mutableStateOf(false) }
    var server by remember { mutableStateOf(app.sessions.lastServerUrl().orEmpty()) }
    var key by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<PairProblem?>(null) }
    var confirm by remember { mutableStateOf<(() -> Unit)?>(null) }
    val client = remember { app.pairingClient() }

    fun pair(l: PairingLink) {
        busy = true; problem = null
        scope.launch {
            try {
                val session = client.pair(l, Platform.deviceName() + " · Kelo")
                app.sessions.signIn(session)
                val profile = runCatching { app.sessions.requireApi().get<Profile>("/api/profile") }.getOrNull()
                profile?.let(app::setProfile)
                onSignedIn(profile)
            } catch (e: ApiError) {
                problem = if (e.code == PairingClient.CODE_SERVER_OLD) PairProblem.ServerOld else PairProblem.Failed(if (e.isNetwork) "" else e.detail)
            } finally {
                busy = false
            }
        }
    }

    fun submitLink() {
        val l = PairingLinks.parse(link)
        problem = when {
            l == null -> PairProblem.NotALink
            !ServerAddress.isAllowed(l.serverUrl) -> PairProblem.HttpsOnly
            else -> null
        }
        if (l != null && problem == null) confirm = { pair(l) }
    }

    fun submitKey() {
        val url = ServerAddress.normalize(server)
        problem = when {
            url == null -> PairProblem.BadAddress
            !ServerAddress.isAllowed(url) -> PairProblem.HttpsOnly
            else -> null
        }
        if (url == null || problem != null) return
        confirm = {
            busy = true
            scope.launch {
                try {
                    // an access key, or a sign-in code typed in its place (core/KeySignIn: tried through POST /api/pair on a 401)
                    val (session, checked) = KeySignIn.connect(client, url, key, Platform.deviceName() + " · Kelo")
                    app.sessions.signIn(session)   // with the key it pinned (KE2E)
                    val profile = checked ?: runCatching { app.sessions.requireApi().get<Profile>("/api/profile") }.getOrNull()
                    profile?.let(app::setProfile)
                    onSignedIn(profile)
                } catch (e: ApiError) {
                    problem = when {
                        e.isUnauthorized -> PairProblem.WrongKey
                        e.code == PairingClient.CODE_SERVER_OLD -> PairProblem.ServerOld
                        else -> PairProblem.Failed(if (e.isNetwork) "" else e.detail)
                    }
                } finally {
                    busy = false
                }
            }
        }
    }

    Box(Modifier.fillMaxSize().background(c.bg), contentAlignment = Alignment.TopCenter) {
        Column(
            Modifier.widthIn(max = 520.dp).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 28.dp, vertical = 40.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Box(Modifier.size(104.dp).clip(CircleShape).background(Color(0xFFFFF3D6)), contentAlignment = Alignment.Center) {
                KeloCharacter(KeloMood.Idle, Modifier.size(92.dp))
            }
            PageTitle(stringResource(R.string.connect_title))
            Text(stringResource(R.string.connect_lead), style = MaterialTheme.typography.bodyLarge, color = c.ink)
            if (!withKey) {
                Text(stringResource(R.string.step_number, 1) + " " + stringResource(R.string.connect_step1, stringResource(R.string.connect_step1_path)),
                    style = MaterialTheme.typography.bodyMedium, color = c.ink)
                Text(stringResource(R.string.step_number, 2) + " " + stringResource(R.string.desktop_pair_step2), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                KeloTextField(
                    link, { link = it; problem = null }, stringResource(R.string.connect_link_placeholder),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go), keyboardActions = KeyboardActions(onGo = { submitLink() }),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    SecondaryButton(stringResource(R.string.connect_link_paste), onClick = {
                        clipboard.getText()?.text?.let { link = it.trim(); problem = null }
                    })
                    PrimaryButton(stringResource(if (busy) R.string.connect_pairing else R.string.connect_submit), onClick = ::submitLink,
                        enabled = !busy && link.isNotBlank())
                }
                MutedText(stringResource(R.string.connect_first_device, stringResource(R.string.connect_first_device_cmd)), small = true)
                LinkButton(stringResource(R.string.connect_with_key), onClick = { withKey = true; problem = null })
            } else {
                KeloTextField(server, { server = it; problem = null }, stringResource(R.string.connect_server_placeholder),
                    label = stringResource(R.string.connect_server_label))
                KeloTextField(key, { key = it; problem = null }, stringResource(R.string.connect_key_placeholder), password = true,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go), keyboardActions = KeyboardActions(onGo = { submitKey() }))
                MutedText(stringResource(R.string.connect_key_hint), small = true)
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                    SecondaryButton(stringResource(R.string.back), onClick = { withKey = false; problem = null })
                    PrimaryButton(stringResource(if (busy) R.string.connect_checking else R.string.connect_submit), onClick = ::submitKey,
                        enabled = !busy && server.isNotBlank() && key.isNotBlank())
                }
            }
            problem?.let { p ->
                ErrorText(
                    when (p) {
                        PairProblem.NotALink -> stringResource(R.string.connect_link_invalid)
                        PairProblem.HttpsOnly -> stringResource(R.string.connect_https_only)
                        PairProblem.BadAddress -> stringResource(R.string.connect_server_invalid)
                        PairProblem.WrongKey -> stringResource(R.string.connect_key_wrong)
                        PairProblem.ServerOld -> stringResource(R.string.e2e_server_old_body, chat.kelo.app.core.AppVersion.minServer)
                        is PairProblem.Failed -> listOf(stringResource(R.string.connect_failed), p.detail).filter { it.isNotBlank() }.joinToString(" ")
                    },
                )
            }
        }
    }

    confirm?.let { go ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(stringResource(R.string.connect_confirm_title)) },
            text = { Text(stringResource(R.string.connect_confirm_body)) },
            confirmButton = { TextButton(onClick = { confirm = null; go() }) { Text(stringResource(R.string.connect_confirm_yes), color = c.accent) } },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
            containerColor = c.card,
        )
    }
}
