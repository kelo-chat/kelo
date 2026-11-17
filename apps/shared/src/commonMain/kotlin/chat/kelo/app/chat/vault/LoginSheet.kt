@file:OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)

package chat.kelo.app.chat.vault

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.autofill.AutofillType
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.platform.LocalPasswordApp
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.LocalSecureWindow
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/** The compact card's head: key icon, the title and "for <site>" under it. Shared by the card and the sheet. */
@Composable
internal fun LoginHead(login: Boolean, forName: String, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
        Icon(KeloIcons.Key, contentDescription = null, tint = c.ink, modifier = Modifier.padding(top = 2.dp).size(20.dp))
        androidx.compose.foundation.layout.Spacer(Modifier.size(10.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(
                string(if (login) Res.string.secret_card_title_login else Res.string.secret_card_title_key),
                fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge,
                modifier = Modifier.semantics { heading() },
            )
            if (forName.isNotBlank()) MutedText(string(Res.string.secret_card_for, forName), small = true)
        }
    }
}

/**
 * The compact card a login or key request shows in the chat: [LoginHead], ONE button that opens the sheet, and a small "Not now"
 * link ([onNotNow]) that forgets the request.
 */
@Composable
internal fun LoginCompact(login: Boolean, forName: String, onOpen: () -> Unit, onNotNow: () -> Unit) {
    LoginHead(login, forName)
    PrimaryButton(string(Res.string.secret_enter), onClick = onOpen, modifier = Modifier.fillMaxWidth())
    LinkButton(string(Res.string.secret_not_now), onClick = onNotNow)
}

/**
 * The bottom sheet (a dialog on the desktop) where a login or a key is typed: key icon, title, "for <site>", the user name already
 * filled with [hint] (editable), the password with Show, one checkbox "Save in the Keychain" (checked; a key has none), Save
 * (enabled when the fields are filled) and Cancel, which only closes the sheet ([onClose]). The values leave only through [onSave].
 */
@Composable
internal fun LoginSheet(
    login: Boolean,
    forName: String,
    hint: String,
    busy: Boolean,
    problem: String,
    note: String,
    onSave: (user: String, pass: String, keep: Boolean) -> Unit,
    onClose: () -> Unit,
) {
    // a password or key is typed here: keep the window out of screenshots and the recent-apps preview
    LocalSecureWindow.current()
    val key = forName + login
    var user by remember(key) { mutableStateOf(LoginSheetRules.prefill(login, hint)) }
    var pass by remember(key) { mutableStateOf("") }
    var keep by rememberSaveable(key) { mutableStateOf(true) }
    val focus = LocalFocusManager.current
    val passwordApp = LocalPasswordApp.current
    val first = remember { FocusRequester() }
    // the field still empty takes the focus at once, so a password app's suggestion shows up without a tap
    LaunchedEffect(key) { runCatching { first.requestFocus() } }
    val ready = LoginSheetRules.canSave(login, user, pass, busy)
    fun save() { if (ready) onSave(user, pass, keep) }

    KeloModal(onDismiss = onClose) {
        Column(
            Modifier.fillMaxWidth().imePadding().navigationBarsPadding().verticalScroll(rememberScrollState())
                .padding(start = 20.dp, end = 20.dp, bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            LoginHead(login, forName)
            if (login) {
                // only for phones that have a password app set up as the autofill service (nothing to explain otherwise)
                if (remember { passwordApp?.available == true }) {
                    SecondaryButton(string(Res.string.vault_fill_from_app), onClick = { runCatching { first.requestFocus() }; passwordApp?.request() }, modifier = Modifier.fillMaxWidth())
                }
                SecretField(
                    string(Res.string.vault_username), user, { user = it }, keyboard = KeyboardType.Email, masked = false,
                    imeAction = SecretEntryRules.loginImeActions()[0],
                    keyboardActions = KeyboardActions(onNext = { focus.moveFocus(FocusDirection.Down) }),
                    fieldModifier = Modifier.focusRequester(first).autofill(listOf(AutofillType.Username, AutofillType.EmailAddress)) { user = it },
                )
            }
            SecretField(
                string(if (login) Res.string.vault_password else Res.string.secret_field_key), pass, { pass = it }, keyboard = KeyboardType.Password,
                imeAction = SecretEntryRules.keyImeAction(),
                keyboardActions = KeyboardActions(onDone = { save() }),
                fieldModifier = if (login) Modifier.autofill(listOf(AutofillType.Password)) { pass = it } else Modifier.focusRequester(first),
            )
            if (note.isNotEmpty()) MutedText(note, small = true)
            if (login) KeepRow(keep) { keep = it }
            if (problem.isNotEmpty()) ErrorText(problem, small = true)
            PrimaryButton(string(if (busy) Res.string.vault_saving else Res.string.save), onClick = ::save, enabled = ready, modifier = Modifier.fillMaxWidth())
            LinkButton(string(Res.string.cancel), onClick = onClose, modifier = Modifier.align(Alignment.CenterHorizontally))
        }
    }
}

/** The one checkbox; the whole row toggles it, and a long label wraps. */
@Composable
private fun KeepRow(keep: Boolean, onChange: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Checkbox) { onChange(!keep) },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Checkbox(
            checked = keep, onCheckedChange = null,
            colors = CheckboxDefaults.colors(checkedColor = c.accent, uncheckedColor = c.muted, checkmarkColor = c.card),
        )
        Text(string(Res.string.secret_save_in_vault), style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
    }
}
