package chat.kelo.desktop.pages.connectors

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.Icon
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.chat.vault.VaultItem
import chat.kelo.app.chat.vault.VaultMode
import chat.kelo.app.connectors.Connector
import chat.kelo.app.connectors.ConnectorCalls
import chat.kelo.app.connectors.ConnectorRules
import chat.kelo.app.connectors.GoogleCalendarAccount
import chat.kelo.app.connectors.GoogleCalendarState
import chat.kelo.app.connectors.KeyForm
import chat.kelo.app.connectors.MailAccount
import chat.kelo.app.connectors.MailProviders
import chat.kelo.app.connectors.OAuthFlow
import chat.kelo.app.connectors.OAuthProvider
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.SelectField
import chat.kelo.desktop.chat.SelectOption
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.Browser
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.MailRules
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/*
 * The right way to connect a service, whichever it is (android/kelo .../connectors/ConnectPanel.kt, DeviceConnect.kt,
 * GoogleConnect.kt): a code typed on the service's page, a sign-in on its own page, a mailbox (Google sign-in or app password), a
 * key, a site login kept in the vault, Google Calendar with its accounts. Pages opened in the browser are followed by reloading
 * whenever a Kelo window becomes active again.
 */

/** The connect panel for [c]. [onNotify], when given, offers "에이전트에게 알리기" once connected. */
@Composable
fun ConnectPanel(app: AppContainer, c: Connector, onChanged: () -> Unit = {}, onNotify: ((String) -> Unit)? = null) {
    when (c.connect) {
        "device" -> DeviceConnect(app, c.via.ifBlank { c.id }, onChanged, onNotify)
        "key" -> KeyConnect(app, c, onChanged, onNotify)
        "browser" -> BrowserConnect(app, c, onNotify)
        "mcp" -> McpConnect(app, c, onChanged, onNotify)
        "mail" -> MailConnect(app, c, onChanged, onNotify)
        "google" -> GoogleConnect(app, c, onChanged, onNotify)
        "caldav" -> CalDavConnect(app, c, onChanged, onNotify)
        else -> MutedText(if (c.status == "unsupported") c.description else stringResource(R.string.connector_not_yet), small = true)
    }
}

/** Sign in on the service's own page (hosted MCP servers), in the browser. */
@Composable
private fun McpConnect(app: AppContainer, c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }
    var other by remember(c.id) { mutableStateOf(false) }
    ReloadOnReturn(c.id) { busy = false }
    if (confirming) {
        ConfirmAsk(
            stringResource(R.string.mcp_disconnect_confirm, c.name), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), c.item) }; onChanged() } },
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
                            val url = ConnectorCalls.mcpStart(app.sessions.requireApi(), c.id)
                            if (!openInBrowser(url)) { err = strings.get(R.string.connector_browser_failed); busy = false }
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
                    KeyConnect(app, c.copy(connect = "key", via = fb.via, item = fb.item, description = fb.blurb), onChanged, onNotify)
                } else {
                    LinkButton("${fb.title} ›", onClick = { other = true })
                }
            }
        }
    }
}

/**
 * A mailbox with an app password, or Google sign-in for Gmail. The Gmail connector offers only Gmail; the other mailbox connector
 * offers the server's services in the server's order.
 */
@Composable
private fun MailConnect(app: AppContainer, c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    val gmail = c.id == "gmail"
    var data by remember(c.id) { mutableStateOf<MailProviders?>(null) }
    var provider by remember(c.id) { mutableStateOf("") }
    var address by remember(c.id) { mutableStateOf("") }
    var password by remember(c.id) { mutableStateOf("") }
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var adding by remember(c.id) { mutableStateOf(false) }
    var byPassword by remember(c.id) { mutableStateOf(false) }
    var removing by remember { mutableStateOf<MailAccount?>(null) }

    suspend fun load() {
        runCatching { ConnectorCalls.mailProviders(app.sessions.requireApi()) }.onSuccess { d ->
            data = d
            if (provider.isEmpty() || MailRules.choices(c.id, d.providers).none { it.id == provider }) provider = MailRules.first(c.id, d.providers)
        }
    }
    ReloadOnReturn(c.id) { load(); busy = false }

    val d = data ?: return
    val shown = ConnectorRules.mailAccounts(c.id, d.accounts)
    val choices = ConnectorRules.mailChoices(c.id, d.providers)
    val p = d.providers.find { it.id == provider }

    removing?.let { a ->
        ConfirmAsk(
            stringResource(R.string.mail_disconnect_confirm, a.label), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), a.name) }; load(); onChanged() } },
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
            ReviewNote(app, c, onOpened = { scope.launch { load() }; onChanged() })
            LinkButton(stringResource(R.string.mail_by_password), onClick = { byPassword = true })
        } else if (form && gmail && d.googleWeb && !byPassword) {
            PrimaryButton(
                if (busy) stringResource(R.string.connector_opening) else stringResource(R.string.mail_google_sign_in),
                enabled = !busy,
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            val url = ConnectorCalls.googleWebStart(app.sessions.requireApi())
                            if (!openInBrowser(url)) { err = strings.get(R.string.connector_browser_failed); busy = false }
                        } catch (e: ApiError) {
                            err = e.detail; busy = false
                        }
                    }
                },
            )
            MutedText(stringResource(R.string.mail_google_help), small = true)
            LinkButton(stringResource(R.string.mail_by_password), onClick = { byPassword = true })
        } else if (form) {
            if (choices.size > 1) {
                Text(stringResource(R.string.mail_provider), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
                SelectField(choices.map { SelectOption(it.id, it.name) }, provider, stringResource(R.string.mail_provider), onPick = { provider = it })
            }
            p?.let { Steps(it.help) }
            KeloTextField(address, { address = it }, stringResource(R.string.mail_address), maxLength = 200, label = stringResource(R.string.mail_address))
            KeloTextField(password, { password = it }, stringResource(R.string.mail_password), password = true, maxLength = 200, label = stringResource(R.string.mail_password))
            PrimaryButton(
                stringResource(if (busy) R.string.mail_checking else R.string.check_and_connect),
                enabled = !busy && address.isNotBlank() && password.isNotEmpty(),
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            ConnectorCalls.addMail(app.sessions.requireApi(), provider, address.trim(), password)
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
        if (gmail) OperatorSettings(app, "google", onSaved = { scope.launch { load() } })
    }
}

/** A key or token made on the service (or a Google service account). The server tries it once before saving it. */
@Composable
internal fun KeyConnect(app: AppContainer, c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var f by remember(c.via) { mutableStateOf<KeyForm?>(null) }
    var values by remember(c.via) { mutableStateOf(mapOf<String, String>()) }
    var err by remember(c.via) { mutableStateOf("") }
    var busy by remember(c.via) { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }
    suspend fun load() {
        runCatching { ConnectorCalls.keyForm(app.sessions.requireApi(), c.via.ifBlank { c.id }) }.onSuccess { f = it }
    }
    ReloadOnReturn(c.via) { load() }
    val form = f ?: return
    val google = form.id == "google"
    if (confirming) {
        ConfirmAsk(
            stringResource(R.string.key_disconnect_confirm, form.name), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), form.item) }; load(); onChanged() } },
            onDismiss = { confirming = false },
        )
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (form.connected) {
            ShieldTitle(stringResource(R.string.connector_connected_name, form.label))
            val account = ConnectorRules.keyAccount(form.label)
            if (google && account.isNotEmpty()) {
                SmallText(stringResource(R.string.key_google_share))
                SelectionContainer { Text(emphasized("%1\$s", account, code = true), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.ink) }
            }
            MutedText(stringResource(R.string.key_connected_note), small = true)
            if (onNotify != null) {
                val text = stringResource(R.string.chat_connect_notify, form.name)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            LinkButton(stringResource(R.string.connector_disconnect), onClick = { confirming = true }, danger = true)
            return@Column
        }
        Steps(form.help)
        form.fields.forEach { x ->
            if (google && x.id == "key") {
                SecondaryButton(stringResource(R.string.key_pick_json), onClick = {
                    val file = pickFile() ?: return@SecondaryButton
                    scope.launch {
                        val text = withContext(Dispatchers.IO) { runCatching { if (file.length() > MAX_KEY_FILE) null else file.readText() }.getOrNull() }
                        if (text == null) err = strings.get(R.string.key_file_failed) else values = values + ("key" to text)
                    }
                })
                KeloTextField(values["key"].orEmpty(), { values = values + ("key" to it) }, stringResource(R.string.key_paste_json), singleLine = false, maxLength = 20_000)
            } else {
                KeloTextField(values[x.id].orEmpty(), { values = values + (x.id to it) }, x.placeholder.ifBlank { x.label }, password = x.secret,
                    label = x.label.ifBlank { null })
            }
        }
        PrimaryButton(
            stringResource(if (busy) R.string.key_checking else if (form.checked) R.string.check_and_connect else R.string.save),
            enabled = !busy && ConnectorRules.keyReady(form, values),
            onClick = {
                err = ""; busy = true
                scope.launch {
                    try {
                        ConnectorCalls.saveKey(app.sessions.requireApi(), form.id, values)
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

private const val MAX_KEY_FILE = 64 * 1024

/** The system's file picker (one file), or null. */
private fun pickFile(): java.io.File? {
    val d = java.awt.FileDialog(null as java.awt.Frame?, "", java.awt.FileDialog.LOAD)
    d.isVisible = true
    val dir = d.directory ?: return null
    val name = d.file ?: return null
    return java.io.File(dir, name)
}

/** A site the agent uses with its own browser; a login, if the site needs one, is kept in the vault and typed by the server. */
@Composable
private fun BrowserConnect(app: AppContainer, c: Connector, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    val host = ConnectorRules.host(c.site)
    val savedDefault = stringResource(R.string.browser_saved_default)
    var saved by remember(host) { mutableStateOf<String?>(null) }
    var asking by remember(host) { mutableStateOf<String?>(null) }
    var err by remember(host) { mutableStateOf("") }
    ReloadOnReturn(host) {
        saved = try {
            val it = VaultCalls.info(app.sessions.requireApi()).items.find { i -> i.kind == "login" && host in i.hosts }
            if (it != null) it.usernameHint ?: savedDefault else ""
        } catch (_: ApiError) { "" }
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (c.site.isNotBlank()) LinkButton(stringResource(R.string.browser_site_link, host.ifBlank { c.site }), onClick = {
            if (!openInBrowser(c.site)) err = strings.get(R.string.connector_browser_failed)
        })
        val s = saved
        val a = asking
        when {
            !s.isNullOrEmpty() -> Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(KeloIcons.Shield, contentDescription = null, tint = Kelo.colors.ink, modifier = Modifier.size(16.dp))
                Spacer(Modifier.size(6.dp))
                SmallText(stringResource(R.string.browser_saved, s))
            }
            a != null -> LoginForm(app, a, onNotify, onClosed = { asking = null })
            s == "" -> SecondaryButton(stringResource(R.string.browser_ask), onClick = {
                err = ""
                scope.launch {
                    try { asking = ConnectorCalls.addLogin(app.sessions.requireApi(), c.site, c.name) } catch (e: ApiError) { err = e.detail }
                }
            })
        }
        MutedText(stringResource(R.string.browser_note), small = true)
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
}

/** The secure login form for a site (PUT /api/vault/{name}/login): the value is never shown again, nor seen by the agent. */
@Composable
private fun LoginForm(app: AppContainer, name: String, onNotify: ((String) -> Unit)?, onClosed: () -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var item by remember(name) { mutableStateOf<VaultItem?>(null) }
    var done by remember(name) { mutableStateOf<VaultMode?>(null) }
    var user by remember(name) { mutableStateOf("") }
    var pass by remember(name) { mutableStateOf("") }
    var mode by remember(name) { mutableStateOf(VaultMode.AFTER_SUCCESS) }
    var msg by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    LaunchedEffect(name) { item = try { VaultCalls.find(app.sessions.requireApi(), name) } catch (_: ApiError) { null } }
    val it = item ?: return
    val what = stringResource(R.string.vault_what_login, it.label)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        ShieldTitle(stringResource(R.string.vault_card_needed, what))
        val d = done
        if (d != null) {
            SmallText(stringResource(when (d) {
                VaultMode.AFTER_SUCCESS -> R.string.vault_card_done_after_success
                VaultMode.ONCE -> R.string.vault_card_done_once
                VaultMode.NOW -> R.string.vault_card_done_now
            }))
            if (onNotify != null) {
                val text = stringResource(if (d == VaultMode.NOW) R.string.vault_card_notify_saved else R.string.vault_card_notify_entered, what)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            return@Column
        }
        KeloTextField(user, { user = it }, stringResource(R.string.vault_username), maxLength = 200, label = stringResource(R.string.vault_username))
        KeloTextField(pass, { pass = it }, stringResource(R.string.vault_password), password = true, maxLength = 512, label = stringResource(R.string.vault_password))
        Text(stringResource(R.string.vault_mode), style = MaterialTheme.typography.labelLarge, color = c.ink)
        VaultMode.entries.forEach { m ->
            val on = m == mode
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(if (on) c.mine else c.bubble).clickable { mode = m }.padding(10.dp)) {
                Text(stringResource(when (m) {
                    VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success
                    VaultMode.NOW -> R.string.vault_mode_now
                    VaultMode.ONCE -> R.string.vault_mode_once
                }), style = MaterialTheme.typography.bodyMedium, color = c.ink, fontWeight = if (on) FontWeight.SemiBold else null)
            }
        }
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.vault_save), enabled = !busy && pass.isNotBlank() && user.isNotBlank(), onClick = {
            busy = true; msg = ""
            scope.launch {
                try {
                    VaultCalls.putLogin(app.sessions.requireApi(), it.name, user.trim(), pass, mode)
                    user = ""; pass = ""; done = mode
                } catch (e: ApiError) { msg = e.detail } finally { busy = false }
            }
        })
        LinkButton(stringResource(R.string.cancel), onClick = {
            scope.launch { runCatching { VaultCalls.cancelDraft(app.sessions.requireApi(), name) }; onClosed() }
        })
        MutedText(stringResource(R.string.vault_footer), small = true)
    }
}

/**
 * Connect a service by typing a short code on its own page (GitHub, Microsoft). When the server offers a web sign-in, the sign-in
 * button comes first and the code is the other way.
 */
@Composable
fun DeviceConnect(app: AppContainer, id: String, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    val changed by rememberUpdatedState(onChanged)
    var p by remember(id) { mutableStateOf<OAuthProvider?>(null) }
    var flow by remember(id) { mutableStateOf<OAuthFlow?>(null) }
    var deadline by remember { mutableLongStateOf(0L) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    var clientId by remember { mutableStateOf("") }
    var editId by remember { mutableStateOf(false) }
    var scopeId by remember(id) { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var copied by remember { mutableStateOf(false) }
    var byCode by remember(id) { mutableStateOf(false) }
    var confirming by remember { mutableStateOf(false) }

    suspend fun load() {
        try {
            val x = ConnectorCalls.oauthProviders(app.sessions.requireApi()).find { it.id == id }
            // a connection shows the choice it was made with; otherwise the first (the default) until the user picks one
            if (scopeId.isEmpty() || (x?.connected == true && x.grantedScope.isNotEmpty() && p?.grantedScope != x.grantedScope)) {
                scopeId = x?.grantedScope?.takeIf { it.isNotEmpty() } ?: x?.scopes?.firstOrNull()?.id.orEmpty()
            }
            p = x
        } catch (_: ApiError) {
        }
    }
    ReloadOnReturn(id) { load() }

    val flowId = flow?.id
    val pending = flow?.pending == true
    LaunchedEffect(flowId, pending) {
        if (flowId == null || !pending) return@LaunchedEffect
        var ticks = 0
        while (true) {
            delay(1000)
            now = System.currentTimeMillis()
            if (++ticks % 3 != 0) continue
            try {
                val f = ConnectorCalls.flow(app.sessions.requireApi(), flowId)
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

    val provider = p ?: return
    val name = provider.name
    fun act(block: suspend () -> Unit) {
        err = ""; busy = true
        scope.launch { try { block() } catch (e: ApiError) { err = e.detail } finally { busy = false } }
    }
    if (confirming) {
        ConfirmAsk(
            stringResource(R.string.device_disconnect_confirm, name), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), provider.item) }; flow = null; load(); changed() } },
            onDismiss = { confirming = false },
        )
    }
    val f = flow
    if (provider.connected && (f == null || f.status == "connected")) {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            ShieldTitle(stringResource(R.string.connector_connected_name, name))
            MutedText(stringResource(R.string.device_connected_note, provider.account.ifBlank { name }), small = true)
            if (onNotify != null) {
                val text = stringResource(R.string.chat_connect_notify, name)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            LinkButton(stringResource(R.string.connector_disconnect), onClick = { confirming = true }, danger = true)
            if (provider.settingsUrl.isNotBlank()) LinkButton(stringResource(R.string.device_manage, name), onClick = { openInBrowser(provider.settingsUrl) })
        }
        return
    }
    if (f != null && f.pending) {
        val c = Kelo.colors
        val secs = ConnectorRules.secondsLeft(deadline, now)
        val codeLabel = stringResource(R.string.device_code_label)
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            BoldLine(stringResource(R.string.device_enter_code, name))
            SelectionContainer {
                Text(
                    f.userCode,
                    modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.bubble).padding(horizontal = 8.dp, vertical = 14.dp)
                        .semantics { contentDescription = codeLabel },
                    color = c.ink, textAlign = TextAlign.Center, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = 28.sp, letterSpacing = 3.sp,
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                SecondaryButton(stringResource(if (copied) R.string.device_copied else R.string.device_copy), onClick = { Browser.copy(f.userCode); copied = true },
                    modifier = Modifier.weight(1f))
                PrimaryButton(stringResource(R.string.device_open, name), onClick = {
                    if (!openInBrowser(f.verificationUri)) err = strings.get(R.string.connector_browser_failed)
                }, modifier = Modifier.weight(1f))
            }
            MutedText(stringResource(R.string.device_wait_help, name, ConnectorRules.mmss(secs)), small = true)
            LinkButton(stringResource(R.string.cancel), onClick = {
                scope.launch { runCatching { ConnectorCalls.cancelFlow(app.sessions.requireApi(), f.id) }; flow = null }
            })
            if (err.isNotEmpty()) ErrorText(err, small = true)
        }
        return
    }
    val ended = ConnectorRules.ended(f)
    val failedText = stringResource(R.string.device_failed)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (ended != null) ErrorText(ended.message.ifBlank { failedText }, small = true)
        @Composable
        fun ScopePicker() {
            if (provider.scopes.isEmpty()) return
            Text(stringResource(R.string.device_scope), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
            SelectField(provider.scopes.map { SelectOption(it.id, it.label) }, scopeId, stringResource(R.string.device_scope), onPick = { scopeId = it })
            provider.scopes.find { it.id == scopeId }?.detail?.takeIf { it.isNotBlank() }?.let { MutedText(it, small = true) }
        }
        if (provider.web && !byCode) {
            ScopePicker()
            PrimaryButton(
                if (busy) stringResource(R.string.connector_opening) else stringResource(R.string.device_sign_in, name),
                enabled = !busy,
                onClick = {
                    act {
                        val url = ConnectorCalls.githubWebStart(app.sessions.requireApi(), scopeId)
                        if (!openInBrowser(url)) err = strings.get(R.string.connector_browser_failed)
                    }
                },
            )
            MutedText(stringResource(R.string.device_sign_in_help, name), small = true)
            LinkButton(stringResource(R.string.device_by_code), onClick = { byCode = true })
        } else if (!provider.hasClientId || editId) {
            BoldLine(stringResource(R.string.device_register, name))
            Steps(provider.help)
            KeloTextField(clientId, { clientId = it }, stringResource(R.string.client_id), maxLength = 100)
            PrimaryButton(stringResource(R.string.save), enabled = !busy && clientId.trim().length >= 6, onClick = {
                act {
                    p = ConnectorCalls.saveClientId(app.sessions.requireApi(), id, clientId.trim())
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
                        val started = ConnectorCalls.startFlow(app.sessions.requireApi(), id, scopeId)
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
        if (provider.id == "github") OperatorSettings(app, "github", onSaved = { scope.launch { load() } })
    }
}

/**
 * Google Calendar: the Google sign-in first, each connected account with its own disconnect, the service account kept as the other
 * way ([Connector.fallback]). While new Google sign-ins wait for Google's review there is no sign-in button: the note and the beta
 * request. (The phone also offers its own calendar there; a computer has none to share.)
 */
@Composable
private fun GoogleConnect(app: AppContainer, c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    var state by remember(c.id) { mutableStateOf<GoogleCalendarState?>(null) }
    var failed by remember(c.id) { mutableStateOf(false) }
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var removing by remember { mutableStateOf<GoogleCalendarAccount?>(null) }
    var other by remember(c.id) { mutableStateOf(false) }
    suspend fun load() {
        try { state = ConnectorCalls.googleCalendar(app.sessions.requireApi()); failed = false } catch (e: kotlinx.coroutines.CancellationException) { throw e } catch (_: Exception) { failed = true }
    }
    ReloadOnReturn(c.id) { load(); busy = false }
    removing?.let { a ->
        ConfirmAsk(
            stringResource(R.string.gcal_disconnect_confirm, a.address), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), a.name) }; load(); onChanged() } },
            onDismiss = { removing = null },
        )
    }
    val s = state
    if (s == null) {
        if (failed) ErrorText(stringResource(R.string.connect_failed), small = true)
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        s.accounts.forEach { a ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                ShieldTitle(a.address)
                MutedText(stringResource(when {
                    a.problem -> R.string.gcal_account_problem
                    a.canWrite -> R.string.gcal_account_note
                    else -> R.string.gcal_account_read_only
                }), small = true)
                LinkButton(stringResource(R.string.connector_disconnect), onClick = { removing = a }, danger = true)
            }
        }
        if (s.accounts.isNotEmpty() && onNotify != null) {
            val text = stringResource(R.string.chat_connect_notify, c.name)
            PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
        }
        val pending = ConnectorRules.pendingReview(c) || s.pendingReview
        if (pending) {
            if (s.accounts.isEmpty() || s.accounts.any { it.problem }) ReviewNote(app, c, onOpened = { scope.launch { load() }; onChanged() })
        } else if (s.accounts.isEmpty()) BoldLine(stringResource(R.string.gcal_title))
        if (!pending && s.available) {
            val label = when {
                busy -> stringResource(R.string.connector_opening)
                s.accounts.isEmpty() -> stringResource(R.string.gcal_sign_in)
                s.accounts.any { it.problem } -> stringResource(R.string.gcal_sign_in_again)
                else -> stringResource(R.string.gcal_add_account)
            }
            val start: () -> Unit = {
                err = ""; busy = true
                scope.launch {
                    try {
                        val url = ConnectorCalls.googleWebStart(app.sessions.requireApi(), "calendar")
                        if (!openInBrowser(url)) { err = strings.get(R.string.connector_browser_failed); busy = false }
                    } catch (e: ApiError) { err = e.detail; busy = false }
                }
            }
            if (s.accounts.isEmpty() || s.accounts.any { it.problem }) PrimaryButton(label, enabled = !busy, onClick = start)
            else SecondaryButton(label, onClick = start, enabled = !busy)
            if (s.accounts.isEmpty()) MutedText(stringResource(R.string.gcal_help), small = true)
        } else if (!pending && s.accounts.isEmpty()) {
            MutedText(s.reason.ifBlank { stringResource(R.string.gcal_not_ready) }, small = true)
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)
        val fb = c.fallback
        if (fb != null && fb.connect == "key" && (!pending || fb.connected)) {
            if (other || fb.connected) {
                BoldLine(stringResource(R.string.gcal_other_way))
                KeyConnect(app, c.copy(connect = "key", via = fb.via, item = fb.item, description = fb.blurb), onChanged, null)
            } else {
                LinkButton(stringResource(R.string.gcal_other_way_open), onClick = { other = true })
            }
        }
        OperatorSettings(app, "google", onSaved = { scope.launch { load() } })
    }
}
