package chat.kelo.app.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.theme.Kelo

/**
 * The one question before chats first go to an AI service ([AiConsent]): what goes where, the service chosen now, the privacy
 * policy, and Agree / Not now. Dismissing it is Not now: nothing is sent.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ConsentSheet(ask: ConsentAsk, busy: Boolean, error: String?, onAgree: () -> Unit, onNotNow: () -> Unit) {
    val c = Kelo.colors
    val uri = LocalUriHandler.current
    ModalBottomSheet(
        onDismissRequest = { if (!busy) onNotNow() },
        sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(),
        containerColor = c.card,
        modifier = Modifier.testTag("ai_consent"),
    ) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding()
                .padding(start = 20.dp, end = 20.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(stringResource(R.string.ai_consent_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                modifier = Modifier.semantics { heading() })
            Text(stringResource(R.string.ai_consent_body), style = MaterialTheme.typography.bodyMedium, color = c.ink)
            if (ask.need.company.isNotBlank()) MutedText(stringResource(R.string.ai_consent_now, ask.need.company), small = true)
            MutedText(stringResource(R.string.ai_consent_not_now_note), small = true)
            LinkButton(stringResource(R.string.ai_consent_privacy), onClick = { runCatching { uri.openUri(ask.need.privacyUrl) } })
            error?.let { ErrorText(it) }
            PrimaryButton(stringResource(R.string.ai_consent_agree), onClick = onAgree, enabled = !busy, modifier = Modifier.fillMaxWidth())
            SecondaryButton(stringResource(R.string.ai_consent_not_now), onClick = onNotNow, enabled = !busy, modifier = Modifier.fillMaxWidth())
        }
    }
}

/** "Your chats go to Google" under the chosen provider (model picker, Model page); nothing when it cannot be told. */
@Composable
fun ChatsGoLine(provider: String?, company: String?, modifier: Modifier = Modifier) {
    val text = when (val go = AiConsent.chatsGo(provider, company)) {
        is ChatsGo.To -> stringResource(R.string.ai_chats_go_to, go.company)
        ChatsGo.Local -> stringResource(R.string.ai_chats_stay)
        null -> return
    }
    MutedText(text, small = true, modifier = modifier.testTag("chats_go"))
}
