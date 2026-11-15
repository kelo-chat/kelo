package chat.kelo.app.wallet

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
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/**
 * Saving a payment PIN for one site: the site (fixed when the agent asked for a site's PIN), a name, the PIN twice (masked, digits
 * only, 4-8). [저장] asks for the owner's fingerprint / face, encrypts the PIN with this phone's key and sends only that ciphertext.
 * The PIN never goes to the chat, the agent or a log. [onSaved]: the server's answer.
 */
@Composable
fun PinForm(
    site: String = "",
    siteFixed: Boolean = false,
    requestId: String? = null,
    onSaved: (PinSaved) -> Unit,
    modifier: Modifier = Modifier,
) {
    chat.kelo.app.ui.SecureWindow()
    val sessions = LocalAppContainer.current.sessions
    val auth = rememberCardAuthenticator()
    val scope = rememberCoroutineScope()
    var where by remember { mutableStateOf(site) }
    var name by remember { mutableStateOf("") }
    var pin by remember { mutableStateOf("") }
    var again by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var problem by remember { mutableStateOf<CardSendError?>(null) }
    val title = stringResource(R.string.pin_auth_save_title)
    val subtitle = stringResource(R.string.pin_auth_save_subtitle)
    fun save() {
        val secret = PinSecret.of(pin) ?: return
        busy = true; problem = null
        scope.launch {
            val (saved, err) = savePin(sessions.requireApi(), auth, where, name, secret, title, subtitle, requestId)
            busy = false
            if (saved != null) {
                pin = ""; again = ""
                onSaved(saved)
            } else {
                problem = err
            }
        }
    }
    Column(modifier, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        if (!siteFixed) {
            Label(stringResource(R.string.pin_form_site))
            KeloTextField(where, { where = it.take(200) }, stringResource(R.string.pin_form_site_hint),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false))
        }
        Label(stringResource(R.string.pin_form_name))
        KeloTextField(name, { name = it.filterNot { ch -> ch.isDigit() }.take(60) }, stringResource(R.string.pin_form_name_hint))
        Label(stringResource(R.string.pin_form_pin))
        KeloTextField(pin, { pin = PinRules.clean(it) }, stringResource(R.string.pin_form_pin_hint), password = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, autoCorrectEnabled = false))
        KeloTextField(again, { again = PinRules.clean(it) }, stringResource(R.string.pin_form_again), password = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword, autoCorrectEnabled = false))
        if (PinRules.mismatch(pin, again)) ErrorText(stringResource(R.string.pin_form_mismatch), small = true)
        problem?.let { ErrorText(pinErrorText(it), small = true) }
        PrimaryButton(stringResource(if (busy) R.string.vault_saving else R.string.save), onClick = ::save,
            enabled = !busy && PinRules.ready(pin, again, where))
        MutedText(stringResource(R.string.pin_form_note), small = true)
    }
}

@Composable
private fun Label(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
}

@Composable
fun pinErrorText(e: CardSendError): String = stringResource(
    when (e) {
        CardSendError.NO_LOCK -> R.string.chat_card_error_no_lock
        CardSendError.CANCELLED -> R.string.chat_card_error_cancelled
        CardSendError.NETWORK -> R.string.chat_card_error_network
        CardSendError.NOT_ON_PHONE -> R.string.pin_error_not_on_phone
        CardSendError.MISMATCH -> R.string.pin_error_rejected
        CardSendError.NOT_WAITING -> R.string.chat_card_error_not_waiting
        CardSendError.OTHER -> R.string.chat_card_error_other
        CardSendError.KEYGUARD_REQUIRED -> R.string.wallet_keyguard_needed_title
    },
)
