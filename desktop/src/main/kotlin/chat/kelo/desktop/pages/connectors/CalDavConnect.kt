package chat.kelo.desktop.pages.connectors

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.connectors.Connector
import chat.kelo.app.connectors.ConnectorCalls
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// The CalDAV calendar connector of android/kelo .../connectors/CalDavConnect.kt (its model and calls are in that Android screen file,
// so they are copied here). Keep in step with it.

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
internal fun CalDavConnect(app: AppContainer, c: Connector, onChanged: () -> Unit, onNotify: ((String) -> Unit)?) {
    val scope = rememberCoroutineScope()
    var accounts by remember(c.id) { mutableStateOf<List<CalDavAccount>?>(null) }
    var url by remember(c.id) { mutableStateOf("") }
    var user by remember(c.id) { mutableStateOf("") }
    var password by remember(c.id) { mutableStateOf("") }
    var err by remember(c.id) { mutableStateOf("") }
    var busy by remember(c.id) { mutableStateOf(false) }
    var adding by remember(c.id) { mutableStateOf(false) }
    var removing by remember { mutableStateOf<CalDavAccount?>(null) }

    suspend fun load() { runCatching { CalDavCalls.accounts(app.sessions.requireApi()) }.onSuccess { accounts = it } }
    ReloadOnReturn(c.id) { load(); busy = false }
    val shown = accounts ?: return
    removing?.let { a ->
        ConfirmAsk(
            stringResource(R.string.caldav_disconnect_confirm, a.address), stringResource(R.string.connector_disconnect),
            onConfirm = { scope.launch { runCatching { ConnectorCalls.disconnect(app.sessions.requireApi(), a.name) }; load(); onChanged() } },
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
            MutedText(stringResource(R.string.caldav_help), small = true)
            Text(stringResource(R.string.caldav_url), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
            KeloTextField(url, { url = it }, stringResource(R.string.caldav_url_hint), maxLength = 300)
            KeloTextField(user, { user = it }, stringResource(R.string.caldav_user), maxLength = 200, label = stringResource(R.string.caldav_user))
            KeloTextField(password, { password = it }, stringResource(R.string.mail_password), password = true, maxLength = 200,
                label = stringResource(R.string.mail_password))
            PrimaryButton(
                stringResource(if (busy) R.string.mail_checking else R.string.check_and_connect),
                enabled = !busy && url.isNotBlank() && user.isNotBlank() && password.isNotEmpty(),
                onClick = {
                    err = ""; busy = true
                    scope.launch {
                        try {
                            CalDavCalls.add(app.sessions.requireApi(), url.trim(), user.trim(), password)
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
