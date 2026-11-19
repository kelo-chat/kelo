package chat.kelo.app.voice

import chat.kelo.app.platform.AudioRecorder
import chat.kelo.app.core.ApiError
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.atomic.AtomicLong

/** Turns a WAV file into text: the server's speech endpoint ([VoiceApi] implements it; tests use a fake). */
interface Transcriber {
    suspend fun transcribe(wav: ByteArray): String
}

/**
 * Server voice input for one composer: record in memory, then send the WAV to the server when the owner ends it. The text comes back
 * through [onText] with what the owner asked for: [AfterSpeech.FIELD] (■, a pause, the two-minute cap: the caller puts it in the field
 * at the caret) or [AfterSpeech.SEND] (↑: the caller sends it). The phase rules are [RecordingSteps]; the controller only runs what
 * they say. A recording that failed because the server could not be reached is kept for [retry] (which then puts the text in the
 * field); [discard] drops it, and so does leaving the chat (the caller drops the controller).
 *
 * It is also the server's [RecordingBarSource]: [bar] is what the composer draws while the owner speaks.
 */
class ServerMicController(
    private val recorder: AudioRecorder,
    /** The transcriber of the signed-in server; null while signed out (the recording then counts as offline). */
    private val transcriber: () -> Transcriber?,
    private val scope: CoroutineScope,
    /** Called when a recording starts: remember where the words go (the caret). */
    private val onBegin: () -> Unit,
    /** The recognised words (already trimmed, never empty) and what the owner asked for. */
    private val onText: (String, AfterSpeech) -> Unit,
) : RecordingBarSource {
    private val _phase = MutableStateFlow(RecordingSteps.Phase.IDLE)
    val phase: StateFlow<RecordingSteps.Phase> = _phase.asStateFlow()

    private val _elapsedMs = MutableStateFlow(0L)
    val elapsedMs: StateFlow<Long> = _elapsedMs.asStateFlow()

    private val _problem = MutableStateFlow<VoiceProblem?>(null)
    /** The one line under the composer: why the last recognition did not give text (null = nothing to show). */
    val problem: StateFlow<VoiceProblem?> = _problem.asStateFlow()

    private val _bar = MutableStateFlow<RecordingBarState?>(null)
    override val bar: StateFlow<RecordingBarState?> = _bar.asStateFlow()

    override val trail = LevelTrail()

    /** A hold-to-talk finger is down: the composer keeps its normal look (the bar is not drawn). */
    var held: Boolean = false
        set(value) {
            field = value
            publish()
        }

    private val bytes = AtomicLong(0)
    private val meter = LevelMeter()
    private val levels = ConcurrentLinkedQueue<Float>()
    private var tick: Job? = null
    private var sending: Job? = null
    private var pending: ByteArray? = null

    val available: Boolean get() = recorder.available

    val recording: Boolean get() = _phase.value == RecordingSteps.Phase.RECORDING

    /** Tap: start when idle, stop and put the words in the field when recording. */
    fun toggle() {
        if (_phase.value == RecordingSteps.Phase.RECORDING) stop() else start()
    }

    /** False when nothing started (no microphone, or the line did not open). */
    fun start(): Boolean {
        val step = RecordingSteps.step(_phase.value, RecordingSteps.Event.Start)
        if (step.action != RecordingSteps.Action.OPEN_MIC) return false
        pending = null
        _problem.value = null
        onBegin()
        bytes.set(0)
        levels.clear()
        meter.reset()
        trail.clear()
        _elapsedMs.value = 0
        val opened = recorder.start { chunk ->
            bytes.addAndGet(chunk.size.toLong())
            // the level of each 80 ms window; the tick below moves them onto the trail
            meter.add(chunk).forEach { levels.add(it) }
        }
        if (!opened) {
            _problem.value = VoiceProblem.FAILED
            return false
        }
        _phase.value = RecordingSteps.Phase.RECORDING
        publish()
        tick = scope.launch {
            while (isActive) {
                delay(TICK_MS)
                val now = System.nanoTime()
                while (true) {
                    val level = levels.poll() ?: break
                    trail.push(level, now)
                }
                val elapsed = bytes.get() * 1000L / BYTES_PER_SECOND
                _elapsedMs.value = elapsed
                publish()
                val step = RecordingSteps.step(_phase.value, RecordingSteps.Event.Tick(elapsed))
                if (step.action == RecordingSteps.Action.CLOSE_AND_SEND) {   // the two-minute cap: into the field, never sent
                    finish(AfterSpeech.FIELD)
                    break
                }
            }
        }
        return true
    }

    /** ■, the mic tap again, or the hold is released on the button: the words go into the field. */
    fun stop() = finish(AfterSpeech.FIELD)

    /** Ends the recording: the WAV goes to the server, and the reply goes to [onText] with [after]. */
    override fun finish(after: AfterSpeech) {
        val step = RecordingSteps.step(_phase.value, RecordingSteps.Event.Stop)
        if (step.action != RecordingSteps.Action.CLOSE_AND_SEND) return
        tick?.cancel()
        tick = null
        val wav = Wav.encode(recorder.stop())
        _phase.value = RecordingSteps.Phase.SENDING
        publish()
        send(wav, after)
    }

    /** Same as [discard]: ✕ or a slide off (the older popup calls it this way). */
    fun cancel() = discard()

    /** ✕ or a slide off: drops the recording, or the reply that is on its way. */
    override fun discard() {
        if (_phase.value == RecordingSteps.Phase.RECORDING) recorder.cancel()
        tick?.cancel()
        tick = null
        sending?.cancel()
        sending = null
        pending = null
        meter.reset()
        levels.clear()
        trail.clear()
        _elapsedMs.value = 0
        _phase.value = RecordingSteps.Phase.IDLE
        publish()
    }

    /** The owner asks again after the server could not be reached: the same recording goes up once more, and lands in the field. */
    fun retry() {
        val wav = pending ?: return
        pending = null
        _problem.value = null
        _phase.value = RecordingSteps.Phase.SENDING
        publish()
        send(wav, AfterSpeech.FIELD)
    }

    fun clearProblem() {
        _problem.value = null
    }

    private fun send(wav: ByteArray, after: AfterSpeech) {
        sending = scope.launch {
            try {
                val client = transcriber() ?: throw ApiError(ApiError.NO_RESPONSE, "not signed in")
                val text = client.transcribe(wav).trim()
                if (text.isEmpty()) {
                    _problem.value = VoiceProblem.NOTHING_HEARD
                } else {
                    onText(text, after)
                }
                _phase.value = RecordingSteps.Phase.IDLE
            } catch (e: ApiError) {
                // a lost connection keeps the recording for "다시 하기"; any other answer drops it
                pending = if (e.isNetwork) wav else null
                _problem.value = voiceProblem(if (e.isNetwork) null else e.status, e.code)
                _phase.value = RecordingSteps.Phase.IDLE
            }
            sending = null
            publish()
        }
    }

    /** The bar follows the phase, the clock and the hold: IDLE → none, RECORDING → listening, SENDING → turning into text. */
    private fun publish() {
        val stage = when (_phase.value) {
            RecordingSteps.Phase.IDLE -> null
            RecordingSteps.Phase.RECORDING -> RecordingBarState.Stage.LISTENING
            RecordingSteps.Phase.SENDING -> RecordingBarState.Stage.TURNING_TO_TEXT
        }
        val elapsed = _elapsedMs.value
        _bar.value = stage?.let {
            RecordingBarState(stage = it, elapsedMs = elapsed, endsInSeconds = RecordingBarRules.endsIn(elapsed), held = held)
        }
    }

    companion object {
        const val TICK_MS = 50L
        /** 16 kHz, 16-bit, mono. */
        const val BYTES_PER_SECOND = 32_000L
    }
}
