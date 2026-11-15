package chat.kelo.app.chat.vault

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.autofill.AutofillNode
import androidx.compose.ui.autofill.AutofillType
import androidx.compose.ui.composed
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalAutofill
import androidx.compose.ui.platform.LocalAutofillTree
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ChoiceChip
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.emphasized
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/**
 * The form where a login or a key is typed. The value goes to the server and is never shown again;
 * the assistant never sees it. A login form is marked for Android autofill (username / password), so 1Password and other
 * password managers can fill it; a service key is never offered for autofill.
 *
 * Used on the chat's secure card and (later) in 마이 → 안전한 보관함.
 */
@OptIn(ExperimentalLayoutApi::class, ExperimentalComposeUiApi::class)
@Composable
fun SecureEntryForm(item: VaultItem, onSaved: (VaultMode) -> Unit, defaultMode: VaultMode = VaultMode.NOW) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var user by remember(item.name) { mutableStateOf("") }
    var pass by remember(item.name) { mutableStateOf("") }
    var mode by rememberSaveable(item.name) { mutableStateOf(defaultMode) }
    var msg by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    val login = item.isLogin
    // a password or key being typed: keep the window out of screenshots and the recent-apps preview
    chat.kelo.app.ui.SecureWindow()
    // the first field takes the focus at once, so a password app's suggestion shows up without a tap
    val first = remember { androidx.compose.ui.focus.FocusRequester() }
    val view = androidx.compose.ui.platform.LocalView.current
    androidx.compose.runtime.LaunchedEffect(item.name) { runCatching { first.requestFocus() } }
    fun askPasswordApp() {
        runCatching { first.requestFocus() }
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            runCatching { view.context.getSystemService(android.view.autofill.AutofillManager::class.java)?.requestAutofill(view) }
        }
    }

    fun submit() {
        busy = true; msg = ""
        scope.launch {
            try {
                val api = sessions.requireApi()
                if (login) VaultCalls.putLogin(api, item.name, user.trim(), pass, mode) else VaultCalls.putSecret(api, item.name, pass.trim())
                user = ""; pass = ""
                onSaved(mode)
            } catch (e: ApiError) {
                msg = e.detail
            } finally {
                busy = false
            }
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (login) {
            // only for phones that have a password app set up as the autofill service (nothing to explain otherwise)
            val hasPasswordApp = remember {
                android.os.Build.VERSION.SDK_INT >= 26 && runCatching {
                    view.context.getSystemService(android.view.autofill.AutofillManager::class.java)
                        ?.let { it.isEnabled && it.hasEnabledAutofillServices() } == true
                }.getOrDefault(false)
            }
            if (hasPasswordApp) {
                chat.kelo.app.ui.components.SecondaryButton(stringResource(R.string.vault_fill_from_app), onClick = ::askPasswordApp, modifier = Modifier.fillMaxWidth())
            }
            FieldLabel(stringResource(R.string.vault_username))
            KeloTextField(
                user, { user = it }, stringResource(R.string.vault_username), maxLength = 200,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email),
                modifier = Modifier.focusRequester(first).autofill(listOf(AutofillType.Username, AutofillType.EmailAddress)) { user = it },
            )
        }
        FieldLabel(stringResource(if (login) R.string.vault_password else R.string.vault_key))
        KeloTextField(
            pass, { pass = it }, stringResource(if (login) R.string.vault_password else R.string.vault_key),
            password = true, maxLength = 512,
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
            modifier = if (login) Modifier.autofill(listOf(AutofillType.Password)) { pass = it } else Modifier.focusRequester(first),
        )
        if (login) {
            FieldLabel(stringResource(R.string.vault_mode))
            ModePicker(mode) { mode = it }
        }
        val ready = pass.isNotEmpty() && (if (login) user.isNotBlank() else pass.trim().length >= 8)
        PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.vault_save), onClick = ::submit, enabled = !busy && ready)
        if (msg.isNotEmpty()) ErrorText(msg, small = true)
        MutedText(stringResource(R.string.vault_footer), small = true)
    }
}

/** The three ways to keep a login, as one grouped list: a radio per row, the chosen row's explanation under its name. */
@Composable
internal fun ModePicker(mode: VaultMode, onPick: (VaultMode) -> Unit) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).border(1.dp, c.line, RoundedCornerShape(14.dp)).selectableGroup()) {
        VaultMode.entries.forEachIndexed { i, m ->
            if (i > 0) androidx.compose.material3.HorizontalDivider(color = c.line)
            val on = mode == m
            Row(
                Modifier.fillMaxWidth().selectable(selected = on, role = androidx.compose.ui.semantics.Role.RadioButton) { onPick(m) }
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                verticalAlignment = Alignment.Top,
            ) {
                androidx.compose.material3.RadioButton(
                    selected = on, onClick = null,
                    colors = androidx.compose.material3.RadioButtonDefaults.colors(selectedColor = c.accent, unselectedColor = c.muted),
                    modifier = Modifier.padding(top = 2.dp),
                )
                Spacer(Modifier.size(10.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(stringResource(modeText(m)), style = MaterialTheme.typography.bodyMedium, color = c.ink,
                         fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal)
                    if (on) Text(stringResource(modeHelp(m)), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
            }
        }
    }
}

@Composable
private fun FieldLabel(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
}

private fun modeText(m: VaultMode) = when (m) {
    VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success
    VaultMode.NOW -> R.string.vault_mode_now
    VaultMode.ONCE -> R.string.vault_mode_once
}

private fun modeHelp(m: VaultMode) = when (m) {
    VaultMode.AFTER_SUCCESS -> R.string.vault_mode_after_success_help
    VaultMode.NOW -> R.string.vault_mode_now_help
    VaultMode.ONCE -> R.string.vault_mode_once_help
}

/** "로그인 정보" / "키" phrase for an item: "<label> 로그인 정보" or "<label> 키". */
@Composable
fun vaultWhat(item: VaultItem): String =
    stringResource(if (item.isLogin) R.string.vault_what_login else R.string.vault_what_key, item.label)

/**
 * The card that appears in the chat when the assistant asks for a login or a key. [onNotify] sends a message to
 * the agent ("…를 보관함에 저장했어요. 이어서 진행해 줘.").
 */
@Composable
fun SecureCard(name: String, onNotify: ((String) -> Unit)?, modifier: Modifier = Modifier) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var item by remember(name) { mutableStateOf<VaultItem?>(null) }
    var done by rememberSaveable(name) { mutableStateOf<VaultMode?>(null) }
    var closed by rememberSaveable(name) { mutableStateOf(false) }
    LaunchedEffect(name) {
        item = try { VaultCalls.find(sessions.requireApi(), name) } catch (_: ApiError) { null }
    }
    val it = item ?: return
    val what = vaultWhat(it)
    val c = Kelo.colors
    CardBox(modifier) {
        if (closed) {
            MutedText(stringResource(R.string.vault_card_cancelled, what), small = true)
            return@CardBox
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.size(20.dp))
            Spacer(Modifier.size(8.dp))
            Text(stringResource(R.string.vault_card_needed, what), fontWeight = FontWeight.Bold, color = c.ink)
        }
        MutedText(stringResource(R.string.vault_card_hosts, it.hosts.joinToString(", ")), small = true)
        val d = done
        if (d != null) {
            Text(
                stringResource(
                    when (d) {
                        VaultMode.AFTER_SUCCESS -> R.string.vault_card_done_after_success
                        VaultMode.ONCE -> R.string.vault_card_done_once
                        VaultMode.NOW -> R.string.vault_card_done_now
                    },
                ),
                style = MaterialTheme.typography.bodySmall, color = c.ink,
            )
            if (onNotify != null) {
                val text = stringResource(if (d == VaultMode.NOW) R.string.vault_card_notify_saved else R.string.vault_card_notify_entered, what)
                PrimaryButton(stringResource(R.string.notify_agent), onClick = { onNotify(text) })
            }
        } else {
            SecureEntryForm(it, onSaved = { m -> done = m }, defaultMode = if (it.isLogin) VaultMode.AFTER_SUCCESS else VaultMode.NOW)
            LinkButton(stringResource(R.string.cancel), onClick = {
                // nothing was entered: the request is forgotten, and nothing is left in the vault
                scope.launch {
                    runCatching { VaultCalls.cancelDraft(sessions.requireApi(), name) }
                    closed = true
                }
            })
        }
    }
}

/** .approval / .secure-card: a full-width card in the chat list. */
@Composable
fun CardBox(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val c = Kelo.colors
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(c.card)
            .border(1.dp, c.line, RoundedCornerShape(16.dp))
            .padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content,
    )
}

/** Marks a field for the Android autofill service (password managers fill it on focus). */
@OptIn(ExperimentalComposeUiApi::class)
private fun Modifier.autofill(types: List<AutofillType>, onFill: (String) -> Unit): Modifier = composed {
    val fill by rememberUpdatedState(onFill)
    val node = remember { AutofillNode(autofillTypes = types, onFill = { fill(it) }) }
    val autofill = LocalAutofill.current
    LocalAutofillTree.current += node
    this
        .onGloballyPositioned { node.boundingBox = it.boundsInWindow() }
        .onFocusChanged { f ->
            autofill?.run { if (f.isFocused) requestAutofillForNode(node) else cancelAutofillForNode(node) }
        }
}
