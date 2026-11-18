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

/**
 * Server voice input for one composer: record in memory, then send the WAV to the server when the owner stops. The text comes back
 * through [onText] (the caller puts it in the field at the caret; nothing is sent). The phase rules are [RecordingSteps]; the
 * controller only runs what the rules say. A recording that failed because the server could not be reached is kept for [retry];
 * [cancel] drops it, and so does leaving the chat (the caller drops the controller).
 */
class ServerMicController(
    private val recorder: AudioRecorder,
    /** The API client of the signed-in server; null while signed out (the recording then counts as offline). */
    private val voice: () -> VoiceApi?,
    private val scope: CoroutineScope,
    /** Called when a recording starts: remember where the words go (the caret). */
    private val onBegin: () -> Unit,
    /** The recognised words for the field (already trimmed, never empty). */
    private val onText: (String) -> Unit,
) {
    private val _phase = MutableStateFlow(RecordingSteps.Phase.IDLE)
    val phase: StateFlow<RecordingSteps.Phase> = _phase.asStateFlow()

    private val _bars = MutableStateFlow<List<Float>>(emptyList())
    /** The sound graph: the newest level last, one per 50 ms. */
    val bars: StateFlow<List<Float>> = _bars.asStateFlow()

    private val _elapsedMs = MutableStateFlow(0L)
    val elapsedMs: StateFlow<Long> = _elapsedMs.asStateFlow()

    private val _problem = MutableStateFlow<VoiceProblem?>(null)
    /** The one line under the composer: why the last recognition did not give text (null = nothing to show). */
    val problem: StateFlow<VoiceProblem?> = _problem.asStateFlow()

    private val bytes = AtomicLong(0)
    private val levels = ConcurrentLinkedQueue<Float>()
    private var tick: Job? = null
    private var sending: Job? = null
    private var pending: ByteArray? = null

    val available: Boolean get() = recorder.available

    val recording: Boolean get() = _phase.value == RecordingSteps.Phase.RECORDING

    /** Tap: start when idle, send when recording. */
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
        _bars.value = emptyList()
        _elapsedMs.value = 0
        val opened = recorder.start { chunk ->
            bytes.addAndGet(chunk.size.toLong())
            levels.add(SoundBars.rms(chunk, 0, chunk.size))
        }
        if (!opened) {
            _problem.value = VoiceProblem.FAILED
            return false
        }
        _phase.value = RecordingSteps.Phase.RECORDING
        tick = scope.launch {
            while (isActive) {
                delay(TICK_MS)
                val drained = ArrayList<Float>()
                while (true) drained.add(levels.poll() ?: break)
                if (drained.isNotEmpty()) drained.forEach { _bars.value = SoundBars.push(_bars.value, it) }
                val elapsed = bytes.get() * 1000L / BYTES_PER_SECOND
                _elapsedMs.value = elapsed
                val step = RecordingSteps.step(_phase.value, RecordingSteps.Event.Tick(elapsed))
                if (step.action == RecordingSteps.Action.CLOSE_AND_SEND) {   // the two-minute cap
                    stop()
                    break
                }
            }
        }
        return true
    }

    /** Tap again, or the hold is released on the button: send what was said. */
    fun stop() {
        val step = RecordingSteps.step(_phase.value, RecordingSteps.Event.Stop)
        if (step.action != RecordingSteps.Action.CLOSE_AND_SEND) return
        tick?.cancel()
        tick = null
        val wav = Wav.encode(recorder.stop())
        _phase.value = RecordingSteps.Phase.SENDING
        send(wav)
    }

    /** Slide off, or ✕: drops the recording (or the reply that is on its way). */
    fun cancel() {
        if (_phase.value == RecordingSteps.Phase.RECORDING) recorder.cancel()
        tick?.cancel()
        tick = null
        sending?.cancel()
        sending = null
        pending = null
        _bars.value = emptyList()
        _elapsedMs.value = 0
        _phase.value = RecordingSteps.Phase.IDLE
    }

    /** The owner asks again after the server could not be reached: the same recording goes up once more. */
    fun retry() {
        val wav = pending ?: return
        pending = null
        _problem.value = null
        _phase.value = RecordingSteps.Phase.SENDING
        send(wav)
    }

    fun clearProblem() {
        _problem.value = null
    }

    private fun send(wav: ByteArray) {
        sending = scope.launch {
            try {
                val client = voice() ?: throw ApiError(ApiError.NO_RESPONSE, "not signed in")
                val text = client.transcribe(wav).trim()
                if (text.isEmpty()) {
                    _problem.value = VoiceProblem.NOTHING_HEARD
                } else {
                    onText(text)
                }
                _phase.value = RecordingSteps.Phase.IDLE
            } catch (e: ApiError) {
                // a lost connection keeps the recording for "다시 하기"; any other answer drops it
                pending = if (e.isNetwork) wav else null
                _problem.value = voiceProblem(if (e.isNetwork) null else e.status, e.code)
                _phase.value = RecordingSteps.Phase.IDLE
            }
            sending = null
        }
    }

    companion object {
        const val TICK_MS = 50L
        /** 16 kHz, 16-bit, mono. */
        const val BYTES_PER_SECOND = 32_000L
    }
}
