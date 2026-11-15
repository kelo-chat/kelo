package chat.kelo.app.connectors

import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import chat.kelo.app.ui.icons.KeloIcons
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.chat.SelectField
import chat.kelo.app.chat.SelectOption
import chat.kelo.app.chat.vault.SecureCard
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.emphasized
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * The right way to connect this service, whichever it is. Without [connector] it looks the service up by [id]
 * (for a card in the chat). [onNotify], when given, offers "에이전트에게 알리기" once connected.
 */
@Composable
fun ConnectPanel(
    id: String, connector: Connector? = null, onChanged: () -> Unit = {}, onNotify: ((String) -> Unit)? = null,
    onPhoneCalendar: (() -> Unit)? = null, header: Boolean = false,
) {
    val sessions = LocalAppContainer.current.sessions
    var looked by remember(id) { mutableStateOf<Connector?>(null) }
    var tick by remember { mutableIntStateOf(0) }
    if (connector == null) {
        ReloadOnReturn(id to tick) {
            runCatching { ConnectorCalls.list(sessions.requireApi()) }.onSuccess { l -> looked = l.find { it.id == id } }
        }
    }
    val c = connector ?: looked
    val placeholder = chat.kelo.app.ui.components.rememberSkeletonShown(c == null)
    if (c == null || placeholder) {
        chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    val changed: () -> Unit = { onChanged(); if (connector == null) tick++ }
    if (header) {   // the chat's card: which service, with its icon
        androidx.compose.foundation.layout.Row(
            verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            modifier = Modifier.padding(bottom = 8.dp),
        ) {
            ConnectorTile(c, 32.dp)
            Text(c.name.ifBlank { c.id }, style = MaterialTheme.typography.titleMedium, color = Kelo.colors.ink)
        }
    }
    when (c.connect) {
        "device" -> DeviceConnect(c.via.ifBlank { c.id }, changed, onNotify)
        "key" -> KeyConnect(c, changed, onNotify)
        "browser" -> BrowserConnect(c, onNotify)
        "mcp" -> McpConnect(c, changed, onNotify)
        "mail" -> MailConnect(c, changed, onNotify)
        "caldav" -> CalDavConnect(c, changed, onNotify)
        "google" -> GoogleConnect(c, changed, onNotify, onPhoneCalendar)
        else -> MutedText(if (c.status == "unsupported") c.description else stringResource(R.string.connector_not_yet), small = true)
    }
}

/** Sign in on the service's own page (hosted MCP servers), in the browser. The service sends the user back to the server. */
@Composable
private fun McpConnect(c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }
    var other by remember(c.id) { mutableStateOf(false) }
    // back from the browser: the button is ready again (the list reload shows whether it worked)
    ReloadOnReturn(c.id) { busy = false }

    if (confirming) {
        ConfirmDialog(
            stringResource(R.string.mcp_disconnect_confirm, c.name), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(sessions.requireApi(), c.item) }; onChanged() } },
            onDismiss = { confirming = false },
        )
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (c.status == "connected") {
            ShieldTitle(stringResource(R.string.connector_connected_name, c.name))
            MutedText(stringResource(R.string.mcp_connected_note), small = true)
            if (onNotify != null) {
                val text = stringResource(R.string.chat_connect_notify, c.name)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            LinkButton(stringResource(R.string.connector_disconnect), onClick = { confirming = true }, danger = true)
        } else {
            BoldLine(stringResource(R.string.mcp_title, c.name))
            MutedText(stringResource(R.string.mcp_help, c.name), small = true)
            // the service may accept only apps it approved, which shows only after the login: said before the sign-in opens
            if (c.note.isNotBlank()) MutedText(c.note, small = true)
            PrimaryButton(
                if (busy) stringResource(R.string.connector_opening) else stringResource(R.string.mcp_sign_in, c.name),
                enabled = !busy,
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            val url = ConnectorCalls.mcpStart(sessions.requireApi(), c.id)
                            if (!openInBrowser(context, url)) { err = context.getString(R.string.connector_browser_failed); busy = false }
                        } catch (e: ApiError) {
                            err = e.detail; busy = false
                        }
                    }
                },
            )
            if (err.isNotEmpty()) ErrorText(err, small = true)
            val fb = c.fallback
            if (fb != null && fb.connect == "key" && fb.title.isNotBlank()) {
                if (other) {
                    BoldLine(fb.title)
                    KeyConnect(c.copy(connect = "key", via = fb.via, item = fb.item, description = fb.blurb), onChanged, onNotify)
                } else {
                    LinkButton("${fb.title} ›", onClick = { other = true })
                }
            }
        }
    }
}

/** A mailbox with an app password (or Google sign-in for Gmail). The server signs in once to check it; only a mailbox that lets it in is saved. */
@Composable
private fun MailConnect(c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val gmail = c.id == "gmail"
    var data by remember(c.id) { mutableStateOf<MailProviders?>(null) }
    var provider by rememberSaveable(c.id) { mutableStateOf(if (gmail) "gmail" else "naver") }
    var address by remember(c.id) { mutableStateOf("") }
    var password by remember(c.id) { mutableStateOf("") }
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var adding by remember(c.id) { mutableStateOf(false) }
    var byPassword by rememberSaveable(c.id) { mutableStateOf(false) }
    var removing by remember { mutableStateOf<MailAccount?>(null) }

    suspend fun load() {
        runCatching { ConnectorCalls.mailProviders(sessions.requireApi()) }.onSuccess { data = it }
    }
    ReloadOnReturn(c.id) { load(); busy = false }

    if (data == null) {   // not the password form for a moment before the connected mailbox shows
        chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    val providers = data?.providers.orEmpty()
    val shown = ConnectorRules.mailAccounts(c.id, data?.accounts.orEmpty())
    val choices = ConnectorRules.mailChoices(c.id, providers)
    val p = providers.find { it.id == provider }

    removing?.let { a ->
        ConfirmDialog(
            stringResource(R.string.mail_disconnect_confirm, a.label), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(sessions.requireApi(), a.name) }; load(); onChanged() } },
            onDismiss = { removing = null },
        )
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        shown.forEach { a ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                ShieldTitle(a.label)
                MutedText(stringResource(R.string.mail_account_note), small = true)
                LinkButton(stringResource(R.string.connector_disconnect), onClick = { removing = a }, danger = true)
            }
        }
        if (shown.isNotEmpty() && onNotify != null) {
            val text = stringResource(R.string.mail_notify)
            PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
        }
        val form = shown.isEmpty() || adding
        if (form && gmail && ConnectorRules.pendingReview(c) && !byPassword) {
            // the Google sign-in waits for Google's review: the note, and the app password as a small link
            ReviewNote(c, onOpened = { scope.launch { load() }; onChanged() })
            LinkButton(stringResource(R.string.mail_by_password), onClick = { byPassword = true })
        } else if (form && gmail && data?.googleWeb == true && !byPassword) {
            PrimaryButton(
                if (busy) stringResource(R.string.connector_opening) else stringResource(R.string.mail_google_sign_in),
                enabled = !busy,
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            val url = ConnectorCalls.googleWebStart(sessions.requireApi())
                            if (!openInBrowser(context, url)) { err = context.getString(R.string.connector_browser_failed); busy = false }
                        } catch (e: ApiError) {
                            err = e.detail; busy = false
                        }
                    }
                },
            )
            MutedText(stringResource(R.string.mail_google_help), small = true)
            LinkButton(stringResource(R.string.mail_by_password), onClick = { byPassword = true })
        } else if (form) {
            chat.kelo.app.ui.SecureWindow()   // an app password being typed
            if (choices.size > 1) {
                FieldLabel(stringResource(R.string.mail_provider))
                SelectField(stringResource(R.string.mail_provider), choices.map { SelectOption(it.id, it.name) }, provider, onSelect = { provider = it })
            }
            p?.let { Steps(it.help) }
            KeloTextField(
                address, { address = it }, stringResource(R.string.mail_address), maxLength = 200,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email),
            )
            KeloTextField(
                password, { password = it }, stringResource(R.string.mail_password), password = true, maxLength = 200,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
            )
            PrimaryButton(
                stringResource(if (busy) R.string.mail_checking else R.string.check_and_connect),
                enabled = !busy && address.isNotBlank() && password.isNotEmpty(),
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            ConnectorCalls.addMail(sessions.requireApi(), provider, address.trim(), password)
                            password = ""; address = ""; adding = false
                            load(); onChanged()
                        } catch (e: ApiError) {
                            err = e.detail
                        } finally {
                            busy = false
                        }
                    }
                },
            )
            MutedText(stringResource(R.string.mail_check_note), small = true)
            if (adding) LinkButton(stringResource(R.string.cancel), onClick = { adding = false })
        } else if (!gmail) {
            LinkButton(stringResource(R.string.mail_add_other), onClick = { adding = true })
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)
        if (gmail) OperatorSettings("google", onSaved = { scope.launch { load() } })
    }
}

/** A key or token made on the service (or a Zoom app, or a Google service account). The server tries it once before saving it. */
@Composable
internal fun KeyConnect(c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var f by remember(c.via) { mutableStateOf<KeyForm?>(null) }
    var values by remember(c.via) { mutableStateOf(mapOf<String, String>()) }
    var err by remember(c.via) { mutableStateOf("") }
    var busy by remember(c.via) { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }

    suspend fun load() {
        runCatching { ConnectorCalls.keyForm(sessions.requireApi(), c.via) }.onSuccess { f = it }
    }
    ReloadOnReturn(c.via) { load() }

    // the Google service account's JSON file, picked with the system document picker
    val fileFailed = stringResource(R.string.key_file_failed)
    val currentValues by rememberUpdatedState(values)
    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        scope.launch {
            val text = readSmallText(context, uri)
            if (text == null) err = fileFailed else values = currentValues + ("key" to text)
        }
    }

    val form = f
    if (form == null) {
        chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    val google = form.id == "google"

    if (confirming) {
        ConfirmDialog(
            stringResource(R.string.key_disconnect_confirm, form.name), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(sessions.requireApi(), form.item) }; load(); onChanged() } },
            onDismiss = { confirming = false },
        )
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (form.connected) {
            ShieldTitle(stringResource(R.string.connector_connected_name, form.label))
            val account = ConnectorRules.keyAccount(form.label)
            if (google && account.isNotEmpty()) {
                SmallText(stringResource(R.string.key_google_share))
                Text(emphasized("%1\$s", account, code = true), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.ink)
            }
            MutedText(stringResource(R.string.key_connected_note), small = true)
            if (onNotify != null) {
                val text = stringResource(R.string.chat_connect_notify, form.name)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            LinkButton(stringResource(R.string.connector_disconnect), onClick = { confirming = true }, danger = true)
            return@Column
        }
        chat.kelo.app.ui.SecureWindow()   // a key being typed: keep it out of screenshots and the recent-apps preview
        Steps(form.help)
        form.fields.forEach { x ->
            if (google && x.id == "key") {
                SecondaryButton(
                    stringResource(R.string.key_pick_json),
                    onClick = { picker.launch(arrayOf("application/json", "text/*", "application/octet-stream")) },
                )
                KeloTextField(
                    values["key"].orEmpty(), { values = values + ("key" to it) }, stringResource(R.string.key_paste_json),
                    singleLine = false, maxLength = 20_000,
                    keyboardOptions = KeyboardOptions(autoCorrectEnabled = false),
                )
            } else {
                KeloTextField(
                    values[x.id].orEmpty(), { values = values + (x.id to it) }, x.placeholder.ifBlank { x.label },
                    password = x.secret,
                    keyboardOptions = KeyboardOptions(
                        autoCorrectEnabled = false, keyboardType = if (x.secret) KeyboardType.Password else KeyboardType.Ascii,
                    ),
                )
            }
        }
        val ready = ConnectorRules.keyReady(form, values)
        PrimaryButton(
            stringResource(if (busy) R.string.key_checking else if (form.checked) R.string.check_and_connect else R.string.save),
            enabled = !busy && ready,
            onClick = {
                err = ""; busy = true
                scope.launch {
                    try {
                        ConnectorCalls.saveKey(sessions.requireApi(), form.id, values)
                        values = emptyMap()
                        load(); onChanged()
                    } catch (e: ApiError) {
                        err = e.detail
                    } finally {
                        busy = false
                    }
                }
            },
        )
        MutedText(stringResource(if (form.checked) R.string.key_note_checked else R.string.key_note_unchecked), small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
}

/** The text of a picked file, or null when it cannot be read or is too large to be a key file. */
private suspend fun readSmallText(context: Context, uri: Uri): String? = withContext(Dispatchers.IO) {
    runCatching {
        context.contentResolver.openInputStream(uri)?.use { input ->
            val bytes = input.readNBytesCompat(MAX_KEY_FILE + 1)
            if (bytes.size > MAX_KEY_FILE) null else bytes.toString(Charsets.UTF_8)
        }
    }.getOrNull()
}

private const val MAX_KEY_FILE = 64 * 1024

private fun java.io.InputStream.readNBytesCompat(limit: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buf = ByteArray(8192)
    while (out.size() < limit) {
        val n = read(buf, 0, minOf(buf.size, limit - out.size()))
        if (n < 0) break
        out.write(buf, 0, n)
    }
    return out.toByteArray()
}

/** A site the agent uses with its own browser. A login, if the site needs one, is kept in the vault and typed by the server. */
@Composable
private fun BrowserConnect(c: Connector, onNotify: ((String) -> Unit)?) {
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val host = ConnectorRules.host(c.site)
    val savedDefault = stringResource(R.string.browser_saved_default)
    var saved by remember(host) { mutableStateOf<String?>(null) }   // null: loading, "": nothing kept
    var asking by rememberSaveable(host) { mutableStateOf<String?>(null) }
    var err by remember(host) { mutableStateOf("") }

    ReloadOnReturn(host) {
        saved = try {
            val it = VaultCalls.info(sessions.requireApi()).items.find { i -> i.kind == "login" && host in i.hosts }
            if (it != null) it.usernameHint ?: savedDefault else ""
        } catch (_: ApiError) {
            ""
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (c.site.isNotBlank()) {
            LinkButton(stringResource(R.string.browser_site_link, host.ifBlank { c.site }), onClick = {
                if (!openInBrowser(context, c.site)) err = context.getString(R.string.connector_browser_failed)
            })
        }
        val s = saved
        val a = asking
        when {
            !s.isNullOrEmpty() -> ShieldTitleSmall(stringResource(R.string.browser_saved, s))
            a != null -> SecureCard(a, onNotify)
            s == "" -> SecondaryButton(stringResource(R.string.browser_ask), onClick = {
                err = ""
                scope.launch {
                    try {
                        asking = ConnectorCalls.addLogin(sessions.requireApi(), c.site, c.name)
                    } catch (e: ApiError) {
                        err = e.detail
                    }
                }
            })
        }
        MutedText(stringResource(R.string.browser_note), small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
}

@Composable
private fun ShieldTitleSmall(text: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(KeloIcons.Shield, contentDescription = null, tint = Kelo.colors.ink, modifier = Modifier.size(16.dp))
        Spacer(Modifier.size(6.dp))
        SmallText(text)
    }
}

@Composable
private fun FieldLabel(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
}
