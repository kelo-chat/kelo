package chat.kelo.desktop.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.AiConsent
import chat.kelo.app.chat.ChatsGo
import chat.kelo.app.chat.ConsentAsk
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.Browser
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton

/**
 * The one question before chats first go to an AI service (chat.kelo.app.chat.AiConsent; Android ConsentSheet): what goes where,
 * the service chosen now, the privacy policy, and Agree / Not now. Closing it is Not now: nothing is sent.
 */
@Composable
fun ConsentDialog(ask: ConsentAsk, busy: Boolean, error: String?, onAgree: () -> Unit, onNotNow: () -> Unit) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = { if (!busy) onNotNow() }, containerColor = c.card,
        title = { Text(stringResource(R.string.ai_consent_title), color = c.ink) },
        text = {
            Column(Modifier.widthIn(max = 520.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(stringResource(R.string.ai_consent_body), color = c.ink, style = MaterialTheme.typography.bodyMedium)
                if (ask.need.company.isNotBlank()) MutedText(stringResource(R.string.ai_consent_now, ask.need.company), small = true)
                MutedText(stringResource(R.string.ai_consent_not_now_note), small = true)
                LinkButton(stringResource(R.string.ai_consent_privacy), onClick = { Browser.open(ask.need.privacyUrl) })
                error?.let { ErrorText(it) }
                PrimaryButton(stringResource(R.string.ai_consent_agree), onClick = onAgree, enabled = !busy, modifier = Modifier.fillMaxWidth())
                SecondaryButton(stringResource(R.string.ai_consent_not_now), onClick = onNotNow, enabled = !busy, modifier = Modifier.fillMaxWidth())
            }
        },
        confirmButton = {},
    )
}

/** "Your chats go to Google" under the chosen provider (model picker, Model page); nothing when it cannot be told. */
@Composable
fun ChatsGoLine(provider: String?, company: String?, modifier: Modifier = Modifier) {
    val text = when (val go = AiConsent.chatsGo(provider, company)) {
        is ChatsGo.To -> stringResource(R.string.ai_chats_go_to, go.company)
        ChatsGo.Local -> stringResource(R.string.ai_chats_stay)
        null -> return
    }
    MutedText(text, small = true, modifier = modifier)
}
