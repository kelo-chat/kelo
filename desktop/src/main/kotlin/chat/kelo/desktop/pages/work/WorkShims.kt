package chat.kelo.desktop.pages.work

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.followup.FollowupAction
import chat.kelo.app.followup.FollowupStep
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.MarkdownView
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.BackTitleBar
import chat.kelo.desktop.ui.HeaderHeight
import chat.kelo.desktop.ui.SUB_PAGE_WIDTH
import java.util.Locale

/*
 * The few Android pieces the ported Work pages lean on, in their desktop form: no haptics on a computer, the app's language from
 * the desktop strings, a sub page under the floating header, the loading placeholders, data: URL pictures and the follow-up buttons.
 */

/** A computer has no vibration: the ported pages call these where the phone ticks. */
internal object NoHaptics {
    fun tick() {}
    fun confirm() {}
    fun reject() {}
}

@Composable
internal fun rememberHaptics(): NoHaptics = NoHaptics

/** The app's language (what the desktop strings are shown in). */
@Composable
@ReadOnlyComposable
internal fun appLocale(): Locale = LocalStrings.current.locale

/**
 * A Work sub page: the back arrow and title under the floating header, centred and at most [SUB_PAGE_WIDTH] wide; the content
 * scrolls itself (as on the phone). Escape steps back.
 */
@Composable
internal fun AgentSubPage(title: String, onBack: () -> Unit, actions: @Composable () -> Unit = {}, content: @Composable () -> Unit) {
    Box(
        Modifier.fillMaxSize().background(Kelo.colors.bg).onPreviewKeyEvent { e ->
            if (e.type == KeyEventType.KeyDown && e.key == Key.Escape) { onBack(); true } else false
        },
        contentAlignment = Alignment.TopCenter,
    ) {
        Column(Modifier.widthIn(max = SUB_PAGE_WIDTH.dp).fillMaxWidth().padding(top = HeaderHeight)) {
            BackTitleBar(title, onBack, actions = { actions() })
            Box(Modifier.fillMaxSize()) { content() }
        }
    }
}

/** Grey bars where rows will come. */
@Composable
internal fun SkeletonRows(count: Int) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        repeat(count) { i ->
            Box(Modifier.fillMaxWidth(if (i % 2 == 0) 0.9f else 0.6f).height(14.dp).clip(RoundedCornerShape(7.dp)).background(c.bubble))
        }
    }
}

@Composable
internal fun MarkdownText(text: String) = MarkdownView(text)

/** A data: URL picture (a tab's thumbnail, a chat card's last picture). */
internal object BrowserImage {
    fun bytes(dataUrl: String): ByteArray? {
        val comma = dataUrl.indexOf(',')
        if (!dataUrl.startsWith("data:image/") || comma < 0 || !dataUrl.substring(0, comma).endsWith(";base64")) return null
        return runCatching { java.util.Base64.getDecoder().decode(dataUrl.substring(comma + 1)) }.getOrNull()
    }

    fun decode(dataUrl: String): ImageBitmap? = bytes(dataUrl)?.let(::decodeBytes)

    fun decodeBytes(b: ByteArray): ImageBitmap? = runCatching { org.jetbrains.skia.Image.makeFromEncoded(b).toComposeImageBitmap() }.getOrNull()
}

// ---- the follow-up buttons (android/kelo .../followup/FollowupUi.kt)

@Composable
internal fun actionLabel(step: FollowupStep): String = when (step) {
    is FollowupStep.OpenLogin -> stringResource(R.string.followup_action_login)
    is FollowupStep.Answer -> stringResource(if (step.approve) R.string.followup_action_approve else R.string.followup_action_deny)
    is FollowupStep.ResumeWorker -> stringResource(R.string.followup_action_resume)
    is FollowupStep.Drop -> stringResource(R.string.followup_action_drop)
    is FollowupStep.Snooze -> stringResource(R.string.followup_action_snooze, laterText(step.minutes))
    is FollowupStep.Retry -> stringResource(R.string.followup_action_retry)
}

@Composable
internal fun laterText(minutes: Int): String = when {
    minutes >= 1440 && minutes % 1440 == 0 -> pluralStringResource(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440)
    minutes >= 60 && minutes % 60 == 0 -> pluralStringResource(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60)
    else -> pluralStringResource(R.plurals.agent_rel_minutes_future, minutes, minutes)
}

private fun primary(step: FollowupStep): Boolean =
    step is FollowupStep.OpenLogin || step is FollowupStep.Retry || (step is FollowupStep.Answer && step.approve) || step is FollowupStep.ResumeWorker

/** The buttons on a follow-up card; they wrap onto more lines. [busy] shows a small spinner instead. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun FollowupButtons(buttons: List<Pair<FollowupAction, FollowupStep>>, busy: Boolean, onPress: (FollowupStep) -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    if (buttons.isEmpty()) return
    if (busy) {
        Row(modifier.padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(18.dp), color = c.muted, strokeWidth = 2.dp)
        }
        return
    }
    FlowRow(modifier, horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        val main = buttons.indexOfFirst { primary(it.second) }
        buttons.forEachIndexed { i, (_, step) ->
            val label = actionLabel(step)
            val pad = PaddingValues(horizontal = 14.dp, vertical = 8.dp)
            if (i == main) {
                Button(onClick = { onPress(step) }, shape = RoundedCornerShape(50), contentPadding = pad,
                    colors = ButtonDefaults.buttonColors(containerColor = c.accent, contentColor = c.onAccent)) {
                    Text(label, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center)
                }
            } else {
                OutlinedButton(onClick = { onPress(step) }, shape = RoundedCornerShape(50), contentPadding = pad, border = BorderStroke(1.dp, c.line),
                    colors = ButtonDefaults.outlinedButtonColors(containerColor = c.card, contentColor = c.ink)) {
                    Text(label, style = MaterialTheme.typography.labelLarge, textAlign = TextAlign.Center)
                }
            }
        }
    }
}

/**
 * What the phone shows as a bottom sheet (a few words to a job, how a schedule repeats), as a dialog on a card: a window has no
 * bottom edge to slide from. Clicking outside or Escape closes it.
 */
@Composable
internal fun DialogSheet(onDismissRequest: () -> Unit, content: @Composable androidx.compose.foundation.layout.ColumnScope.() -> Unit) {
    androidx.compose.ui.window.Dialog(onDismissRequest = onDismissRequest) {
        Column(
            Modifier.widthIn(max = 480.dp).fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(Kelo.colors.card)
                .verticalScroll(androidx.compose.foundation.rememberScrollState()).padding(top = 20.dp),
            content = content,
        )
    }
}


/**
 * The sign-in form for one Kelo keychain item, opened from a follow-up on hold: the chat's own secure form (the value goes to the
 * server's vault and is never shown again). Once filled in, the follow-ups load again.
 */
@Composable
internal fun SignInSheet(vm: WorkModel, name: String, onClose: () -> Unit) {
    val sessions = vm.sessionsForForms
    DialogSheet(onDismissRequest = onClose) {
        Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(stringResource(R.string.signin_sheet_title), style = MaterialTheme.typography.titleMedium, color = Kelo.colors.ink)
            chat.kelo.desktop.chat.VaultCard(
                chat.kelo.app.chat.ChatItem.Vault("hold:$name", name), sessions,
                onSettle = { onClose(); vm.loadFollowups() }, onNotify = { _ -> vm.loadFollowups() },
            )
        }
    }
}
