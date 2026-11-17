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
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.input.pointer.AwaitPointerEventScope
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.semantics.onClick
import androidx.compose.ui.semantics.onLongClick
import androidx.compose.ui.semantics.role
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import kotlinx.coroutines.withTimeoutOrNull
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
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import kotlinx.coroutines.launch

/**
 * The composer's mic: tap to speak, and the words appear in the field at the cursor as they are heard; tap again (or stop
 * speaking) to end. Or hold it: it dictates while the finger is down, letting go keeps the words in the field (nothing is sent) and
 * sliding off the button before letting go cancels ([HoldToTalk]). The ring around it follows how loud it hears. The microphone permission is asked on the first tap; when it is
 * refused, a short explanation offers the app's settings. A phone without speech recognition shows the button greyed, and a tap
 * or long press says why.
 */
@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun MicButton(field: () -> TextFieldValue, onField: (TextFieldValue) -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val c = Kelo.colors
    val haptics = LocalHaptics.current
    val reduce = LocalReduceMotion.current
    var anchor by remember { mutableStateOf<SpeechMerge.Anchor?>(null) }
    var explainDenied by remember { mutableStateOf(false) }
    var phase by remember { mutableStateOf(HoldToTalk.Phase.IDLE) }
    val problemText = mapOf(
        SpeechProblem.NO_MATCH to stringResource(R.string.voice_no_match),
        SpeechProblem.NETWORK to stringResource(R.string.voice_dictation_offline),
        SpeechProblem.BUSY to stringResource(R.string.voice_busy),
        SpeechProblem.OTHER to stringResource(R.string.voice_dictation_failed),
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
        // the permission box takes the touch away: a hold that asked for it ends here, and listening goes on as after a tap
        phase = HoldToTalk.Phase.IDLE
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

    val holding = phase == HoldToTalk.Phase.HOLDING || phase == HoldToTalk.Phase.HOLDING_AWAY
    val away = phase == HoldToTalk.Phase.HOLDING_AWAY
    val holdLabel = stringResource(R.string.voice_hold_label)
    val holdHint = stringResource(R.string.voice_hold_hint)

    /** Sliding off the held mic: listening ends without a result and the field goes back to what it was. */
    fun cancelDictation() {
        val a = anchor
        speech.cancel()
        anchor = null
        if (a != null) onField(TextFieldValue(a.before + a.after, TextRange(a.before.length)))
    }

    val onTap: () -> Unit = {
        when {
            !speech.available -> scope.launch { tooltip.show() }
            speech.listening -> speech.stop()
            ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED -> begin()
            else -> permission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    /** The finger stayed down: dictate until it lifts. False when nothing started (no speech recognition on this phone). */
    fun onHoldStart(): Boolean = when {
        !speech.available -> { scope.launch { tooltip.show() }; false }
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED -> { begin(); true }
        else -> { permission.launch(Manifest.permission.RECORD_AUDIO); true }
    }
    val applyEvent by rememberUpdatedState<(HoldToTalk.Event) -> Unit> { event ->
        val step = HoldToTalk.step(phase, event)
        phase = step.phase
        when (step.action) {
            HoldToTalk.Action.TAP -> onTap()
            HoldToTalk.Action.START -> if (!onHoldStart()) phase = HoldToTalk.Phase.IDLE
            HoldToTalk.Action.FINISH -> speech.stop()
            HoldToTalk.Action.CANCEL -> cancelDictation()
            HoldToTalk.Action.NONE -> {}
        }
    }
    // a hold that ends because the recognizer stopped by itself (a pause, an error) leaves nothing held
    androidx.compose.runtime.LaunchedEffect(speech.listening) { if (!speech.listening && holding) phase = HoldToTalk.Phase.IDLE }

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
                .pointerInput(Unit) {
                    val slack = 24.dp.toPx()
                    awaitEachGesture {
                        awaitFirstDown(requireUnconsumed = false)
                        applyEvent(HoldToTalk.Event.Down(listening = speech.listening))
                        // a tap is over before the hold time; moving off the button ends the touch's meaning too
                        val early = withTimeoutOrNull(HoldToTalk.HOLD_MS) {
                            var e: HoldToTalk.Event
                            do { e = nextTouchEvent(slack) } while (e is HoldToTalk.Event.Move && e.onButton)
                            e
                        }
                        if (early == null) applyEvent(HoldToTalk.Event.HoldElapsed) else applyEvent(early)
                        if (early !is HoldToTalk.Event.Up && early !is HoldToTalk.Event.Cancel) {
                            while (true) {
                                val e = nextTouchEvent(slack)
                                applyEvent(e)
                                if (e is HoldToTalk.Event.Up || e is HoldToTalk.Event.Cancel) break
                            }
                        }
                    }
                }
                .semantics {
                    role = Role.Button
                    contentDescription = if (speech.available) label else unavailable
                    if (speech.listening) stateDescription = listeningState
                    // the screen reader's double tap is the tap; its long press does the same (the hold needs a finger that stays down)
                    onClick(label = label) { onTap(); true }
                    onLongClick(label = holdLabel) { if (!speech.available) scope.launch { tooltip.show() } else onTap(); true }
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
                    away -> c.dangerInk
                    speech.listening -> c.accent
                    else -> c.muted
                },
            )
        }
    }

    if (holding) HoldHint(holdHint, away)

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

/** The next thing the finger does, for [HoldToTalk]: lifted, taken away by the system, or moved (on the button or not). */
private suspend fun AwaitPointerEventScope.nextTouchEvent(slack: Float): HoldToTalk.Event {
    val event = awaitPointerEvent()
    val change = event.changes.firstOrNull() ?: return HoldToTalk.Event.Cancel
    change.consume()
    return if (!change.pressed) HoldToTalk.Event.Up
    else HoldToTalk.Event.Move(HoldToTalk.onButton(change.position.x, change.position.y, size.width.toFloat(), size.height.toFloat(), slack))
}

/** "Release to finish, slide away to cancel": a small card above the mic while it is held; it turns red when letting go would cancel. */
@Composable
private fun HoldHint(text: String, away: Boolean) {
    val c = Kelo.colors
    val gap = with(androidx.compose.ui.platform.LocalDensity.current) { 10.dp.roundToPx() }
    Popup(
        popupPositionProvider = object : PopupPositionProvider {
            override fun calculatePosition(anchorBounds: IntRect, windowSize: IntSize, layoutDirection: LayoutDirection, popupContentSize: IntSize): IntOffset =
                IntOffset(
                    (anchorBounds.right - popupContentSize.width).coerceIn(0, (windowSize.width - popupContentSize.width).coerceAtLeast(0)),
                    (anchorBounds.top - popupContentSize.height - gap).coerceAtLeast(0),
                )
        },
    ) {
        Text(
            text,
            color = if (away) c.dangerInk else c.ink,
            style = androidx.compose.material3.MaterialTheme.typography.bodyMedium,
            modifier = Modifier
                .clip(RoundedCornerShape(14.dp))
                .background(if (away) c.dangerSoft else c.card)
                .padding(horizontal = 14.dp, vertical = 10.dp),
        )
    }
}
