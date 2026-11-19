package chat.kelo.app.call

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.sizeIn
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.material3.ripple
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onKeyEvent
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppServices
import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.agent.AgentPicture
import chat.kelo.app.res.*
import chat.kelo.app.ui.character.KeloCharacter
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.character.centerShift
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.update.UpdateRules
import chat.kelo.app.voice.Waveform

/**
 * A round call control button with its label underneath.
 * The entire column acts as a single accessible control.
 */
@Composable
fun CallControl(
    icon: ImageVector,
    size: Dp,
    bg: Color,
    tint: Color,
    label: String,
    tag: String,
    modifier: Modifier = Modifier,
    checked: Boolean? = null,
    onClick: () -> Unit,
) {
    val clickModifier = if (checked != null) {
        Modifier.toggleable(
            value = checked,
            role = Role.Switch,
            interactionSource = remember { MutableInteractionSource() },
            indication = ripple(bounded = false, radius = size / 2),
            onValueChange = { onClick() },
        )
    } else {
        Modifier.clickable(
            role = Role.Button,
            interactionSource = remember { MutableInteractionSource() },
            indication = ripple(bounded = false, radius = size / 2),
            onClick = onClick,
        )
    }

    Column(
        modifier = modifier
            .testTag(tag)
            .clip(RoundedCornerShape(size / 2 + 16.dp))
            .then(clickModifier)
            .semantics(mergeDescendants = true) {
                this.contentDescription = label
            }
            .padding(horizontal = 8.dp, vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Box(
            modifier = Modifier
                .size(size)
                .sizeIn(minWidth = 48.dp, minHeight = 48.dp)
                .shadow(4.dp, CircleShape, ambientColor = Kelo.colors.scrim, spotColor = Kelo.colors.scrim)
                .clip(CircleShape)
                .background(bg),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                icon,
                contentDescription = null,
                tint = tint,
                modifier = Modifier.size(size * 0.44f).sizeIn(minWidth = 24.dp, minHeight = 24.dp),
            )
        }
        Text(
            label,
            style = MaterialTheme.typography.labelSmall,
            color = Kelo.colors.muted,
            textAlign = TextAlign.Center,
            maxLines = 2,
            modifier = Modifier.widthIn(max = size + 24.dp),
        )
    }
}

/**
 * Incoming call screen: rings the user with accept and decline controls.
 */
@Composable
fun IncomingCallScreen(
    session: CallSession,
    agentName: String,
    photo: ImageBitmap? = null,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val topic = session.topic

    // Two soft rings that pulse every 1.6 s
    val infiniteTransition = rememberInfiniteTransition(label = "ring_pulse")
    val pulseProgress by infiniteTransition.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(durationMillis = 1600, easing = LinearEasing),
            repeatMode = RepeatMode.Restart,
        ),
        label = "pulse_progress",
    )

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(c.bg)
            .testTag("call.incoming"),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .systemBarsPadding()
                .padding(horizontal = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Spacer(Modifier.weight(1f))

            // Avatar with pulsing rings
            Box(
                modifier = Modifier.size(160.dp),
                contentAlignment = Alignment.Center,
            ) {
                if (!reduce) {
                    val scale1 = 1f + 0.22f * pulseProgress
                    val alpha1 = 0.18f * (1f - pulseProgress)
                    val scale2 = 1f + 0.40f * pulseProgress
                    val alpha2 = 0.08f * (1f - pulseProgress)

                    Box(
                        Modifier
                            .size(120.dp * scale2)
                            .clip(CircleShape)
                            .background(c.accent.copy(alpha = alpha2)),
                    )
                    Box(
                        Modifier
                            .size(120.dp * scale1)
                            .clip(CircleShape)
                            .background(c.accent.copy(alpha = alpha1)),
                    )
                } else {
                    Box(
                        Modifier
                            .size(140.dp)
                            .clip(CircleShape)
                            .background(c.accent.copy(alpha = 0.12f)),
                    )
                }

                Box(
                    modifier = Modifier
                        .size(120.dp)
                        .shadow(6.dp, CircleShape, ambientColor = c.scrim, spotColor = c.scrim)
                        .clip(CircleShape)
                        .background(c.card),
                    contentAlignment = Alignment.Center,
                ) {
                    if (photo != null) {
                        AgentPicture(agentName, photo, 120)
                    } else {
                        KeloCharacter(
                            KeloMood.Idle,
                            Modifier.size((120 * 0.92f).dp).offset(
                                x = (120 * 0.92f * KeloMood.Idle.centerShift() / 120f).dp,
                                y = 3.dp,
                            ),
                        )
                    }
                }
            }

            Spacer(Modifier.height(16.dp))
            Text(
                agentName,
                style = MaterialTheme.typography.headlineSmall,
                color = c.ink,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                string(Res.string.call_incoming_line),
                style = MaterialTheme.typography.bodyMedium,
                color = c.muted,
                textAlign = TextAlign.Center,
            )
            if (topic.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    topic,
                    style = MaterialTheme.typography.bodyMedium,
                    color = c.ink,
                    textAlign = TextAlign.Center,
                    maxLines = 3,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            Spacer(Modifier.weight(1f))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CallControl(
                    icon = KeloIcons.HandsetDown,
                    size = 72.dp,
                    bg = c.danger,
                    tint = c.onDanger,
                    label = string(Res.string.call_decline),
                    tag = "call.decline",
                    onClick = { session.decline() },
                )
                CallControl(
                    icon = KeloIcons.Handset,
                    size = 72.dp,
                    bg = c.success,
                    tint = c.onSuccess,
                    label = string(Res.string.call_accept),
                    tag = "call.accept",
                    onClick = { session.accept() },
                )
            }

            Spacer(Modifier.height(40.dp))
        }
    }
}

/**
 * In-call screen: avatar with mood & voice level ring, status line, clock, waveform, voice download prompt, and controls.
 */
@Composable
fun InCallScreen(
    session: CallSession,
    agentName: String,
    photo: ImageBitmap? = null,
    audio: CallAudio? = null,
    onOpenCard: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    val ui by session.ui.collectAsStateWithLifecycle()
    val mood = CallRules.mood(ui.phase)

    Column(
        modifier = modifier
            .fillMaxSize()
            .systemBarsPadding()
            .padding(horizontal = 24.dp)
            .testTag("call.active"),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.weight(1f))

        // Avatar (136.dp, mood = CallRules.mood(phase))
        val isSpeaking = ui.phase == CallSteps.Phase.SPEAKING
        val ringWidth = if (isSpeaking) (2f + 6f * session.trail.newest.coerceIn(0f, 1f)).dp else 0.dp

        Box(
            modifier = Modifier
                .size(136.dp)
                .shadow(6.dp, CircleShape, ambientColor = c.scrim, spotColor = c.scrim)
                .clip(CircleShape)
                .background(c.card)
                .then(if (ringWidth > 0.dp) Modifier.border(ringWidth, c.accent, CircleShape) else Modifier)
                .clickable(enabled = isSpeaking) { session.stopVoice() },
            contentAlignment = Alignment.Center,
        ) {
            if (photo != null) {
                AgentPicture(agentName, photo, 136)
            } else {
                KeloCharacter(
                    mood,
                    Modifier.size((136 * 0.92f).dp).offset(
                        x = (136 * 0.92f * mood.centerShift() / 120f).dp,
                        y = 3.dp,
                    ),
                )
            }
        }

        Spacer(Modifier.height(16.dp))
        Text(
            agentName,
            style = MaterialTheme.typography.headlineSmall,
            color = c.ink,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
        Spacer(Modifier.height(4.dp))

        // Status text line
        val stateText = when {
            ui.muted -> string(Res.string.call_muted)
            ui.phase == CallSteps.Phase.CONNECTING -> string(Res.string.call_connecting)
            ui.phase == CallSteps.Phase.LISTENING || ui.phase == CallSteps.Phase.HEARING -> string(Res.string.call_listening)
            ui.phase == CallSteps.Phase.TRANSCRIBING || ui.phase == CallSteps.Phase.WAITING -> string(Res.string.call_thinking)
            ui.phase == CallSteps.Phase.SPEAKING -> string(Res.string.call_speaking)
            ui.phase == CallSteps.Phase.PREPARING -> string(Res.string.call_connecting)
            else -> string(Res.string.call_connecting)
        }
        Text(
            stateText,
            style = MaterialTheme.typography.bodyMedium,
            color = c.muted,
            modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
        )

        Spacer(Modifier.height(2.dp))
        Text(
            CallRules.clock(ui.elapsedMs),
            style = MaterialTheme.typography.bodySmall,
            color = c.muted,
        )

        Spacer(Modifier.height(20.dp))
        // Waveform
        val waveActive = ui.phase == CallSteps.Phase.LISTENING || ui.phase == CallSteps.Phase.HEARING || ui.phase == CallSteps.Phase.SPEAKING
        Waveform(
            trail = session.trail,
            active = waveActive,
            modifier = Modifier
                .widthIn(max = 280.dp)
                .fillMaxWidth()
                .height(40.dp),
        )

        if (ui.offline) {
            Spacer(Modifier.height(12.dp))
            Text(
                string(Res.string.call_offline),
                style = MaterialTheme.typography.bodySmall,
                color = c.dangerInk,
                textAlign = TextAlign.Center,
            )
        }

        if (ui.voiceLine) {
            Spacer(Modifier.height(12.dp))
            val downloadingPercent = ui.voiceDownloadPercent
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(12.dp))
                    .background(c.neutralSoft)
                    .padding(horizontal = 12.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                val voiceText = if (downloadingPercent != null) {
                    string(Res.string.voice_speech_downloading, downloadingPercent)
                } else {
                    string(Res.string.call_voice_better)
                }
                Text(
                    voiceText,
                    style = MaterialTheme.typography.bodySmall,
                    color = c.ink,
                )
                if (downloadingPercent == null) {
                    TextButton(onClick = { session.getBetterVoice() }) {
                        Text(string(Res.string.call_voice_get), color = c.accent, style = MaterialTheme.typography.labelMedium)
                    }
                }
                IconButton(
                    onClick = { session.dismissVoiceLine() },
                    modifier = Modifier.size(24.dp),
                ) {
                    Icon(
                        KeloIcons.Close,
                        contentDescription = string(Res.string.call_voice_line_close),
                        tint = c.muted,
                        modifier = Modifier.size(16.dp),
                    )
                }
            }
        }

        if (ui.cardWaiting) {
            Spacer(Modifier.height(12.dp))
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(99.dp))
                    .background(c.infoSoft)
                    .padding(start = 14.dp, end = 6.dp, top = 2.dp, bottom = 2.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    string(Res.string.call_card_waiting),
                    style = MaterialTheme.typography.bodySmall,
                    color = c.infoInk,
                )
                TextButton(onClick = { onOpenCard?.invoke() }) {
                    Text(string(Res.string.call_card_open), color = c.infoInk, style = MaterialTheme.typography.labelMedium)
                }
            }
        }

        Spacer(Modifier.weight(1f))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(28.dp, Alignment.CenterHorizontally),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            CallControl(
                icon = if (ui.muted) KeloIcons.MicOff else KeloIcons.Mic,
                size = 60.dp,
                bg = if (ui.muted) c.ink else c.neutralSoft,
                tint = if (ui.muted) c.bg else c.ink,
                label = string(if (ui.muted) Res.string.call_unmute else Res.string.call_mute),
                tag = "call.mute",
                checked = ui.muted,
                onClick = { session.setMuted(!ui.muted) },
            )
            if (audio?.hasSpeakerSwitch == true) {
                CallControl(
                    icon = KeloIcons.Speaker,
                    size = 60.dp,
                    bg = if (ui.speakerOn) c.ink else c.neutralSoft,
                    tint = if (ui.speakerOn) c.bg else c.ink,
                    label = string(Res.string.call_speaker),
                    tag = "call.speaker",
                    checked = ui.speakerOn,
                    onClick = { session.setSpeaker(!ui.speakerOn) },
                )
            }
            CallControl(
                icon = KeloIcons.HandsetDown,
                size = 72.dp,
                bg = c.danger,
                tint = c.onDanger,
                label = string(Res.string.call_hang_up),
                tag = "call.hangup",
                onClick = { session.hangUp() },
            )
        }

        Spacer(Modifier.height(40.dp))
    }
}

/**
 * Call overlay: manages incoming screen, in-call screen, minimized pill, and readiness dialogs.
 */
@Composable
fun CallOverlay(
    center: CallCenter,
    agentName: String = "Kelo",
    photo: ImageBitmap? = null,
    audio: CallAudio? = null,
    onOpenCard: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    val session by center.session.collectAsStateWithLifecycle()
    val isMinimized by center.isMinimized.collectAsStateWithLifecycle()

    val currentSession = session ?: return
    val ui by currentSession.ui.collectAsStateWithLifecycle()

    // Handle session end auto-cleanup
    LaunchedEffect(ui.phase, ui.endedReason) {
        if (ui.phase == CallSteps.Phase.ENDED) {
            when (ui.endedReason) {
                CallSteps.EndReason.DECLINED,
                CallSteps.EndReason.HUNG_UP,
                CallSteps.EndReason.MISSED -> center.clear()
                else -> Unit // Let the dialog handle reason reporting and user confirm
            }
        }
    }

    if (isMinimized) {
        // Minimized pill over the chat, under the header
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .statusBarsPadding()
                .padding(top = 64.dp),
            contentAlignment = Alignment.TopCenter,
        ) {
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(20.dp))
                    .background(c.card)
                    .border(1.dp, c.line, RoundedCornerShape(20.dp))
                    .clickable { center.minimized(false) }
                    .padding(horizontal = 16.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Icon(
                    KeloIcons.Handset,
                    contentDescription = null,
                    tint = c.ink,
                    modifier = Modifier.size(16.dp),
                )
                Text(
                    string(Res.string.call_back_to_call, CallRules.clock(ui.elapsedMs)),
                    style = MaterialTheme.typography.bodyMedium,
                    color = c.ink,
                )
            }
        }
        return
    }

    val focusRequester = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        focusRequester.requestFocus()
    }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(c.bg)
            .focusRequester(focusRequester)
            .onKeyEvent { event ->
                when (event.key) {
                    Key.Escape -> {
                        if (ui.phase == CallSteps.Phase.RINGING) currentSession.decline()
                        else currentSession.hangUp()
                        true
                    }
                    Key.Enter -> {
                        if (ui.phase == CallSteps.Phase.RINGING) {
                            currentSession.accept()
                            true
                        } else false
                    }
                    Key.Spacebar -> {
                        if (ui.phase == CallSteps.Phase.SPEAKING) {
                            currentSession.stopVoice()
                            true
                        } else false
                    }
                    Key.M -> {
                        currentSession.setMuted(!ui.muted)
                        true
                    }
                    else -> false
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .widthIn(max = 420.dp),
            contentAlignment = Alignment.Center,
        ) {
            if (ui.phase == CallSteps.Phase.RINGING) {
                IncomingCallScreen(
                    session = currentSession,
                    agentName = agentName,
                    photo = photo,
                )
            } else {
                InCallScreen(
                    session = currentSession,
                    agentName = agentName,
                    photo = photo,
                    audio = audio,
                    onOpenCard = {
                        center.minimized(true)
                        onOpenCard?.invoke()
                    },
                )
            }
        }

        // PREPARING dialogs
        if (ui.phase == CallSteps.Phase.PREPARING) {
            when (ui.need) {
                CallReadiness.Need.STT_QUESTION -> {
                    AlertDialog(
                        onDismissRequest = { currentSession.answerSttQuestion(false) },
                        containerColor = c.card,
                        text = { Text(string(Res.string.voice_server_offer_body, "SenseVoice"), color = c.ink) },
                        confirmButton = {
                            TextButton(onClick = { currentSession.answerSttQuestion(true) }) {
                                Text(string(Res.string.ok), color = c.accent)
                            }
                        },
                        dismissButton = {
                            TextButton(onClick = { currentSession.answerSttQuestion(false) }) {
                                Text(string(Res.string.cancel), color = c.ink)
                            }
                        },
                    )
                }
                CallReadiness.Need.VOICE_QUESTION -> {
                    AlertDialog(
                        onDismissRequest = { currentSession.answerVoiceQuestion(false) },
                        containerColor = c.card,
                        title = { Text(string(Res.string.call_voice_offer_title), color = c.ink) },
                        text = {
                            Text(
                                string(Res.string.call_voice_offer, "145 MB"),
                                color = c.ink,
                            )
                        },
                        confirmButton = {
                            TextButton(onClick = { currentSession.answerVoiceQuestion(true) }) {
                                Text(string(Res.string.call_voice_offer_ok), color = c.accent)
                            }
                        },
                        dismissButton = {
                            TextButton(onClick = { currentSession.answerVoiceQuestion(false) }) {
                                Text(string(Res.string.call_voice_offer_later), color = c.ink)
                            }
                        },
                    )
                }
                CallReadiness.Need.IMPOSSIBLE -> {
                    AlertDialog(
                        onDismissRequest = { currentSession.hangUp(); center.clear() },
                        containerColor = c.card,
                        text = { Text(string(Res.string.call_needs_speech_model), color = c.ink) },
                        confirmButton = {
                            TextButton(onClick = { currentSession.hangUp(); center.clear() }) {
                                Text(string(Res.string.ok), color = c.accent)
                            }
                        },
                    )
                }
                else -> Unit
            }
        }

        // ENDED reason dialogs
        if (ui.phase == CallSteps.Phase.ENDED && ui.endedReason != null) {
            val endMessage = when (ui.endedReason) {
                CallSteps.EndReason.MIC_DENIED -> string(Res.string.call_mic_denied)
                CallSteps.EndReason.NO_STT -> string(Res.string.call_needs_speech_model)
                CallSteps.EndReason.BACKGROUND -> string(Res.string.call_ended_background)
                CallSteps.EndReason.INTERRUPTED -> string(Res.string.call_ended_interrupted)
                CallSteps.EndReason.QUIET -> string(Res.string.call_ended_quiet)
                else -> null
            }
            if (endMessage != null) {
                AlertDialog(
                    onDismissRequest = { center.clear() },
                    containerColor = c.card,
                    text = { Text(endMessage, color = c.ink) },
                    confirmButton = {
                        TextButton(onClick = { center.clear() }) {
                            Text(string(Res.string.ok), color = c.accent)
                        }
                    },
                )
            }
        }
    }
}
