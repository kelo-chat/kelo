package chat.kelo.app.followup

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.BorderStroke
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.chat.vault.SecureCard
import chat.kelo.app.chat.vault.VaultCalls
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/** A button's words, from the app's own strings (the server's ids never show). */
@Composable
fun actionLabel(step: FollowupStep): String = when (step) {
    is FollowupStep.OpenLogin -> stringResource(R.string.followup_action_login)
    is FollowupStep.Answer -> stringResource(if (step.approve) R.string.followup_action_approve else R.string.followup_action_deny)
    is FollowupStep.ResumeWorker -> stringResource(R.string.followup_action_resume)
    is FollowupStep.Drop -> stringResource(R.string.followup_action_drop)
    is FollowupStep.Snooze -> stringResource(R.string.followup_action_snooze, laterText(step.minutes))
    is FollowupStep.Retry -> stringResource(R.string.followup_action_retry)
}

/** "30분 뒤" / "2시간 뒤" / "1일 뒤" */
@Composable
fun laterText(minutes: Int): String = when {
    minutes >= 1440 && minutes % 1440 == 0 -> pluralStringResource(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440)
    minutes >= 60 && minutes % 60 == 0 -> pluralStringResource(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60)
    else -> pluralStringResource(R.plurals.agent_rel_minutes_future, minutes, minutes)
}

/** The main step of a set (filled); the others are outlined. */
private fun primary(step: FollowupStep): Boolean =
    step is FollowupStep.OpenLogin || step is FollowupStep.Retry || (step is FollowupStep.Answer && step.approve) || step is FollowupStep.ResumeWorker

/**
 * The buttons under an agent's message or on a card: they wrap onto more lines rather than squeeze (long words in any language
 * stay whole). [busy] shows a small spinner in place of the buttons.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun FollowupButtons(buttons: List<Pair<FollowupAction, FollowupStep>>, busy: Boolean, onPress: (FollowupStep) -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    if (buttons.isEmpty()) return
    if (busy) {
        Row(modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(18.dp), color = c.muted, strokeWidth = 2.dp)
        }
        return
    }
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        // one filled button at most: the first main step; the rest are outlined
        val main = buttons.indexOfFirst { primary(it.second) }
        buttons.forEachIndexed { i, (_, step) ->
            val label = actionLabel(step)
            val pad = PaddingValues(horizontal = 14.dp, vertical = 8.dp)
            if (i == main) {
                Button(
                    onClick = { onPress(step) }, shape = RoundedCornerShape(50), contentPadding = pad,
                    colors = ButtonDefaults.buttonColors(containerColor = c.accent, contentColor = c.onAccent),
                ) { Text(label, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
            } else {
                OutlinedButton(
                    onClick = { onPress(step) }, shape = RoundedCornerShape(50), contentPadding = pad, border = BorderStroke(1.dp, c.line),
                    colors = ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink),
                ) { Text(label, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center) }
            }
        }
    }
}

/**
 * The sign-in sheet for one Kelo keychain item, opened from a follow-up button: the same secure form the chat shows. When it is
 * filled in, [onDone] gets the line that tells the agent (null when the request was already gone).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SignInSheet(name: String, onNotify: (String) -> Unit, onClose: () -> Unit) {
    val c = Kelo.colors
    val sessions = LocalAppContainer.current.sessions
    var state by remember(name) { mutableStateOf(0) }   // 0 looking, 1 found, 2 gone
    LaunchedEffect(name) {
        state = try { if (VaultCalls.find(sessions.requireApi(), name) != null) 1 else 2 } catch (_: ApiError) { 2 }
    }
    ModalBottomSheet(onDismissRequest = onClose, sheetState = chat.kelo.app.ui.components.rememberKeloSheetState(), containerColor = c.bg) {
        Column(Modifier.fillMaxWidth().navigationBarsPadding().padding(start = 16.dp, end = 16.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.size(20.dp))
                Text(stringResource(R.string.signin_sheet_title), style = MaterialTheme.typography.titleMedium, color = c.ink)
            }
            when (state) {
                0 -> CircularProgressIndicator(Modifier.size(22.dp), color = c.muted, strokeWidth = 2.dp)
                1 -> SecureCard(name, onNotify = { text -> onNotify(text); onClose() })
                else -> MutedText(stringResource(R.string.signin_sheet_gone), small = true)
            }
        }
    }
}

/** The line that replaces a message's buttons once one was used. */
@Composable
fun outcomeText(o: chat.kelo.app.chat.ActionOutcome): String = stringResource(
    when (o) {
        chat.kelo.app.chat.ActionOutcome.APPROVED -> R.string.followup_done_approved
        chat.kelo.app.chat.ActionOutcome.DENIED -> R.string.followup_done_denied
        chat.kelo.app.chat.ActionOutcome.RESUMED -> R.string.followup_done_resumed
        chat.kelo.app.chat.ActionOutcome.DROPPED -> R.string.followup_done_dropped
        chat.kelo.app.chat.ActionOutcome.SNOOZED -> R.string.followup_done_snoozed
        chat.kelo.app.chat.ActionOutcome.SIGNED_IN -> R.string.followup_done_signed_in
        chat.kelo.app.chat.ActionOutcome.GONE -> R.string.followup_done_gone
        chat.kelo.app.chat.ActionOutcome.RESOLVED -> R.string.followup_done_resolved
    },
)
