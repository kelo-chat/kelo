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
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
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
 * sliding off the button before letting go cancels ([HoldToTalk]). While it listens the composer becomes the recording bar, so the
 * mic is only here for a hold (the composer keeps its look while the finger is down) and when nothing records. The microphone
 * permission is asked on the first tap; when it is refused, a short explanation offers the app's settings. A phone without speech
 * recognition shows the button greyed, and a tap or long press says why.
 */
@OptIn(ExperimentalFoundationApi::class, ExperimentalMaterial3Api::class)
@Composable
fun MicButton(voice: AndroidComposerVoice, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val c = Kelo.colors
    val haptics = LocalHaptics.current
    val reduce = LocalReduceMotion.current
    val container = chat.kelo.app.LocalAppContainer.current
    val scope = rememberCoroutineScope()
    val speech = voice.speech
    val apiNow by container.sessions.api.collectAsState()
    LaunchedEffect(apiNow) { voice.model.refresh() }
    val voiceView by voice.model.view.collectAsState()
    val way = DictationWay.pick(voiceView, phoneRecognizer = speech.available)
    val serverWay = way == DictationWay.Way.SERVER
    val offerWay = way == DictationWay.Way.OFFER_THEN_PHONE || way == DictationWay.Way.OFFER_THEN_NONE
    var askOffer by remember { mutableStateOf(false) }
    var phase by remember { mutableStateOf(HoldToTalk.Phase.IDLE) }
    var explainDenied by remember { mutableStateOf(false) }
    val serverPhase by voice.server.phase.collectAsState()
    val serverProblem by voice.server.problem.collectAsState()
    val recordingNow = speech.listening || serverPhase == RecordingSteps.Phase.RECORDING
    fun begin() {
        haptics.tick()
        if (serverWay) voice.server.start() else voice.phone.start()
    }

    /** The finger is no longer holding: the hold state ends (also when a recognizer stopped by itself). */
    fun endHold() {
        phase = HoldToTalk.Phase.IDLE
        voice.held = false
    }

    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        // the permission box takes the touch away: a hold that asked for it ends here, and listening goes on as after a tap
        endHold()
        if (granted) begin() else explainDenied = true
    }
    val tooltip = rememberTooltipState(isPersistent = false)
    val unavailable = stringResource(R.string.voice_unavailable)
    val downloadingLine = if (voiceView?.state == "downloading") {
        voiceDownloadingLine(voiceView?.progress?.done ?: 0, voiceView?.progress?.total ?: 0)
    } else unavailable
    val label = stringResource(if (recordingNow) R.string.voice_stop else R.string.voice_start)
    val listeningState = stringResource(R.string.voice_listening)

    val holding = phase == HoldToTalk.Phase.HOLDING || phase == HoldToTalk.Phase.HOLDING_AWAY
    val away = phase == HoldToTalk.Phase.HOLDING_AWAY
    val holdLabel = stringResource(R.string.voice_hold_label)
    val holdHint = stringResource(R.string.voice_hold_hint)

    // a recognition problem of the server path: one line, and 다시 하기 when the server could not be reached (the bar is gone then)
    serverProblem?.let { problem ->
        Popup(alignment = Alignment.TopEnd) { ServerProblemCard(voice.server, problem, Modifier.padding(bottom = 8.dp)) }
    }

    fun granted(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** Asks for the microphone when needed, then starts (the permission answer starts it too). */
    fun proceed() {
        if (granted()) begin() else permission.launch(Manifest.permission.RECORD_AUDIO)
    }

    // the first tap after the offer: the owner says 확인 or 취소, then this tap goes on as it would
    if (askOffer && offerWay) {
        val offered = voiceView?.recommended
        if (offered != null) {
            VoiceOfferDialog(
                model = offered,
                onYes = {
                    askOffer = false
                    voice.model.choose(offered.id)
                    if (way == DictationWay.Way.OFFER_THEN_PHONE) proceed()
                },
                onNo = {
                    askOffer = false
                    voice.model.decline()
                    if (way == DictationWay.Way.OFFER_THEN_PHONE) proceed()
                },
            )
        } else askOffer = false
    }

    val onTap: () -> Unit = {
        when {
            offerWay -> askOffer = true
            // the bar is up while it listens, so a tap here only comes from a hold's end: it stops
            recordingNow -> voice.finish(AfterSpeech.FIELD)
            serverWay -> proceed()
            !speech.available -> scope.launch { tooltip.show() }
            // the phone's recognizer keeps working while the model downloads (the tooltip says how far it is)
            way == DictationWay.Way.DOWNLOADING_NONE -> scope.launch { tooltip.show() }
            else -> proceed()
        }
    }

    /** The finger stayed down: dictate until it lifts. False when nothing started (no speech recognition on this phone). */
    fun onHoldStart(): Boolean = when {
        offerWay -> { askOffer = true; false }
        serverWay -> { if (granted()) begin() else permission.launch(Manifest.permission.RECORD_AUDIO); true }
        !speech.available -> { scope.launch { tooltip.show() }; false }
        granted() -> { begin(); true }
        else -> { permission.launch(Manifest.permission.RECORD_AUDIO); true }
    }
    val applyEvent by rememberUpdatedState<(HoldToTalk.Event) -> Unit> { event ->
        val step = HoldToTalk.step(phase, event)
        phase = step.phase
        when (step.action) {
            HoldToTalk.Action.TAP -> onTap()
            HoldToTalk.Action.START -> {
                // the composer keeps its look while the finger is down (the bar is not drawn while held)
                voice.held = true
                if (!onHoldStart()) endHold()
            }
            HoldToTalk.Action.FINISH -> { voice.held = false; voice.finish(AfterSpeech.FIELD) }
            HoldToTalk.Action.CANCEL -> { voice.held = false; voice.discard() }
            HoldToTalk.Action.NONE -> {}
        }
    }
    // a hold that ends because the recognizer stopped by itself (a pause, an error) leaves nothing held
    LaunchedEffect(speech.listening) { if (!speech.listening && holding) endHold() }

    // the ring: grows with the sound level while listening, and breathes a little so it is clearly on. While the server records
    // during a hold (the bar is not up), the ring follows the newest level of the server's trail.
    val trailVersion = voice.server.trail.version
    val ringSource = when {
        speech.listening -> speech.level
        serverPhase == RecordingSteps.Phase.RECORDING -> { trailVersion; voice.server.trail.newest }
        else -> 0f
    }
    val level by animateFloatAsState(ringSource, tween(if (reduce) 0 else 120), label = "micLevel")
    val breath = if (recordingNow && !reduce) {
        rememberInfiniteTransition(label = "micBreath").animateFloat(0f, 1f, infiniteRepeatable(tween(900), RepeatMode.Reverse), label = "b").value
    } else 0f

    TooltipBox(
        positionProvider = TooltipDefaults.rememberPlainTooltipPositionProvider(),
        tooltip = { PlainTooltip { Text(downloadingLine) } },
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
                        applyEvent(HoldToTalk.Event.Down(listening = recordingNow))
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
                    if (recordingNow) stateDescription = listeningState
                    // the screen reader's double tap is the tap; its long press does the same (the hold needs a finger that stays down)
                    onClick(label = label) { onTap(); true }
                    onLongClick(label = holdLabel) { if (!speech.available) scope.launch { tooltip.show() } else onTap(); true }
                },
            contentAlignment = Alignment.Center,
        ) {
            if (recordingNow) {
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
            // the speech model downloads to the server: the mic is a circle with the progress (the owner's addition 2026-11-18)
            val ring = VoiceDownloadRing.of(voiceView)
            if (ring != null) {
                val downloadLabel = voiceDownloadingLine(voiceView?.progress?.done ?: 0, voiceView?.progress?.total ?: 0)
                val percent = ring.percent
                if (percent != null) {
                    CircularProgressIndicator(progress = { percent / 100f }, modifier = Modifier.size(22.dp), color = c.accent, strokeWidth = 2.dp)
                } else {
                    CircularProgressIndicator(modifier = Modifier.size(22.dp), color = c.accent, strokeWidth = 2.dp)
                }
                Box(Modifier.size(22.dp).semantics { contentDescription = downloadLabel })
            } else Icon(
                KeloIcons.Mic, contentDescription = null,
                tint = when {
                    !speech.available -> c.muted.copy(alpha = 0.4f)
                    away -> c.dangerInk
                    recordingNow -> c.accent
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
