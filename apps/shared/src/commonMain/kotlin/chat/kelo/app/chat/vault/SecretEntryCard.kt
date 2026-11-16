@file:OptIn(
    androidx.compose.foundation.ExperimentalFoundationApi::class,
    androidx.compose.material3.ExperimentalMaterial3Api::class,
)

package chat.kelo.app.chat.vault

import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.ui.platform.Haptics
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.text.*

import chat.kelo.app.res.*

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppServices
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalHaptics
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
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SecretEntryCard(
    request: SecretRequest,
    onSaved: (SaveAnswer, SecretResult?) -> Unit,
    onCancel: () -> Unit,
    onGone: () -> Unit = {},
    modifier: Modifier = Modifier,
    framed: Boolean = true,
) {
    var showSheet by rememberSaveable(request.id) { mutableStateOf(false) }

    val body: @Composable () -> Unit = {
        Column(
            Modifier
                .fillMaxWidth()
                .animateContentSize()
                .then(if (framed && request.open) Modifier.clickable { showSheet = true } else Modifier),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            when {
                !request.open -> FoldedEntry(request)
                !framed -> OpenEntry(request, onSaved, onCancel, onGone)
                else -> SummaryEntry(request, onEnter = { showSheet = true }, onCancel = onCancel)
            }
        }
    }

    if (framed) CardBox(modifier) { body() } else Column(modifier) { body() }

    if (showSheet && request.open) {
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
        ModalBottomSheet(
            onDismissRequest = { showSheet = false },
            sheetState = sheetState,
            containerColor = Kelo.colors.card,
        ) {
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                val minH = maxHeight * 0.5f
                val maxH = maxHeight * 0.9f
                Column(
                    Modifier
                        .fillMaxWidth()
                        .heightIn(min = minH, max = maxH)
                        .imePadding()
                        .navigationBarsPadding()
                        .verticalScroll(rememberScrollState())
                        .padding(horizontal = 20.dp, vertical = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    OpenEntry(
                        request,
                        onSaved = { ans, res -> showSheet = false; onSaved(ans, res) },
                        onCancel = { showSheet = false; onCancel() },
                        onGone = { showSheet = false; onGone() },
                    )
                }
            }
        }
    }
}

/** Compact summary in the chat: site, what is asked, cancel, and a primary button "입력하기" / "Enter". */
@Composable
private fun SummaryEntry(
    r: SecretRequest,
    onEnter: () -> Unit,
    onCancel: () -> Unit,
) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.Top) {
        Icon(
            if (r.isCard) KeloIcons.Card else KeloIcons.Shield,
            contentDescription = null,
            tint = c.ink,
            modifier = Modifier.padding(top = 2.dp).size(20.dp),
        )
        Spacer(Modifier.size(8.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                when {
                    r.isCard -> string(Res.string.secret_title_card)
                    r.isPin -> string(Res.string.pin_secret_title, r.hosts.firstOrNull() ?: r.name)
                    r.isLogin -> string(Res.string.secret_title_login, r.label.ifEmpty { r.hosts.firstOrNull().orEmpty() })
                    else -> string(Res.string.secret_title_key, r.label.ifEmpty { r.name })
                },
                fontWeight = FontWeight.Bold,
                color = c.ink,
                style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.semantics { heading() },
            )
            val hosts = SecretRules.hostsText(r.hosts)
            if (hosts.isNotEmpty()) {
                MutedText(hosts, small = true)
            }
        }
        LinkButton(string(Res.string.cancel), onClick = onCancel)
    }
    PrimaryButton(
        string(Res.string.secret_enter),
        onClick = onEnter,
        modifier = Modifier.fillMaxWidth(),
    )
}

/** The one line an answered entry leaves. */
@Composable
private fun FoldedEntry(r: SecretRequest) {
    val c = Kelo.colors
    val (icon, text, muted) = when (r.state) {
        SecretRequest.STATE_SAVED -> Triple(
            KeloIcons.Shield,
            when {
                r.isCard -> string(Res.string.secret_done_card, r.result?.card?.label?.ifEmpty { null } ?: r.label)
                r.isPin -> string(Res.string.pin_secret_done, r.hosts.firstOrNull() ?: r.name)
                r.isLogin -> string(Res.string.secret_done_login, r.label.ifEmpty { r.name })
                r.result?.checked == "ok" -> string(Res.string.secret_done_checked, r.label.ifEmpty { r.name })
                else -> string(Res.string.secret_done_key, r.label.ifEmpty { r.name })
            },
            false,
        )
        SecretRequest.STATE_CANCELLED -> Triple(KeloIcons.Close, string(Res.string.secret_cancelled), true)
        else -> Triple(KeloIcons.Clock, string(Res.string.secret_gone), true)
    }
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(icon, contentDescription = null, tint = if (muted) c.muted else c.ink, modifier = Modifier.size(16.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = if (muted) c.muted else c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun OpenEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onCancel: () -> Unit, onGone: () -> Unit) {
    // a key, password or card number is typed here: keep the window out of screenshots and the recent-apps preview
    chat.kelo.app.ui.platform.LocalSecureWindow.current()
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.Top) {
        Icon(if (r.isCard) KeloIcons.Card else KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.padding(top = 2.dp).size(20.dp))
        Spacer(Modifier.size(8.dp))
        Text(
            when {
                r.isCard -> string(Res.string.secret_title_card)
                r.isPin -> string(Res.string.pin_secret_title, r.hosts.firstOrNull() ?: r.name)
                r.isLogin -> string(Res.string.secret_title_login, r.label.ifEmpty { r.hosts.firstOrNull().orEmpty() })
                else -> string(Res.string.secret_title_key, r.label.ifEmpty { r.name })
            },
            fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f).semantics { heading() },
        )
        // Cancel in the header, not a line of its own: the card stays short
        LinkButton(string(Res.string.cancel), onClick = onCancel)
    }
    // what the card is for (a card's label already says it)
    if (r.reason.isNotBlank() && r.reason != r.label) Text(r.reason, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    else if (r.isCard && r.label.isNotBlank()) Text(r.label, style = MaterialTheme.typography.bodyMedium, color = c.ink)
    // where the value goes
    val hosts = SecretRules.hostsText(r.hosts)
    MutedText(
        when {
            r.isPin -> string(Res.string.pin_secret_where)
            r.isCard && hosts.isNotEmpty() -> string(Res.string.secret_card_site, hosts)
            r.isCard -> string(Res.string.secret_card_wallet)
            else -> string(Res.string.secret_hosts_only, hosts)
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
}

/** A key or token: one masked field with Show. Tried once before it is saved when [SecretRequest.check]. */
@Composable
private fun KeyEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = rememberViewModel(r.id) { SecretEntryViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    val haptics = LocalHaptics.current
    var value by remember(r.id) { mutableStateOf("") }
    val busy = ui.busy
    val problem = ui.problem.text()
    fun save() = vm.saveKey(r.name, value) { outcome -> if (outcome is SecretOutcome.Saved) value = ""; haptics.done(outcome); outcome.deliver(onSaved, onGone) }

    val scope = rememberCoroutineScope()
    val fieldBring = remember { BringIntoViewRequester() }
    val submitBring = remember { BringIntoViewRequester() }

    SecretField(
        string(Res.string.secret_field_key), value, { value = it }, keyboard = KeyboardType.Password,
        imeAction = SecretEntryRules.keyImeAction(),
        keyboardActions = KeyboardActions(onDone = { if (!busy && SecretEntryRules.keyReady(value)) save() }),
        modifier = Modifier
            .bringIntoViewRequester(fieldBring)
            .onFocusChanged {
                if (it.isFocused) {
                    scope.launch {
                        fieldBring.bringIntoView()
                        submitBring.bringIntoView()
                    }
                }
            },
    )
    if (r.check) MutedText(string(Res.string.secret_check_note), small = true)
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
    PrimaryButton(
        string(if (busy) (if (r.check) Res.string.secret_checking else Res.string.vault_saving) else Res.string.save),
        onClick = ::save, enabled = !busy && SecretEntryRules.keyReady(value),
        modifier = Modifier.bringIntoViewRequester(submitBring),
    )
    MutedText(string(Res.string.secret_never_chat), small = true)
}

/** A website login: user name and password (Show), and how it is kept (by default only once a sign-in with it works). */
@Composable
private fun LoginEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = rememberViewModel(r.id) { SecretEntryViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    val haptics = LocalHaptics.current
    val focusManager = LocalFocusManager.current
    var user by remember(r.id) { mutableStateOf("") }
    var pass by remember(r.id) { mutableStateOf("") }
    var mode by rememberSaveable(r.id) { mutableStateOf(VaultMode.AFTER_SUCCESS) }
    val busy = ui.busy
    val problem = ui.problem.text()
    fun save() = vm.saveLogin(r.name, user, pass, mode) { outcome ->
        if (outcome is SecretOutcome.Saved) { user = ""; pass = "" }
        haptics.done(outcome); outcome.deliver(onSaved, onGone)
    }

    val scope = rememberCoroutineScope()
    val userBring = remember { BringIntoViewRequester() }
    val passBring = remember { BringIntoViewRequester() }
    val submitBring = remember { BringIntoViewRequester() }
    val actions = SecretEntryRules.loginImeActions()

    SecretField(
        string(Res.string.vault_username), user, { user = it }, keyboard = KeyboardType.Email, masked = false,
        imeAction = actions[0],
        keyboardActions = KeyboardActions(onNext = { focusManager.moveFocus(FocusDirection.Down) }),
        modifier = Modifier
            .bringIntoViewRequester(userBring)
            .onFocusChanged {
                if (it.isFocused) {
                    scope.launch {
                        userBring.bringIntoView()
                        submitBring.bringIntoView()
                    }
                }
            },
    )
    SecretField(
        string(Res.string.vault_password), pass, { pass = it }, keyboard = KeyboardType.Password,
        imeAction = actions[1],
        keyboardActions = KeyboardActions(onDone = { if (!busy && SecretEntryRules.loginReady(user, pass)) save() }),
        modifier = Modifier
            .bringIntoViewRequester(passBring)
            .onFocusChanged {
                if (it.isFocused) {
                    scope.launch {
                        passBring.bringIntoView()
                        submitBring.bringIntoView()
                    }
                }
            },
    )
    Text(string(Res.string.vault_mode), style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
    ModePicker(mode) { mode = it }
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
    PrimaryButton(
        string(if (busy) Res.string.vault_saving else Res.string.save), onClick = ::save,
        enabled = !busy && SecretEntryRules.loginReady(user, pass),
        modifier = Modifier.bringIntoViewRequester(submitBring),
    )
    MutedText(string(Res.string.secret_never_chat), small = true)
}

/** A payment card: the wallet's own card form (number, expiry and CVC, nickname, the notice), sealed on this phone; then the request
 *  is answered with the new card (its nickname and last four digits). */
@Composable
private fun CardEntry(r: SecretRequest, onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = rememberViewModel(r.id) { SecretEntryViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    val problem = ui.problem.text()
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        AddCardForm(inline = true, onSaved = { card ->
            // the card is in the wallet either way; an entry that is gone just folds
            vm.cardSaved(r.id, SecretCard(card.id, card.label, card.brand, card.last4)) { outcome -> outcome.deliver(onSaved, onGone) }
        })
    }
    if (problem.isNotEmpty()) ErrorText(problem, small = true)
}

/** The line a problem shows: the server's words, or the card's own "could not reach" line. */
@Composable
private fun SecretProblem?.text(): String = when {
    this == null -> ""
    network -> string(Res.string.secret_failed)
    else -> detail
}

private fun Haptics.done(outcome: SecretOutcome) { if (outcome is SecretOutcome.Saved) confirm() else reject() }

private fun SecretOutcome.deliver(onSaved: (SaveAnswer, SecretResult?) -> Unit, onGone: () -> Unit) {
    when (this) {
        is SecretOutcome.Saved -> onSaved(answer, result)
        SecretOutcome.Gone -> onGone()
        is SecretOutcome.Failed -> {}
    }
}

/** A labelled field; [masked] values show as dots until Show is pressed (the system's own paste works in it). */
@Composable
private fun SecretField(
    label: String,
    value: String,
    onChange: (String) -> Unit,
    keyboard: KeyboardType,
    masked: Boolean = true,
    imeAction: ImeAction = ImeAction.Default,
    keyboardActions: KeyboardActions = KeyboardActions.Default,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    var shown by remember { mutableStateOf(false) }
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(label, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = c.ink)
        // no placeholder: the label is right above, and the same words inside the empty field showed them twice ("키 또는 토큰")
        KeloTextField(
            value, onChange, "", modifier = Modifier.semantics { contentDescription = label }, maxLength = 4096, password = masked && !shown,
            keyboardOptions = KeyboardOptions(
                autoCorrectEnabled = false,
                keyboardType = if (shown && masked) KeyboardType.Ascii else keyboard,
                imeAction = imeAction,
            ),
            keyboardActions = keyboardActions,
            trailingIcon = if (masked) {
                {
                    val what = string(if (shown) Res.string.secret_hide else Res.string.secret_show)
                    IconButton(onClick = { shown = !shown }) {
                        Icon(if (shown) KeloIcons.Close else KeloIcons.Eye, contentDescription = what, tint = c.muted, modifier = Modifier.size(20.dp))
                    }
                }
            } else null,
        )
    }
}
