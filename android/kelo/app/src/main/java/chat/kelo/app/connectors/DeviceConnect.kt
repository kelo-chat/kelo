package chat.kelo.app.connectors

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.chat.SelectField
import chat.kelo.app.chat.SelectOption
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.Card
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.emphasized
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Connect a service by typing a short code on its own page (GitHub, Microsoft). The app shows the code,
 * the user types it on the service, and the token goes straight into the vault. When the server offers a web sign-in
 * (GitHub with an OAuth app), "GitHub로 로그인" opens the service's sign-in page in the browser instead, and the panel
 * shows the new state when the user comes back to the app.
 */
@Composable
fun DeviceConnect(id: String, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    val changed by rememberUpdatedState(onChanged)

    var p by remember(id) { mutableStateOf<OAuthProvider?>(null) }
    var flow by remember(id) { mutableStateOf<OAuthFlow?>(null) }
    var deadline by remember { mutableLongStateOf(0L) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var clientId by remember { mutableStateOf("") }
    var editId by remember { mutableStateOf(false) }
    var scopeId by rememberSaveable(id) { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var copied by remember { mutableStateOf(false) }
    var byCode by rememberSaveable(id) { mutableStateOf(false) }   // the sign-in button is offered first; the code is the fallback
    var confirming by remember { mutableStateOf(false) }

    suspend fun load() {
        try {
            val x = ConnectorCalls.oauthProviders(sessions.requireApi()).find { it.id == id }
            // a connection shows the choice it was made with; otherwise the first (the default) until the user picks one
            if (scopeId.isEmpty() || (x?.connected == true && x.grantedScope.isNotEmpty() && p?.grantedScope != x.grantedScope)) {
                scopeId = x?.grantedScope?.takeIf { it.isNotEmpty() } ?: x?.scopes?.firstOrNull()?.id.orEmpty()
            }
            p = x
        } catch (_: ApiError) {
        }
    }
    ReloadOnReturn(id) { load() }

    // follow the attempt with short requests; it goes on at the server even if this screen is left
    val flowId = flow?.id
    val pending = flow?.pending == true
    LaunchedEffect(flowId, pending) {
        if (flowId == null || !pending) return@LaunchedEffect
        var ticks = 0
        while (true) {
            delay(1000)
            now = System.currentTimeMillis()
            if (++ticks % 3 != 0) continue   // the clock ticks every second, the server is asked every third second
            try {
                val f = ConnectorCalls.flow(sessions.requireApi(), flowId)
                flow = f
                if (!f.pending) {
                    load()
                    if (f.status == "connected") changed()
                    break
                }
            } catch (e: ApiError) {
                if (e.status == 404) { flow = null; break }
            }
        }
    }

    val provider = p
    if (provider == null) {
        chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    val name = provider.name

    fun act(block: suspend () -> Unit) {
        err = ""; busy = true
        scope.launch {
            try { block() } catch (e: ApiError) { err = e.detail } finally { busy = false }
        }
    }

    if (confirming) {
        ConfirmDialog(
            stringResource(R.string.device_disconnect_confirm, name),
            stringResource(R.string.connector_disconnect),
            onConfirm = {
                scope.launch {
                    runCatching { ConnectorCalls.disconnect(sessions.requireApi(), provider.item) }
                    flow = null; load(); changed()
                }
            },
            onDismiss = { confirming = false },
        )
    }

    val f = flow
    // connected
    if (provider.connected && (f == null || f.status == "connected")) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            ShieldTitle(stringResource(R.string.connector_connected_name, name))
            MutedText(stringResource(R.string.device_connected_note, provider.account.ifBlank { name }), small = true)
            if (onNotify != null) {
                val text = stringResource(R.string.chat_connect_notify, name)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            LinkButton(stringResource(R.string.connector_disconnect), onClick = { confirming = true }, danger = true)
            if (provider.settingsUrl.isNotBlank()) {
                LinkButton(stringResource(R.string.device_manage, name), onClick = { openInBrowser(context, provider.settingsUrl) })
            }
        }
        return
    }

    // waiting for the code to be typed
    if (f != null && f.pending) {
        val secs = ConnectorRules.secondsLeft(deadline, now)
        val c = Kelo.colors
        val codeLabel = stringResource(R.string.device_code_label)
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            BoldLine(stringResource(R.string.device_enter_code, name))
            SelectionContainer {
                Text(
                    f.userCode,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(14.dp))
                        .background(c.bubble)
                        .padding(horizontal = 8.dp, vertical = 14.dp)
                        .semantics { contentDescription = codeLabel },
                    color = c.ink, textAlign = TextAlign.Center, fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold, fontSize = 28.sp, letterSpacing = 3.sp,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SecondaryButton(
                    stringResource(if (copied) R.string.device_copied else R.string.device_copy),
                    onClick = { clipboard.setText(AnnotatedString(f.userCode)); copied = true },
                    modifier = Modifier.weight(1f),
                )
                PrimaryButton(
                    stringResource(R.string.device_open, name),
                    onClick = { if (!openInBrowser(context, f.verificationUri)) err = context.getString(R.string.connector_browser_failed) },
                    modifier = Modifier.weight(1f),
                )
            }
            MutedText(stringResource(R.string.device_wait_help, name, ConnectorRules.mmss(secs)), small = true)
            LinkButton(stringResource(R.string.cancel), onClick = {
                scope.launch {
                    runCatching { ConnectorCalls.cancelFlow(sessions.requireApi(), f.id) }
                    flow = null
                }
            })
            if (err.isNotEmpty()) ErrorText(err, small = true)
        }
        return
    }

    val ended = ConnectorRules.ended(f)
    val failedText = stringResource(R.string.device_failed)

    @Composable
    fun ScopePicker() {
        if (provider.scopes.isEmpty()) return
        Text(stringResource(R.string.device_scope), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
        SelectField(
            stringResource(R.string.device_scope),
            provider.scopes.map { SelectOption(it.id, it.label) },
            scopeId,
            onSelect = { scopeId = it },
        )
        provider.scopes.find { it.id == scopeId }?.detail?.takeIf { it.isNotBlank() }?.let { MutedText(it, small = true) }
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (ended != null) ErrorText(ended.message.ifBlank { failedText }, small = true)
        if (provider.web && !byCode) {
            ScopePicker()
            PrimaryButton(
                if (busy) stringResource(R.string.connector_opening) else stringResource(R.string.device_sign_in, name),
                enabled = !busy,
                onClick = {
                    act {
                        val url = ConnectorCalls.githubWebStart(sessions.requireApi(), scopeId)
                        if (!openInBrowser(context, url)) err = context.getString(R.string.connector_browser_failed)
                    }
                },
            )
            MutedText(stringResource(R.string.device_sign_in_help, name), small = true)
            LinkButton(stringResource(R.string.device_by_code), onClick = { byCode = true })
        } else if (!provider.hasClientId || editId) {
            BoldLine(stringResource(R.string.device_register, name))
            Steps(provider.help)
            KeloTextField(
                clientId, { clientId = it }, stringResource(R.string.client_id), maxLength = 100,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Ascii),
            )
            PrimaryButton(stringResource(R.string.save), enabled = !busy && clientId.trim().length >= 6, onClick = {
                act {
                    p = ConnectorCalls.saveClientId(sessions.requireApi(), id, clientId.trim())
                    clientId = ""; editId = false
                }
            })
            if (editId) LinkButton(stringResource(R.string.cancel), onClick = { editId = false })
        } else {
            ScopePicker()
            PrimaryButton(
                if (busy) stringResource(R.string.device_preparing) else stringResource(R.string.device_connect, name),
                enabled = !busy,
                onClick = {
                    act {
                        val started = ConnectorCalls.startFlow(sessions.requireApi(), id, scopeId)
                        deadline = System.currentTimeMillis() + started.expiresIn * 1000L
                        now = System.currentTimeMillis()
                        copied = false
                        flow = started
                    }
                },
            )
            LinkButton(stringResource(R.string.device_change_client), onClick = { editId = true })
            if (provider.web) LinkButton(stringResource(R.string.device_sign_in_link, name), onClick = { byCode = false })
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)
        if (provider.id == "github") OperatorSettings("github", onSaved = { scope.launch { load() } })
    }
}

/**
 * For the person who runs Kelo for others, the OAuth app (GitHub or Google) everyone signs in with.
 * The secret can be replaced, never read back.
 */
@Composable
fun OperatorSettings(provider: String, onSaved: () -> Unit = {}, startOpen: Boolean = false) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val github = provider == "github"
    var open by rememberSaveable(provider) { mutableStateOf(startOpen) }
    var app by remember(provider) { mutableStateOf<OAuthApp?>(null) }
    var cid by remember(provider) { mutableStateOf("") }
    var secret by remember(provider) { mutableStateOf("") }
    var msg by remember(provider) { mutableStateOf("") }

    // null: not known yet; false: this app is signed in as a paired phone, which cannot change operator settings (403): nothing shown
    var owner by remember(provider) { mutableStateOf<Boolean?>(null) }

    suspend fun load() {
        val a = ConnectorCalls.oauthApp(sessions.requireApi(), provider)
        app = a; cid = a.clientId
    }
    LaunchedEffect(provider) {
        try {
            load(); owner = true
        } catch (e: ApiError) {
            owner = if (e.status == 403) false else null
        } catch (e: kotlinx.coroutines.CancellationException) {
            throw e
        } catch (_: Exception) {
        }
    }

    if (owner == false) return
    if (!open) {
        if (owner == true) LinkButton(stringResource(R.string.operator_open), onClick = { open = true })
        return
    }
    val a = app
    if (a == null) {
        chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    val secretHint = stringResource(if (github) R.string.operator_secret_github else R.string.operator_secret_google)
    Card(Modifier.padding(top = 6.dp)) {
        BoldLine(stringResource(if (github) R.string.operator_title_github else R.string.operator_title_google))
        MutedText(stringResource(R.string.operator_note), small = true)
        if (a.callback.isNotBlank()) {
            Text(
                emphasized(stringResource(if (github) R.string.operator_callback_github else R.string.operator_callback_google), a.callback, code = true),
                style = MaterialTheme.typography.bodySmall, color = Kelo.colors.ink,
            )
        }
        KeloTextField(
            cid, { cid = it }, stringResource(R.string.client_id), maxLength = 100,
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Ascii),
        )
        KeloTextField(
            secret, { secret = it },
            if (a.hasSecret) stringResource(R.string.operator_secret_saved, secretHint) else secretHint,
            password = true, maxLength = 200,
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
        )
        val savedText = stringResource(R.string.operator_saved)
        PrimaryButton(stringResource(R.string.save), enabled = cid.isNotBlank(), onClick = {
            msg = ""
            scope.launch {
                try {
                    val saved = ConnectorCalls.saveOauthApp(sessions.requireApi(), provider, cid.trim(), secret.trim())
                    secret = ""; app = saved; cid = saved.clientId
                    msg = savedText
                    onSaved()
                } catch (e: ApiError) {
                    msg = e.detail
                }
            }
        })
        MutedText(stringResource(if (a.isBroker) R.string.operator_broker_on else R.string.operator_broker_off), small = true)
        if (!github) GoogleOpenSwitch(a.openToAll, onChanged = { app = it })
        if (msg.isNotEmpty()) SmallText(msg)
    }
}
