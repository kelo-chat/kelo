package chat.kelo.app.voice

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.window.Popup
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import chat.kelo.app.core.KeloApi
import chat.kelo.app.platform.AudioRecorder
import chat.kelo.app.ui.icons.KeloIcons
import kotlinx.coroutines.launch
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.text.Text as KeloText

/** The one line for a recognition that gave no text (under the composer or in the page). */
@Composable
fun voiceProblemLine(problem: VoiceProblem): String = when (problem) {
    VoiceProblem.NOTHING_HEARD -> string(Res.string.voice_no_match)
    VoiceProblem.OFFLINE -> string(Res.string.voice_server_offline)
    VoiceProblem.BUSY -> string(Res.string.voice_server_busy)
    VoiceProblem.TOO_LONG -> string(Res.string.voice_server_too_long)
    VoiceProblem.NOT_READY -> string(Res.string.voice_server_download_failed)
    VoiceProblem.FAILED -> string(Res.string.voice_dictation_failed)
}

/** The one line after a recognition gave no text, with "다시 하기" when the server could not be reached (the recording is kept). */
@Composable
fun ServerProblemCard(controller: ServerMicController, problem: VoiceProblem, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Row(
        modifier
            .width(300.dp)
            .clip(RoundedCornerShape(16.dp))
            .background(c.card)
            .padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        KeloText(voiceProblemLine(problem), color = c.ink, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
        if (problem == VoiceProblem.OFFLINE) {
            TextAction(string(Res.string.voice_retry), c.accent) { controller.retry() }
        } else {
            TextAction(string(Res.string.ok), c.accent) { controller.clearProblem() }
        }
    }
}

/** The first-tap question: "<model> 음성 인식 모델을 Kelo 서버에 받을까요?" with 확인 / 취소 (one dialog, no licence text). */
@Composable
fun VoiceOfferDialog(model: ModelView, onYes: () -> Unit, onNo: () -> Unit) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onNo,
        containerColor = c.card,
        text = { KeloText(string(Res.string.voice_server_offer_body, model.name), color = c.ink) },
        confirmButton = { TextButton(onClick = onYes) { KeloText(string(Res.string.ok), color = c.accent) } },
        dismissButton = { TextButton(onClick = onNo) { KeloText(string(Res.string.cancel), color = c.ink) } },
    )
}

/** Downloading line (percent) for the mic's tooltip and My → 음성 입력. */
@Composable
fun voiceDownloadingLine(done: Long, total: Long): String {
    val percent = if (total <= 0) 0 else ((done * 100) / total).toInt().coerceIn(0, 100)
    return string(Res.string.voice_server_downloading, percent)
}

@Composable
private fun TextAction(text: String, color: androidx.compose.ui.graphics.Color, onClick: () -> Unit) {
    TextButton(onClick = onClick, contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 6.dp)) {
        KeloText(text, color = color, style = MaterialTheme.typography.labelLarge)
    }
}

/** m:ss, the way the sound graph's clock reads. */
fun formatClock(ms: Long): String {
    val total = (ms / 1000).coerceAtLeast(0)
    return "${total / 60}:${(total % 60).toString().padStart(2, '0')}"
}

/**
 * The mic for a computer (no phone recognizer): it shows only when the server's speech model is ready or can be offered or is being
 * downloaded; tap starts and stops the recording (see [ServerMicController]). Nothing is drawn when the way is NONE or PHONE.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerMicHost(
    controller: ServerMicController,
    voice: VoiceModel,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val view by voice.view.collectAsState()
    var askOffer by remember { mutableStateOf(false) }
    val way = DictationWay.pick(view, phoneRecognizer = false)
    val shown = controller.available && (way == DictationWay.Way.SERVER || way == DictationWay.Way.OFFER_THEN_NONE || way == DictationWay.Way.DOWNLOADING_NONE)
    val phase by controller.phase.collectAsState()
    val problem by controller.problem.collectAsState()
    val tooltip = rememberTooltipState(isPersistent = false)
    val downloading = view?.state == "downloading"
    val tip = if (downloading) voiceDownloadingLine(view?.progress?.done ?: 0, view?.progress?.total ?: 0) else string(Res.string.voice_unavailable)
    if (shown) {
        val recording = phase == RecordingSteps.Phase.RECORDING
        Box(modifier) {
            TooltipBox(
                positionProvider = TooltipDefaults.rememberPlainTooltipPositionProvider(),
                tooltip = { PlainTooltip { KeloText(tip, color = c.ink) } },
                state = tooltip,
            ) {
                val ring = VoiceDownloadRing.of(view)
                if (ring != null) {
                    // the speech model downloads: the mic is a circle with the progress (a spinner when the server gives no number)
                    val label = voiceDownloadingLine(view?.progress?.done ?: 0, view?.progress?.total ?: 0)
                    Box(
                        Modifier.size(40.dp).semantics { contentDescription = label },
                        contentAlignment = Alignment.Center,
                    ) {
                        val percent = ring.percent
                        if (percent != null) {
                            CircularProgressIndicator(progress = { percent / 100f }, modifier = Modifier.size(22.dp), color = c.accent, strokeWidth = 2.dp)
                        } else {
                            CircularProgressIndicator(modifier = Modifier.size(22.dp), color = c.accent, strokeWidth = 2.dp)
                        }
                    }
                } else IconButton(onClick = {
                    when (way) {
                        DictationWay.Way.OFFER_THEN_NONE -> askOffer = true
                        DictationWay.Way.SERVER -> controller.toggle()
                        else -> scope.launch { tooltip.show() }
                    }
                }) {
                    Icon(KeloIcons.Mic, contentDescription = string(if (recording) Res.string.voice_stop else Res.string.voice_start), tint = if (recording) c.accent else c.muted)
                }
            }
            // while the owner speaks the composer itself is the recording bar (ComposerVoice); only a problem line shows here
            problem?.let { p ->
                Popup(alignment = Alignment.TopEnd) { ServerProblemCard(controller, p, Modifier.padding(bottom = 8.dp)) }
            }
        }
    }
    val offered = view?.recommended
    if (askOffer && offered != null) {
        VoiceOfferDialog(
            model = offered,
            onYes = { askOffer = false; voice.choose(offered.id) },
            onNo = { askOffer = false; voice.decline() },
        )
    }
}
