package chat.kelo.app.connectors

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** GET /api/caldav: the CalDAV calendars added here with their own address (iCloud, Yahoo and Naver come with their mail). */
@Serializable
data class CalDavAccount(val name: String = "", val address: String = "", val url: String = "")

@Serializable
data class CalDavAccounts(val accounts: List<CalDavAccount> = emptyList())

object CalDavCalls {
    suspend fun accounts(api: KeloApi): List<CalDavAccount> = api.get<CalDavAccounts>("/api/caldav").accounts

    /** The server signs in once to check the address, user name and app password; only then is anything saved. */
    suspend fun add(api: KeloApi, url: String, username: String, password: String) {
        api.post<JsonElement>("/api/caldav/accounts", buildJsonObject { put("url", url); put("username", username); put("password", password) })
    }
}

/**
 * A calendar from any provider that offers CalDAV (Fastmail, Zoho, Nextcloud ...): the provider's CalDAV address, user name and an app
 * password. Read only: the morning briefing and heads-ups read it; Kelo never changes it.
 */
@Composable
internal fun CalDavConnect(c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var accounts by remember(c.id) { mutableStateOf<List<CalDavAccount>?>(null) }
    var url by remember(c.id) { mutableStateOf("") }
    var user by remember(c.id) { mutableStateOf("") }
    var password by remember(c.id) { mutableStateOf("") }
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var adding by remember(c.id) { mutableStateOf(false) }
    var removing by remember { mutableStateOf<CalDavAccount?>(null) }

    suspend fun load() { runCatching { CalDavCalls.accounts(sessions.requireApi()) }.onSuccess { accounts = it } }
    ReloadOnReturn(c.id) { load(); busy = false }
    val shown = accounts
    if (shown == null) {
        chat.kelo.app.ui.components.SkeletonLines(2)
        return
    }
    removing?.let { a ->
        ConfirmDialog(
            stringResource(R.string.caldav_disconnect_confirm, a.address), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(sessions.requireApi(), a.name) }; load(); onChanged() } },
            onDismiss = { removing = null },
        )
    }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        shown.forEach { a ->
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                ShieldTitle(a.address)
                MutedText(a.url.removePrefix("https://"), small = true)
                MutedText(stringResource(R.string.caldav_account_note), small = true)
                LinkButton(stringResource(R.string.connector_disconnect), onClick = { removing = a }, danger = true)
            }
        }
        val form = shown.isEmpty() || adding
        if (form) {
            chat.kelo.app.ui.SecureWindow()   // an app password being typed
            MutedText(stringResource(R.string.caldav_help), small = true)
            Text(stringResource(R.string.caldav_url), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
            KeloTextField(
                url, { url = it }, stringResource(R.string.caldav_url_hint), maxLength = 300,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri),
            )
            KeloTextField(
                user, { user = it }, stringResource(R.string.caldav_user), maxLength = 200,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email),
            )
            KeloTextField(
                password, { password = it }, stringResource(R.string.mail_password), password = true, maxLength = 200,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
            )
            PrimaryButton(
                stringResource(if (busy) R.string.mail_checking else R.string.check_and_connect),
                enabled = !busy && url.isNotBlank() && user.isNotBlank() && password.isNotEmpty(),
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            CalDavCalls.add(sessions.requireApi(), url.trim(), user.trim(), password)
                            password = ""; user = ""; url = ""; adding = false
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
        } else {
            if (onNotify != null) {
                val text = stringResource(R.string.caldav_notify)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
            LinkButton(stringResource(R.string.caldav_add_other), onClick = { adding = true })
        }
        if (err.isNotEmpty()) ErrorText(err, small = true)
    }
}
