package chat.kelo.desktop.chat

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
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.vault.SaveAnswer
import chat.kelo.app.chat.vault.SecretEntryRules
import chat.kelo.app.chat.vault.SecretRequest
import chat.kelo.app.chat.vault.SecretResult
import chat.kelo.app.chat.vault.SecretRules
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.chat.vault.VaultMode
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch

/**
 * A secure entry the agent asked for, inline in the chat and in the Keychain's waiting list (the phone's SecretEntryCard): what it
 * is for, where the value goes, the field(s), Save and Cancel. A key or a login goes from here straight to the vault; a card is added
 * on the phone (cards are sealed with the phone's own key; the same entry waits in the phone's chat), so here it only says so and can
 * be cancelled. Saving is the consent. An answered entry folds into one line.
 */
@Composable
fun SecretEntryCard(
    request: SecretRequest, sessions: Sessions,
    onSaved: (SaveAnswer, SecretResult?) -> Unit, onCancel: () -> Unit, onGone: () -> Unit = {}, framed: Boolean = true,
    modifier: Modifier = Modifier,
) {
    val body: @Composable () -> Unit = {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (request.open) OpenEntry(request, sessions, onSaved, onCancel, onGone) else Folded(request)
        }
    }
    if (framed) CardBox(modifier) { body() } else body()
}

@Composable
private fun Folded(r: SecretRequest) {
    when (r.state) {
        SecretRequest.STATE_SAVED -> ResultLine(
            KeloIcons.Shield,
            when {
                r.isCard -> stringResource(R.string.secret_done_card, r.result?.card?.label?.ifEmpty { null } ?: r.label)
                r.isPin -> stringResource(R.string.pin_secret_done, r.hosts.firstOrNull() ?: r.name)
                r.isLogin -> stringResource(R.string.secret_done_login, r.label.ifEmpty { r.name })
                r.result?.checked == "ok" -> stringResource(R.string.secret_done_checked, r.label.ifEmpty { r.name })
                else -> stringResource(R.string.secret_done_key, r.label.ifEmpty { r.name })
            },
        )
        SecretRequest.STATE_CANCELLED -> ResultLine(KeloIcons.Close, stringResource(R.string.secret_cancelled), muted = true)
        else -> ResultLine(KeloIcons.Clock, stringResource(R.string.secret_gone), muted = true)
    }
}

@Composable
private fun OpenEntry(r: SecretRequest, sessions: Sessions, onSaved: (SaveAnswer, SecretResult?) -> Unit, onCancel: () -> Unit, onGone: () -> Unit) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.Top) {
        Icon(if (r.isCard) KeloIcons.Card else if (r.isPin) KeloIcons.Lock else KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.padding(top = 2.dp).size(20.dp))
        Spacer(Modifier.size(8.dp))
        Text(
            when {
                r.isCard -> stringResource(R.string.secret_title_card)
                r.isPin -> stringResource(R.string.pin_secret_title, r.hosts.firstOrNull() ?: r.name)
                r.isLogin -> stringResource(R.string.secret_title_login, r.label.ifEmpty { r.hosts.firstOrNull().orEmpty() })
                else -> stringResource(R.string.secret_title_key, r.label.ifEmpty { r.name })
            },
            fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f),
        )
    }
    if (r.reason.isNotBlank() && r.reason != r.label) Text(r.reason, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    else if (r.isCard && r.label.isNotBlank()) Text(r.label, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    val hosts = SecretRules.hostsText(r.hosts)
    MutedText(
        when {
            r.isCard && hosts.isNotEmpty() -> stringResource(R.string.secret_card_site, hosts)
            r.isCard -> stringResource(R.string.secret_card_wallet)
            r.isPin -> stringResource(R.string.pin_secret_where)
            else -> stringResource(R.string.secret_hosts_only, hosts)
        },
        small = true,
    )
    when {
        r.isCard -> Text(stringResource(R.string.secret_card_on_phone), style = MaterialTheme.typography.bodyMedium, color = c.ink)
        // a payment PIN is sealed with the phone's key like an end-to-end card: only the phone can take it
        r.isPin -> Text(stringResource(R.string.desktop_pin_secret_on_phone), style = MaterialTheme.typography.bodyMedium, color = c.ink)
        else -> ValueEntry(r, sessions, onSaved, onGone)
    }
    LinkButton(stringResource(R.string.cancel), onClick = onCancel)
}

/** A key (one masked field) or a login (user name, password, how it is kept). */
@Composable
private fun ValueEntry(r: SecretRequest, sessions: Sessions, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var user by remember(r.id) { mutableStateOf("") }
    var value by remember(r.id) { mutableStateOf("") }
    var mode by remember(r.id) { mutableStateOf(VaultMode.AFTER_SUCCESS) }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    val failed = stringResource(R.string.secret_failed)
    fun save() {
        busy = true; problem = ""
        scope.launch {
            try {
                val api = sessions.requireApi()
                if (r.isLogin) {
                    val a = VaultCalls.putLogin(api, r.name, user.trim(), value, mode)
                    user = ""; value = ""
                    onSaved(a, SecretResult(mode = mode.wire))
                } else {
                    val a = VaultCalls.putSecret(api, r.name, value.trim())
                    value = ""
                    onSaved(a, SecretResult(checked = if (a.checked) "ok" else "unchecked"))
                }
            } catch (e: ApiError) {
                if (e.status == 404) onGone() else problem = if (e.isNetwork) failed else e.detail
            } finally {
                busy = false
            }
        }
    }
    if (r.isLogin) Field(stringResource(R.string.vault_username), user, { user = it }, masked = false)
    Field(stringResource(if (r.isLogin) R.string.vault_password else R.string.secret_field_key), value, { value = it }, masked = true)
    if (r.isLogin) {
        Text(stringResource(R.string.vault_mode), style = MaterialTheme.typography.labelLarge, color = c.ink)
        VaultMode.entries.forEach { m ->
            val on = m == mode
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(if (on) c.mine else c.bubble).clickable { mode = m }.padding(10.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(stringResource(when (m) {
                    VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success
                    VaultMode.NOW -> R.string.vault_mode_now
                    VaultMode.ONCE -> R.string.vault_mode_once
                }), style = MaterialTheme.typography.bodyMedium, color = c.ink, fontWeight = if (on) FontWeight.SemiBold else null)
                if (on) Text(stringResource(when (m) {
                    VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success_help
                    VaultMode.NOW -> R.string.vault_mode_now_help
                    VaultMode.ONCE -> R.string.vault_mode_once_help
                }), style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
        }
    } else if (r.check) {
        MutedText(stringResource(R.string.secret_check_note), small = true)
    }
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
    val ready = if (r.isLogin) SecretEntryRules.loginReady(user, value) else SecretEntryRules.keyReady(value)
    PrimaryButton(
        stringResource(if (busy) (if (r.check && !r.isLogin) R.string.secret_checking else R.string.vault_saving) else R.string.save),
        onClick = ::save, enabled = !busy && ready,
    )
    MutedText(stringResource(R.string.secret_never_chat), small = true)
}

@Composable
private fun Field(label: String, value: String, onChange: (String) -> Unit, masked: Boolean) {
    val clipboard = LocalClipboardManager.current
    var shown by remember { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        KeloTextField(
            // the label floats above once the field is focused; a placeholder of the same words showed them twice ("키 또는 토큰")
            value, onChange, "", password = masked && !shown, maxLength = 4096, label = label,
            trailingIcon = if (masked) {
                {
                    IconButton(onClick = { shown = !shown }) {
                        Icon(if (shown) KeloIcons.Close else KeloIcons.Eye,
                            contentDescription = stringResource(if (shown) R.string.secret_hide else R.string.secret_show),
                            tint = Kelo.colors.muted, modifier = Modifier.size(18.dp))
                    }
                }
            } else null,
        )
        SecondaryButton(stringResource(R.string.secret_paste), onClick = {
            clipboard.getText()?.text?.trim()?.takeIf { it.isNotEmpty() }?.let { onChange(it.take(4096)) }
        })
    }
}
