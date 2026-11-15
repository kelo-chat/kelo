package chat.kelo.app.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.connectors.KeyForm
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ChoiceChip
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlin.coroutines.cancellation.CancellationException

/**
 * The sheet for a message the server held back because it holds a key or password ([SecretGuard]). First it asks: Save in Keychain,
 * Send as is (the same text with "secrets": "allow"), or Edit (back to the composer, text intact). Save opens its second step in the
 * same sheet: the value from the message already filled in (hidden, with Show), what it is for, and a name or site guessed from the
 * server's label; Save there goes straight from the app to the vault. A card number goes on to the wallet's card form instead.
 * The value lives only in this sheet's memory and goes with it.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SecretSheet(
    ask: SecretAsk, onSaved: (value: String, reference: String) -> Unit, onCard: () -> Unit, onSendAsIs: () -> Unit, onEdit: () -> Unit,
) {
    val c = Kelo.colors
    var saving by remember(ask) { mutableStateOf(false) }
    var busy by remember(ask) { mutableStateOf(false) }
    val uses = remember(ask) { SecretGuard.uses(ask.found) }
    ModalBottomSheet(
        onDismissRequest = { if (!busy) onEdit() },
        sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(),
        containerColor = c.card,
    ) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).imePadding().navigationBarsPadding()
                .padding(start = 20.dp, end = 20.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (!saving) {
                Text(stringResource(R.string.secret_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                    modifier = Modifier.semantics { heading() })
                MutedText(stringResource(R.string.secret_body), small = true)
                MutedText(stringResource(R.string.secret_body_save), small = true)
                PrimaryButton(stringResource(R.string.secret_save), onClick = { if (uses == listOf(SecretUse.CARD)) onCard() else saving = true })
                SecondaryButton(stringResource(R.string.secret_send_as_is), onClick = onSendAsIs, modifier = Modifier.fillMaxWidth())
                SecondaryButton(stringResource(R.string.secret_edit), onClick = onEdit, modifier = Modifier.fillMaxWidth())
            } else {
                SaveStep(ask, uses, onBusy = { busy = it }, onSaved = onSaved, onCard = onCard, onBack = { saving = false })
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SaveStep(
    ask: SecretAsk, uses: List<SecretUse>, onBusy: (Boolean) -> Unit, onSaved: (String, String) -> Unit, onCard: () -> Unit, onBack: () -> Unit,
) {
    chat.kelo.app.ui.SecureWindow()   // the value is on screen: out of screenshots and the recent-apps preview
    val c = Kelo.colors
    val sessions = LocalAppContainer.current.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val found = ask.found
    // memory only (remember, never rememberSaveable): the value goes when the sheet goes
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
                onSaved(original, context.getString(R.string.secret_saved_ref, name.ifBlank { context.getString(useText(use)) }))
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                err = if (e.isNetwork) context.getString(R.string.agent_offline) else e.detail   // not OkHttp's "Failed to connect to /10.0.2.2:…"
            } catch (e: Exception) {
                err = e.message ?: e.javaClass.simpleName
            } finally {
                busy = false; onBusy(false)
            }
        }
    }

    Text(stringResource(R.string.secret_save), style = MaterialTheme.typography.titleMedium, color = c.ink, modifier = Modifier.semantics { heading() })
    if (uses.size > 1) {
        Text(stringResource(R.string.secret_for), color = c.ink, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            uses.forEach { u -> ChoiceChip(stringResource(useText(u)), selected = use == u, onClick = { use = u; err = "" }) }
        }
    }
    if (use == SecretUse.CARD) {
        MutedText(stringResource(R.string.secret_card_help), small = true)
        PrimaryButton(stringResource(R.string.secret_card_next), onClick = onCard)
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
        } ?: chat.kelo.app.ui.components.SkeletonLines(1)
        SecretUse.LOGIN -> {
            Label(stringResource(R.string.vault_login_site))
            KeloTextField(site, { site = it }, stringResource(R.string.vault_login_site_placeholder),
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Uri))
            Label(stringResource(R.string.vault_username))
            KeloTextField(user, { user = it }, stringResource(R.string.vault_username), maxLength = 200,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email))
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
        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
        trailingIcon = {
            IconButton(onClick = { shown = !shown }) {
                Icon(KeloIcons.Eye, contentDescription = stringResource(if (shown) R.string.secret_hide else R.string.secret_show),
                    tint = if (shown) c.accent else c.muted, modifier = Modifier.size(20.dp))
            }
        },
    )
    if (err.isNotEmpty()) ErrorText(err, small = true)
    PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.save), onClick = ::save, enabled = ready)
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
