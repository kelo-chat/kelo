package chat.kelo.app.voice

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.focusable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.State
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.isShiftPressed
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.testTag
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.unit.sp
import chat.kelo.app.res.*
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion
import chat.kelo.app.ui.theme.pressShrink
import kotlinx.coroutines.flow.StateFlow
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/** Where the words go when the owner ends a recording: the field (■, a pause, the 2-minute cap) or sent at once (↑). */
enum class AfterSpeech { FIELD, SEND }

/** What the composer draws while the owner speaks. */
data class RecordingBarState(
    val stage: Stage,
    val elapsedMs: Long,
    /** Non-null from 1:50 (server path only). */
    val endsInSeconds: Int?,
    /** A hold-to-talk finger is down: keep the normal composer (the mic stays under the finger). */
    val held: Boolean,
) {
    enum class Stage { LISTENING, TURNING_TO_TEXT }
}

/** One recording way (the server or the phone's recognizer) as the bar sees it. */
interface RecordingBarSource {
    /** null = not recording. */
    val bar: StateFlow<RecordingBarState?>
    val trail: LevelTrail
    fun discard()
    fun finish(after: AfterSpeech)
}

object RecordingBarRules {
    /** One level per 80 ms of audio (12.5 per second). */
    const val WINDOW_MS = 80L
    const val FLOOR_DB = -55f
    const val CEIL_DB = -15f
    /** A loud bar fades over the next few bars instead of dropping at once. */
    const val RELEASE = 0.55f
    const val DOT_DP = 3f
    const val MAX_DP = 24f
    const val GAP_DP = 2f

    fun showBar(s: RecordingBarState?): Boolean = s != null && !s.held

    /** rms 0..1 → dBFS → (dB - FLOOR) / (CEIL - FLOOR), clamped to 0..1. Silence (rms <= 0) is 0. */
    fun level(rms: Float): Float {
        if (rms <= 0f) return 0f
        val db = 20f * log10(rms)
        return ((db - FLOOR_DB) / (CEIL_DB - FLOOR_DB)).coerceIn(0f, 1f)
    }

    fun height(level: Float): Float = DOT_DP + (MAX_DP - DOT_DP) * level.coerceIn(0f, 1f)

    fun visibleBars(widthDp: Float): Int = ((widthDp + GAP_DP) / (DOT_DP + GAP_DP)).toInt().coerceAtLeast(0)

    /** null before the countdown starts (1:50), else the whole seconds left until the 2-minute cap, never below 0. */
    fun endsIn(elapsedMs: Long): Int? {
        if (elapsedMs < RecordingSteps.COUNTDOWN_FROM_MS) return null
        val left = RecordingSteps.MAX_MS - elapsedMs
        return if (left <= 0L) 0 else ((left + 999L) / 1000L).toInt()
    }

    fun sendEnabled(stage: RecordingBarState.Stage, busy: Boolean): Boolean =
        stage == RecordingBarState.Stage.LISTENING && !busy

    fun clockSpoken(elapsedMs: Long): String = formatClock(elapsedMs)
}

/**
 * Turns 16 kHz PCM16 (little endian, mono) into one level per [RecordingBarRules.WINDOW_MS] of audio: the RMS of each full window,
 * mapped by [RecordingBarRules.level]. A window split between two chunks, or an odd byte at a chunk's end, carries over.
 */
class LevelMeter(private val sampleRate: Int = 16000) {
    private val samplesPerWindow = (sampleRate.toLong() * RecordingBarRules.WINDOW_MS / 1000L).toInt()
    private var sumSquares = 0.0
    private var samples = 0
    private var pendingByte = -1

    /** The finished windows' levels in this chunk, oldest first. */
    fun add(pcm16le: ByteArray): FloatArray {
        val out = ArrayList<Float>()
        var i = 0
        if (pendingByte >= 0 && pcm16le.isNotEmpty()) {
            consume(pendingByte or (pcm16le[0].toInt() shl 8), out)
            pendingByte = -1
            i = 1
        }
        while (i + 1 < pcm16le.size) {
            consume((pcm16le[i].toInt() and 0xff) or (pcm16le[i + 1].toInt() shl 8), out)
            i += 2
        }
        if (i < pcm16le.size) pendingByte = pcm16le[i].toInt() and 0xff
        return out.toFloatArray()
    }

    fun reset() {
        sumSquares = 0.0
        samples = 0
        pendingByte = -1
    }

    private fun consume(raw: Int, out: MutableList<Float>) {
        val s = raw.toShort().toDouble() / 32768.0
        sumSquares += s * s
        samples++
        if (samples == samplesPerWindow) {
            val rms = sqrt(sumSquares / samplesPerWindow).toFloat()
            out.add(RecordingBarRules.level(rms))
            sumSquares = 0.0
            samples = 0
        }
    }

    private companion object {
        const val SAMPLES_PER_WINDOW = (16_000L * RecordingBarRules.WINDOW_MS / 1000L).toInt()   // 1 280 samples = 80 ms
    }
}

/**
 * The newest levels, oldest first, capacity [capacity]. [push] applies the release: v = max(level, last * RELEASE), so a bar
 * rises at once and falls over a few bars. [version] changes on every push or clear, so a draw that reads it redraws.
 */
class LevelTrail(private val capacity: Int = 256) {
    private val values = FloatArray(capacity)
    private var head = 0          // index of the next write
    private var count = 0
    private var last = 0f
    private val versionState = mutableIntStateOf(0)

    var lastPushNanos: Long = 0L
        private set

    val version: Int get() = versionState.intValue

    /** The newest level pushed (after the release); 0 when empty. */
    val newest: Float get() = last

    fun push(level: Float, atNanos: Long = System.nanoTime()) {
        val v = max(level.coerceIn(0f, 1f), last * RecordingBarRules.RELEASE)
        last = v
        values[head] = v
        head = (head + 1) % capacity
        if (count < capacity) count++
        lastPushNanos = atNanos
        versionState.intValue = versionState.intValue + 1
    }

    /** Copies the newest levels, oldest → newest, into [out] (from index 0). Returns how many were written. */
    fun copyInto(out: FloatArray): Int {
        val n = minOf(count, out.size)
        val start = (head - count + capacity) % capacity
        val skip = count - n
        for (k in 0 until n) out[k] = values[(start + skip + k) % capacity]
        return n
    }

    fun clear() {
        head = 0
        count = 0
        last = 0f
        lastPushNanos = 0L
        versionState.intValue = versionState.intValue + 1
    }
}

/** The phone recognizer's steps for the bar. The recognizer itself stays platform code and runs the actions it is told. */
object PhoneDictationSteps {
    enum class Action { NONE, STOP_RECOGNIZER, SEND_FIELD, RESTORE_FIELD }

    /** after = what the owner asked for (set by ■ or ↑); null while nothing was asked. */
    data class State(val listening: Boolean, val after: AfterSpeech?)

    sealed interface Event {
        data object Start : Event
        data class Finish(val after: AfterSpeech) : Event
        data object Discard : Event
        /** The recognizer stopped; [problem] = it reported an error (its own line shows). */
        data class Ended(val problem: Boolean) : Event
    }

    fun step(s: State, e: Event): Pair<State, Action> = when (e) {
        Event.Start -> State(listening = true, after = null) to Action.NONE
        is Event.Finish ->
            if (s.listening) State(listening = true, after = e.after) to Action.STOP_RECOGNIZER
            else s to Action.NONE
        Event.Discard ->
            if (s.listening) State(listening = false, after = null) to Action.RESTORE_FIELD
            else State(listening = false, after = null) to Action.NONE
        is Event.Ended -> {
            val done = State(listening = false, after = null)
            when {
                e.problem -> done to Action.NONE                       // nothing is sent, even after ↑
                s.after == AfterSpeech.SEND -> done to Action.SEND_FIELD
                else -> done to Action.NONE
            }
        }
    }
}

/**
 * The composer while the owner speaks: one rounded row in place of the field (the composer's card and padding stay). Left to right:
 * ✕ (drop the recording), the clock, the live sound graph (or "글자로 바꾸는 중…" while the text comes back), ■ (the words go into the
 * field), ↑ (the words are sent, when Kelo is not replying). No placeholder, no "+", no model chip, no text labels.
 */
@Composable
fun RecordingBar(source: RecordingBarSource, state: RecordingBarState, busy: Boolean, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val listening = state.stage == RecordingBarState.Stage.LISTENING
    val clock = RecordingBarRules.clockSpoken(state.elapsedMs)
    val desktop = LocalFormFactor.current == FormFactor.Desktop
    val focus = remember { FocusRequester() }
    if (desktop) LaunchedEffect(Unit) { focus.requestFocus() }
    val counting = state.endsInSeconds != null
    val stateText = string(if (listening) Res.string.voice_listening else Res.string.voice_transcribing)
    val clockText = string(Res.string.voice_elapsed, clock)
    val endsText = state.endsInSeconds?.let { string(Res.string.voice_ends_in, it) }
    Row(
        modifier
            .fillMaxWidth()
            .height(56.dp)
            .padding(horizontal = 8.dp)
            .testTag("voice.bar")
            .semantics {
                liveRegion = LiveRegionMode.Polite
                stateDescription = stateText
            }
            .then(if (desktop) Modifier.focusRequester(focus).focusable().onPreviewKeyEvent { e ->
                if (e.type != KeyEventType.KeyDown) return@onPreviewKeyEvent false
                when {
                    e.key == Key.Escape -> { source.discard(); true }
                    (e.key == Key.Enter || e.key == Key.NumPadEnter) && !e.isShiftPressed -> {
                        if (RecordingBarRules.sendEnabled(state.stage, busy)) source.finish(AfterSpeech.SEND)
                        true
                    }
                    else -> false
                }
            } else Modifier),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        BarButton(KeloIcons.Close, iconSize = 20.dp, background = c.neutralSoft, tint = c.ink, enabled = true,
            label = string(Res.string.voice_cancel_recording), tag = "voice.discard") { source.discard() }
        Text(
            clock,
            style = MaterialTheme.typography.bodyMedium.copy(fontFeatureSettings = "tnum"),
            color = if (counting) c.dangerInk else c.muted,
            modifier = Modifier.widthIn(min = 36.dp).semantics {
                contentDescription = clockText
                endsText?.let { stateDescription = it }
            },
        )
        if (listening) {
            Waveform(source.trail, active = true, modifier = Modifier.weight(1f).height(28.dp).clearAndSetSemantics { })
        } else {
            Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(modifier = Modifier.size(16.dp), strokeWidth = 2.dp, color = c.accent)
                Text(string(Res.string.voice_transcribing), style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1,
                    overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
            }
        }
        BarButton(KeloIcons.StopFilled, iconSize = 16.dp, background = c.neutralSoft, tint = c.ink, enabled = listening,
            label = string(Res.string.voice_stop_recording), tag = "voice.stop") { source.finish(AfterSpeech.FIELD) }
        BarButton(KeloIcons.Send, iconSize = 20.dp, background = c.ink, tint = c.bg, enabled = RecordingBarRules.sendEnabled(state.stage, busy),
            label = string(Res.string.voice_send_now), tag = "voice.send") { source.finish(AfterSpeech.SEND) }
    }
}

/** A 40 dp round button with the composer's press shrink. */
@Composable
private fun BarButton(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    iconSize: androidx.compose.ui.unit.Dp,
    background: Color,
    tint: Color,
    enabled: Boolean,
    label: String,
    tag: String,
    onClick: () -> Unit,
) {
    val c = Kelo.colors
    val source = remember { MutableInteractionSource() }
    IconButton(
        onClick = onClick,
        enabled = enabled,
        interactionSource = source,
        colors = IconButtonDefaults.iconButtonColors(
            containerColor = background, contentColor = tint, disabledContainerColor = c.disabledFill, disabledContentColor = c.disabledInk,
        ),
        modifier = Modifier.size(40.dp).clip(androidx.compose.foundation.shape.CircleShape).pressShrink(source).testTag(tag),
    ) {
        Icon(icon, contentDescription = label, modifier = Modifier.size(iconSize))
    }
}

/**
 * The sound graph: one small rounded bar per 80 ms, newest at the end edge, drawn in one Canvas (no composable per bar). It moves
 * on each frame while [active] and not reduce-motion; the bars jump one pitch per level otherwise.
 */
@Composable
internal fun Waveform(trail: LevelTrail, active: Boolean, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val reduce = LocalReduceMotion.current
    val values = remember { FloatArray(256) }
    val frame = remember { mutableLongStateOf(0L) }
    LaunchedEffect(active, reduce) {
        if (active && !reduce) while (true) withFrameNanos { frame.longValue = it }
    }
    val density = LocalDensity.current
    val rtl = LocalLayoutDirection.current == LayoutDirection.Rtl
    Canvas(modifier) {
        frame.longValue          // a frame redraws the bars
        trail.version            // a new level redraws them
        val n = trail.copyInto(values)
        val pitch = with(density) { (RecordingBarRules.DOT_DP + RecordingBarRules.GAP_DP).dp.toPx() }
        val barW = with(density) { RecordingBarRules.DOT_DP.dp.toPx() }
        val radius = CornerRadius(barW / 2, barW / 2)
        val age = if (reduce) 1f else ((System.nanoTime() - trail.lastPushNanos) / 80_000_000f).coerceIn(0f, 1f)
        val shift = pitch * age
        val cy = size.height / 2
        val w = size.width
        for (k in 0 until n) {
            val level = values[n - 1 - k]
            val h = with(density) { RecordingBarRules.height(level).dp.toPx() }
            var x = w - barW - k * pitch - shift
            if (rtl) x = w - x - barW
            if (x + barW < 0f || x > w) continue
            drawRoundRect(
                color = lerp(c.muted, c.ink, level),
                topLeft = Offset(x, cy - h / 2),
                size = Size(barW, h),
                cornerRadius = radius,
            )
        }
    }
}
