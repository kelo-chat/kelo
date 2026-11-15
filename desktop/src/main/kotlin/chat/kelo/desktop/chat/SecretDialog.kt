package chat.kelo.desktop.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.SecretGuard
import chat.kelo.app.chat.SecretSave
import chat.kelo.app.chat.SecretUse
import chat.kelo.app.connectors.KeyForm
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch
import kotlin.coroutines.cancellation.CancellationException

/**
 * The phone's secret sheet (android .../chat/SecretSheet.kt) as a dialog: a message the server held back because it holds a key or
 * password. Save in Keychain (a second step with the value already filled in, hidden, with Show; saved straight from the app to the
 * vault), Send as is (the same text with "secrets": "allow"), or Edit (back to the composer, text intact). Cards are added in the
 * phone app (the desktop never holds a card's key), so the card step says so. The value lives only in this dialog's memory.
 */
@Composable
fun SecretDialog(
    sessions: Sessions, ask: SecretAsk, onSaved: (value: String, reference: String) -> Unit, onSendAsIs: () -> Unit, onEdit: () -> Unit,
) {
    val c = Kelo.colors
    var saving by remember(ask) { mutableStateOf(false) }
    var busy by remember(ask) { mutableStateOf(false) }
    val uses = remember(ask) { SecretGuard.uses(ask.found) }
    AlertDialog(
        onDismissRequest = { if (!busy) onEdit() }, containerColor = c.card,
        title = { Text(stringResource(if (saving) R.string.secret_save else R.string.secret_title), color = c.ink) },
        text = {
            Column(Modifier.widthIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                if (!saving) {
                    MutedText(stringResource(R.string.secret_body), small = true)
                    MutedText(stringResource(R.string.secret_body_save), small = true)
                    PrimaryButton(stringResource(R.string.secret_save), onClick = { saving = true }, modifier = Modifier.fillMaxWidth())
                    SecondaryButton(stringResource(R.string.secret_send_as_is), onClick = onSendAsIs, modifier = Modifier.fillMaxWidth())
                    SecondaryButton(stringResource(R.string.secret_edit), onClick = onEdit, modifier = Modifier.fillMaxWidth())
                } else {
                    SaveStep(sessions, ask, uses, onBusy = { busy = it }, onSaved = onSaved, onBack = { saving = false })
                }
            }
        },
        confirmButton = {},
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SaveStep(sessions: Sessions, ask: SecretAsk, uses: List<SecretUse>, onBusy: (Boolean) -> Unit, onSaved: (String, String) -> Unit, onBack: () -> Unit) {
    val c = Kelo.colors
    val strings = LocalStrings.current
    val scope = rememberCoroutineScope()
    val found = ask.found
    val original = remember(ask) { ask.values.firstOrNull().orEmpty() }
    var value by remember(ask) { mutableStateOf(original) }
    var shown by remember(ask) { mutableStateOf(false) }
    var use by remember(ask) { mutableStateOf(uses.first()) }
    var label by remember(ask) { mutableStateOf(found.label) }
    var host by remember(ask) { mutableStateOf(SecretGuard.hostGuess(found.label)) }
    var site by remember(ask) { mutableStateOf("") }
    var user by remember(ask) { mutableStateOf("") }
    var form by remember(ask) { mutableStateOf<KeyForm?>(null) }
    val others = remember(ask) { mutableStateMapOf<String, String>() }
    var err by remember(ask) { mutableStateOf("") }
    var busy by remember(ask) { mutableStateOf(false) }

    LaunchedEffect(use) {
        if (use == SecretUse.CONNECTOR_KEY && form == null) {
            form = try { SecretSave.connectorForm(sessions.requireApi(), found.target.id) } catch (e: CancellationException) { throw e } catch (e: Exception) { null }
        }
    }
    val connector = form
    val name = when (use) {
        SecretUse.AI_KEY -> found.label.ifBlank { found.target.id }
        SecretUse.CONNECTOR_KEY -> connector?.name?.ifBlank { null } ?: found.label.ifBlank { found.target.id }
        SecretUse.LOGIN -> label.trim().ifBlank { site.trim() }
        SecretUse.SERVICE_KEY -> label.trim().ifBlank { host.trim() }
        SecretUse.CARD -> ""
    }
    val ready = !busy && value.trim().length >= (if (use == SecretUse.LOGIN) 1 else 8) && when (use) {
        SecretUse.AI_KEY -> found.target.id.isNotEmpty()
        SecretUse.CONNECTOR_KEY -> connector != null && connector.fields.filter { !it.secret }.all { others[it.id].orEmpty().isNotBlank() }
        SecretUse.LOGIN -> site.isNotBlank() && user.isNotBlank()
        SecretUse.SERVICE_KEY -> host.isNotBlank()
        SecretUse.CARD -> false
    }

    fun save() {
        busy = true; onBusy(true); err = ""
        val v = value
        scope.launch {
            try {
                val api = sessions.requireApi()
                when (use) {
                    SecretUse.AI_KEY -> SecretSave.aiKey(api, found.target.id, v)
                    SecretUse.CONNECTOR_KEY -> SecretSave.connectorKey(api, connector ?: return@launch, v, others.toMap())
                    SecretUse.LOGIN -> SecretSave.login(api, site, label, user, v)
                    SecretUse.SERVICE_KEY -> SecretSave.serviceKey(api, host, label, v)
                    SecretUse.CARD -> return@launch
                }
                value = ""
                onSaved(original, strings.get(R.string.secret_saved_ref, name.ifBlank { strings.get(useText(use)) }))
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                err = if (e.isNetwork) strings.get(R.string.agent_offline) else e.detail   // not the transport's own words
            } catch (e: Exception) {
                err = e.message ?: e.javaClass.simpleName
            } finally {
                busy = false; onBusy(false)
            }
        }
    }

    if (uses.size > 1) {
        Text(stringResource(R.string.secret_for), color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            uses.forEach { u -> ChoiceChip(stringResource(useText(u)), selected = use == u, onClick = { use = u; err = "" }) }
        }
    }
    if (use == SecretUse.CARD) {
        MutedText(stringResource(R.string.desktop_ops_wallet_note), small = true)   // cards are added in the phone app
        LinkButton(stringResource(R.string.back), onClick = onBack)
        return
    }
    if ((use == SecretUse.AI_KEY || use == SecretUse.CONNECTOR_KEY) && name.isNotBlank()) {
        Text(stringResource(R.string.secret_key_for, name), color = c.ink, style = MaterialTheme.typography.bodyMedium)
    }
    when (use) {
        SecretUse.CONNECTOR_KEY -> connector?.fields?.filter { !it.secret }?.forEach { f ->
            Label(f.label.ifBlank { f.id })
            KeloTextField(others[f.id].orEmpty(), { others[f.id] = it }, f.placeholder.ifBlank { f.label },
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Ascii))
        }
        SecretUse.LOGIN -> {
            Label(stringResource(R.string.vault_login_site))
            KeloTextField(site, { site = it }, stringResource(R.string.vault_login_site_placeholder),
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri))
            Label(stringResource(R.string.vault_username))
            KeloTextField(user, { user = it }, stringResource(R.string.vault_username), maxLength = 200)
            Label(stringResource(R.string.secret_label))
            KeloTextField(label, { label = it }, stringResource(R.string.vault_login_name_placeholder), maxLength = 80)
        }
        SecretUse.SERVICE_KEY -> {
            Label(stringResource(R.string.secret_label))
            KeloTextField(label, { label = it }, stringResource(R.string.secret_label), maxLength = 80)
            Label(stringResource(R.string.secret_host))
            KeloTextField(host, { host = it }, stringResource(R.string.secret_host_hint),
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri))
        }
        else -> Unit
    }
    Label(stringResource(if (use == SecretUse.LOGIN) R.string.vault_password else R.string.secret_value))
    KeloTextField(
        value, { value = it }, stringResource(R.string.secret_value), password = !shown, maxLength = 4096,
        trailingIcon = {
            IconButton(onClick = { shown = !shown }) {
                Icon(KeloIcons.Eye, contentDescription = stringResource(if (shown) R.string.secret_hide else R.string.secret_show),
                    tint = if (shown) c.accent else c.muted, modifier = Modifier.size(20.dp))
            }
        },
    )
    if (err.isNotEmpty()) ErrorText(err, small = true)
    PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.save), onClick = ::save, modifier = Modifier.fillMaxWidth(), enabled = ready)
    LinkButton(stringResource(R.string.back), onClick = onBack)
}

@Composable
private fun Label(text: String) =
    Text(text, color = Kelo.colors.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodySmall)

private fun useText(u: SecretUse): Int = when (u) {
    SecretUse.AI_KEY -> R.string.secret_use_ai
    SecretUse.CONNECTOR_KEY -> R.string.secret_use_connector
    SecretUse.CARD -> R.string.secret_use_card
    SecretUse.LOGIN -> R.string.secret_use_login
    SecretUse.SERVICE_KEY -> R.string.secret_use_service
}
