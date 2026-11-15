package chat.kelo.app.voice

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import chat.kelo.app.R
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import kotlinx.coroutines.launch

/**
 * The composer's mic: tap to speak, and the words appear in the field at the cursor as they are heard; tap again (or stop
 * speaking) to end. The ring around it follows how loud it hears. The microphone permission is asked on the first tap; when it is
 * refused, a short explanation offers the app's settings. A phone without speech recognition shows the button greyed, and a tap
 * or long press says why.
 */
@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun MicButton(field: () -> TextFieldValue, onField: (TextFieldValue) -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val c = Kelo.colors
    val haptics = rememberHaptics()
    val reduce = LocalReduceMotion.current
    var anchor by remember { mutableStateOf<SpeechMerge.Anchor?>(null) }
    var explainDenied by remember { mutableStateOf(false) }
    val problemText = mapOf(
        SpeechProblem.NO_MATCH to stringResource(R.string.voice_no_match),
        SpeechProblem.NETWORK to stringResource(R.string.voice_network),
        SpeechProblem.BUSY to stringResource(R.string.voice_busy),
        SpeechProblem.OTHER to stringResource(R.string.voice_failed),
    )
    val speech = rememberSpeechInput(
        onWords = { words, _ ->
            val a = anchor ?: return@rememberSpeechInput
            val m = SpeechMerge.merge(a, words)
            onField(TextFieldValue(m.text, TextRange(m.cursor)))
        },
        onEnd = { problem ->
            anchor = null
            haptics.confirm()
            problem?.let { Toast.makeText(context, problemText[it], Toast.LENGTH_SHORT).show() }
        },
    )
    fun begin() {
        val f = field()
        anchor = SpeechMerge.anchor(f.text, f.selection.start, f.selection.end)
        haptics.tick()
        speech.start()
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) begin() else explainDenied = true
    }
    // the header's character listens too
    val activity = chat.kelo.app.LocalAppContainer.current.agentActivity
    val listeningNow = speech.listening
    androidx.compose.runtime.LaunchedEffect(listeningNow) { activity.listening.value = listeningNow }
    androidx.compose.runtime.DisposableEffect(activity) { onDispose { activity.listening.value = false } }
    val tooltip = rememberTooltipState(isPersistent = false)
    val scope = rememberCoroutineScope()
    val unavailable = stringResource(R.string.voice_unavailable)
    val label = stringResource(if (speech.listening) R.string.voice_stop else R.string.voice_start)
    val listeningState = stringResource(R.string.voice_listening)

    val onTap: () -> Unit = {
        when {
            !speech.available -> scope.launch { tooltip.show() }
            speech.listening -> speech.stop()
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED -> begin()
            else -> permission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // the ring: grows with the sound level while listening, and breathes a little so it is clearly on
    val level by animateFloatAsState(if (speech.listening) speech.level else 0f, tween(if (reduce) 0 else 120), label = "micLevel")
    val breath = if (speech.listening && !reduce) {
        rememberInfiniteTransition(label = "micBreath").animateFloat(0f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "b").value
    } else 0f

    TooltipBox(
        positionProvider = TooltipDefaults.rememberPlainTooltipPositionProvider(),
        tooltip = { PlainTooltip { Text(unavailable) } },
        state = tooltip,
        modifier = modifier,
    ) {
        Box(
            Modifier
                .size(48.dp)
                .clip(CircleShape)
                .combinedClickable(
                    role = Role.Button,
                    onClickLabel = label,
                    onClick = onTap,
                    onLongClick = { if (!speech.available) scope.launch { tooltip.show() } else onTap() },
                )
                .semantics {
                    contentDescription = if (speech.available) label else unavailable
                    if (speech.listening) stateDescription = listeningState
                },
            contentAlignment = Alignment.Center,
        ) {
            if (speech.listening) {
                Box(
                    Modifier
                        .size(40.dp)
                        .graphicsLayer {
                            val s = 0.85f + 0.35f * level + 0.06f * breath
                            scaleX = s; scaleY = s
                        }
                        .background(c.accent.copy(alpha = 0.22f), CircleShape),
                )
            }
            Icon(
                KeloIcons.Mic, contentDescription = null,
                tint = when {
                    !speech.available -> c.muted.copy(alpha = 0.4f)
                    speech.listening -> c.accent
                    else -> c.muted
                },
            )
        }
    }

    if (explainDenied) {
        AlertDialog(
            onDismissRequest = { explainDenied = false },
            containerColor = c.card,
            title = { Text(stringResource(R.string.voice_denied_title), color = c.ink) },
            text = { Text(stringResource(R.string.voice_denied_body), color = c.ink) },
            confirmButton = {
                TextButton(onClick = {
                    explainDenied = false
                    runCatching {
                        context.startActivity(
                            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null))
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    }
                }) { Text(stringResource(R.string.open_settings), color = c.accent) }
            },
            dismissButton = { TextButton(onClick = { explainDenied = false }) { Text(stringResource(R.string.close), color = c.ink) } },
        )
    }
}
