package chat.kelo.app.connectors

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import chat.kelo.app.BuildConfig
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/**
 * Under the "Google sign-in is getting ready" note of Gmail / Google Calendar: ask to be let in early ([BetaRules]). The request (the
 * Google address the person will connect) goes to this server, which forwards it to the Kelo developer and keeps its state, so
 * "Requested" survives reopening. Once the developer approves, GET /api/google/beta says [GoogleBetaState.open] and [onOpened] reloads
 * the screen, which then shows the normal Google sign-in button.
 */
@Composable
internal fun BetaRequest(c: Connector, onOpened: () -> Unit) {
    val product = BetaRules.product(c.id) ?: return
    val sessions = LocalAppContainer.current.sessions
    var state by remember(c.id) { mutableStateOf<GoogleBetaState?>(null) }
    var sheet by rememberSaveable(c.id) { mutableStateOf(false) }
    LaunchedEffect(c.id) {
        state = runCatching { ConnectorCalls.googleBeta(sessions.requireApi()) }.getOrNull()
    }
    val s = state
    when (BetaRules.shown(s)) {
        BetaRules.Shown.REQUEST -> PrimaryButton(stringResource(R.string.beta_request), onClick = { sheet = true })
        BetaRules.Shown.REQUESTED -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SecondaryButton(stringResource(R.string.beta_requested), onClick = {}, enabled = false, modifier = Modifier.fillMaxWidth())
            MutedText(stringResource(R.string.beta_sent, s?.email.orEmpty()), small = true)
        }
        BetaRules.Shown.DECLINED -> MutedText(stringResource(R.string.beta_declined), small = true)
        BetaRules.Shown.OPENED -> LaunchedEffect(Unit) { onOpened() }
        BetaRules.Shown.NOTHING -> {}
    }
    if (sheet) BetaSheet(product, onClose = { sheet = false }, onSent = { state = it; sheet = false })
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BetaSheet(product: String, onClose: () -> Unit, onSent: (GoogleBetaState) -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var email by rememberSaveable { mutableStateOf("") }
    var tried by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    val valid = BetaRules.email(email)
    val send: () -> Unit = {
        tried = true
        if (valid != null && !busy) {
            busy = true; err = ""
            scope.launch {
                try {
                    onSent(ConnectorCalls.requestGoogleBeta(sessions.requireApi(), valid, product, BuildConfig.VERSION_NAME))
                } catch (e: ApiError) {
                    err = e.detail
                } finally {
                    busy = false
                }
            }
        }
    }
    ModalBottomSheet(onDismissRequest = onClose, sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(), containerColor = c.bg) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().imePadding()
                .padding(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(stringResource(R.string.beta_request), style = MaterialTheme.typography.titleMedium, color = c.ink)
            MutedText(stringResource(R.string.beta_sheet_note), small = true)
            KeloTextField(
                email, { email = it; err = "" }, stringResource(R.string.beta_email_hint), maxLength = 254,
                keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email, imeAction = ImeAction.Send),
                keyboardActions = androidx.compose.foundation.text.KeyboardActions(onSend = { send() }),
            )
            if (tried && valid == null) ErrorText(stringResource(R.string.beta_email_invalid), small = true)
            if (err.isNotEmpty()) ErrorText(err, small = true)
            PrimaryButton(stringResource(if (busy) R.string.beta_sending else R.string.beta_send), enabled = !busy && email.isNotBlank(), onClick = send)
        }
    }
}
