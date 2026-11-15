package chat.kelo.app.chat.vault

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.wallet.AddCardForm
import kotlinx.coroutines.launch

/**
 * A secure entry the agent asked for, inline in the chat (and in the Keychain's "waiting for input" list): what it is for, where
 * the value will be sent, the field(s), [저장] and [취소]. The value goes from here straight to the vault (a key or a login) or the
 * wallet (a card, through the wallet's own card form and its end-to-end sealing) and is never put in the chat, the model's context,
 * the logs or a notification. Saving is the consent; nothing else is asked first. An answered entry folds into one line.
 *
 * [onSaved]: the server's answer (a reply it started to carry on, if any) and how it went; [onCancel]: [취소] was pressed;
 * [onGone]: the server no longer has it open (answered on another phone, or it ran out).
 */
@Composable
fun SecretEntryCard(
    request: SecretRequest,
    onSaved: (SaveAnswer, SecretResult?) -> Unit,
    onCancel: () -> Unit,
    onGone: () -> Unit = {},
    modifier: Modifier = Modifier,
    framed: Boolean = true,
) {
    val body: @Composable () -> Unit = {
        Column(Modifier.fillMaxWidth().animateContentSize(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (request.open) OpenEntry(request, onSaved, onCancel, onGone) else FoldedEntry(request)
        }
    }
    if (framed) CardBox(modifier) { body() } else Column(modifier) { body() }
}

/** The one line an answered entry leaves. */
@Composable
private fun FoldedEntry(r: SecretRequest) {
    val c = Kelo.colors
    val (icon, text, muted) = when (r.state) {
        SecretRequest.STATE_SAVED -> Triple(
            KeloIcons.Shield,
            when {
                r.isCard -> stringResource(R.string.secret_done_card, r.result?.card?.label?.ifEmpty { null } ?: r.label)
                r.isPin -> stringResource(R.string.pin_secret_done, r.hosts.firstOrNull() ?: r.name)
                r.isLogin -> stringResource(R.string.secret_done_login, r.label.ifEmpty { r.name })
                r.result?.checked == "ok" -> stringResource(R.string.secret_done_checked, r.label.ifEmpty { r.name })
                else -> stringResource(R.string.secret_done_key, r.label.ifEmpty { r.name })
            },
            false,
        )
        SecretRequest.STATE_CANCELLED -> Triple(KeloIcons.Close, stringResource(R.string.secret_cancelled), true)
        else -> Triple(KeloIcons.Clock, stringResource(R.string.secret_gone), true)
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(icon, contentDescription = null, tint = if (muted) c.muted else c.ink, modifier = Modifier.size(16.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = if (muted) c.muted else c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun OpenEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onCancel: () -> Unit, onGone: () -> Unit) {
    // a key, password or card number is typed here: keep the window out of screenshots and the recent-apps preview
    chat.kelo.app.ui.SecureWindow()
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.Top) {
        Icon(if (r.isCard) KeloIcons.Card else KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.padding(top = 2.dp).size(20.dp))
        Spacer(Modifier.size(8.dp))
        Text(
            when {
                r.isCard -> stringResource(R.string.secret_title_card)
                r.isPin -> stringResource(R.string.pin_secret_title, r.hosts.firstOrNull() ?: r.name)
                r.isLogin -> stringResource(R.string.secret_title_login, r.label.ifEmpty { r.hosts.firstOrNull().orEmpty() })
                else -> stringResource(R.string.secret_title_key, r.label.ifEmpty { r.name })
            },
            fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f).semantics { heading() },
        )
    }
    // what the card is for (a card's label already says it)
    if (r.reason.isNotBlank() && r.reason != r.label) Text(r.reason, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    else if (r.isCard && r.label.isNotBlank()) Text(r.label, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    // where the value goes
    val hosts = SecretRules.hostsText(r.hosts)
    MutedText(
        when {
            r.isPin -> stringResource(R.string.pin_secret_where)
            r.isCard && hosts.isNotEmpty() -> stringResource(R.string.secret_card_site, hosts)
            r.isCard -> stringResource(R.string.secret_card_wallet)
            else -> stringResource(R.string.secret_hosts_only, hosts)
        },
        small = true,
    )
    when {
        r.isCard -> CardEntry(r, onSaved, onGone)
        r.isPin -> chat.kelo.app.wallet.PinForm(site = r.hosts.firstOrNull() ?: r.name, siteFixed = true, requestId = r.id, onSaved = { saved ->
            onSaved(SaveAnswer(ok = true, turnId = saved.turnId), SecretResult())
        })
        r.isLogin -> LoginEntry(r, onSaved, onGone)
        else -> KeyEntry(r, onSaved, onGone)
    }
    LinkButton(stringResource(R.string.cancel), onClick = onCancel)
}

/** A key or token: one masked field with Show and Paste. Tried once before it is saved when [SecretRequest.check]. */
@Composable
private fun KeyEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    var value by remember(r.id) { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    val failed = stringResource(R.string.secret_failed)
    fun save() {
        busy = true; problem = ""
        scope.launch {
            try {
                val answer = VaultCalls.putSecret(sessions.requireApi(), r.name, value.trim())
                value = ""
                haptics.confirm()
                onSaved(answer, SecretResult(checked = if (answer.checked) "ok" else "unchecked"))
            } catch (e: ApiError) {
                if (e.status == 404) onGone() else problem = if (e.isNetwork) failed else e.detail
                haptics.reject()
            } finally {
                busy = false
            }
        }
    }
    SecretField(stringResource(R.string.secret_field_key), value, { value = it }, keyboard = KeyboardType.Password)
    if (r.check) MutedText(stringResource(R.string.secret_check_note), small = true)
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
    PrimaryButton(
        stringResource(if (busy) (if (r.check) R.string.secret_checking else R.string.vault_saving) else R.string.save),
        onClick = ::save, enabled = !busy && SecretEntryRules.keyReady(value),
    )
    MutedText(stringResource(R.string.secret_never_chat), small = true)
}

/** A website login: user name and password (Show, Paste), and how it is kept (by default only once a sign-in with it works). */
@Composable
private fun LoginEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    var user by remember(r.id) { mutableStateOf("") }
    var pass by remember(r.id) { mutableStateOf("") }
    var mode by rememberSaveable(r.id) { mutableStateOf(VaultMode.AFTER_SUCCESS) }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf("") }
    val failed = stringResource(R.string.secret_failed)
    fun save() {
        busy = true; problem = ""
        scope.launch {
            try {
                val answer = VaultCalls.putLogin(sessions.requireApi(), r.name, user.trim(), pass, mode)
                user = ""; pass = ""
                haptics.confirm()
                onSaved(answer, SecretResult(mode = mode.wire))
            } catch (e: ApiError) {
                if (e.status == 404) onGone() else problem = if (e.isNetwork) failed else e.detail
                haptics.reject()
            } finally {
                busy = false
            }
        }
    }
    SecretField(stringResource(R.string.vault_username), user, { user = it }, keyboard = KeyboardType.Email, masked = false)
    SecretField(stringResource(R.string.vault_password), pass, { pass = it }, keyboard = KeyboardType.Password)
    Text(stringResource(R.string.vault_mode), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
    ModePicker(mode) { mode = it }
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
    PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.save), onClick = ::save,
        enabled = !busy && SecretEntryRules.loginReady(user, pass))
    MutedText(stringResource(R.string.secret_never_chat), small = true)
}

/** A payment card: the wallet's own card form (number, expiry and CVC, nickname, the notice), sealed on this phone; then the request
 *  is answered with the new card (its nickname and last four digits). */
@Composable
private fun CardEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var problem by remember { mutableStateOf("") }
    val failed = stringResource(R.string.secret_failed)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        AddCardForm(inline = true, onSaved = { card ->
            scope.launch {
                try {
                    val answer = SecretCalls.cardSaved(sessions.requireApi(), r.id, card.id)
                    onSaved(answer, SecretResult(card = SecretCard(card.id, card.label, card.brand, card.last4)))
                } catch (e: ApiError) {
                    // the card is in the wallet either way; an entry that is gone just folds
                    if (e.status == 404) onGone() else problem = if (e.isNetwork) failed else e.detail
                }
            }
        })
    }
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
}

/** A labelled field; [masked] values show as dots until Show is pressed. Paste puts the clipboard's text in (trimmed). */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SecretField(label: String, value: String, onChange: (String) -> Unit, keyboard: KeyboardType, masked: Boolean = true) {
    val c = Kelo.colors
    val clipboard = LocalClipboardManager.current
    var shown by remember { mutableStateOf(false) }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = c.ink)
        // no placeholder: the label is right above, and the same words inside the empty field showed them twice ("키 또는 토큰")
        KeloTextField(
            value, onChange, "", modifier = Modifier.semantics { contentDescription = label }, maxLength = 4096, password = masked && !shown,
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = if (shown && masked) KeyboardType.Ascii else keyboard),
            trailingIcon = if (masked) {
                {
                    val what = stringResource(if (shown) R.string.secret_hide else R.string.secret_show)
                    IconButton(onClick = { shown = !shown }) {
                        Icon(if (shown) KeloIcons.Close else KeloIcons.Eye, contentDescription = what, tint = c.muted, modifier = Modifier.size(20.dp))
                    }
                }
            } else null,
        )
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SecondaryButton(stringResource(R.string.secret_paste), onClick = {
                clipboard.getText()?.text?.trim()?.takeIf { it.isNotEmpty() }?.let { onChange(it.take(4096)) }
            })
        }
    }
}
