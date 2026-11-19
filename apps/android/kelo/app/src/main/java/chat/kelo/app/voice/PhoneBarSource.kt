package chat.kelo.app.voice

import android.os.SystemClock
import androidx.compose.runtime.snapshotFlow
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** The phone's sound level as points on a fixed pitch (pure, so it is tested without the recognizer). */
object PhoneBarSampler {
    /**
     * The level at each tick in (fromMs, untilMs] that is a multiple of [everyMs]: the newest observation at or before the tick
     * (0 before the first observation). [observations] are (time in ms, level), oldest first.
     */
    fun sample(observations: List<Pair<Long, Float>>, everyMs: Long, fromMs: Long, untilMs: Long): List<Float> {
        val out = ArrayList<Float>()
        var i = 0
        var current = 0f
        var tick = (fromMs / everyMs + 1) * everyMs
        while (tick <= untilMs) {
            while (i < observations.size && observations[i].first <= tick) {
                current = observations[i].second
                i++
            }
            out += current
            tick += everyMs
        }
        return out
    }
}

/**
 * The phone's own recognizer (SpeechInput) as a recording source of the composer: while it listens, the composer draws the bar.
 * The words it hears stream into the field at the caret (never sent by themselves); [finish] and [discard] are driven by the bar
 * and the hold, and the step rules are [PhoneDictationSteps].
 */
class PhoneBarSource(
    private val scope: CoroutineScope,
    /** The composer's voice (the field the words go into). */
    private val composer: () -> ComposerVoice,
    /** The recognition ended: the problem, if it gave one (the caller shows the line and the haptics). */
    private val onEnded: (SpeechProblem?) -> Unit,
) : RecordingBarSource {
    /** The recognizer; set by [rememberAndroidComposerVoice], which makes it with callbacks that point back here. */
    lateinit var speech: SpeechInput
        internal set

    private var steps = PhoneDictationSteps.State(listening = false, after = null)
    private var startedAt = 0L
    private var pushedUntil = 0L
    private val observed = ArrayList<Pair<Long, Float>>()
    private var ticker: Job? = null
    private var watcher: Job? = null
    private var heldNow = false

    private val _bar = MutableStateFlow<RecordingBarState?>(null)
    override val bar: StateFlow<RecordingBarState?> = _bar.asStateFlow()

    override val trail = LevelTrail()

    /** True while the recognizer listens (the bar is up unless a hold keeps the composer). */
    val recording: Boolean get() = steps.listening

    /** A hold-to-talk finger is down: the bar is not drawn (see [RecordingBarRules.showBar]). */
    var held: Boolean
        get() = heldNow
        set(value) {
            heldNow = value
            publish()
        }

    /** Starts listening with the caret as the place for the words. False when nothing started. */
    fun start(): Boolean {
        if (steps.listening || !speech.available) return false
        composer().begin()
        steps = PhoneDictationSteps.step(steps, PhoneDictationSteps.Event.Start).first
        startedAt = SystemClock.elapsedRealtime()
        pushedUntil = 0L
        observed.clear()
        trail.clear()
        watcher = scope.launch {
            snapshotFlow { speech.level }.collect { observed += elapsed() to it }
        }
        ticker = scope.launch {
            while (isActive) {
                delay(TICK_MS)
                pushLevels()
            }
        }
        publish()
        speech.start()
        return true
    }

    /** ■ or ↑: the recognizer stops and its last words still arrive; ↑ sends them once the recognition ends. */
    override fun finish(after: AfterSpeech) {
        val (next, action) = PhoneDictationSteps.step(steps, PhoneDictationSteps.Event.Finish(after))
        steps = next
        if (action == PhoneDictationSteps.Action.STOP_RECOGNIZER) speech.stop()
    }

    /** ✕ or a slide off: the field goes back to what it was, and nothing is delivered. */
    override fun discard() {
        val (next, action) = PhoneDictationSteps.step(steps, PhoneDictationSteps.Event.Discard)
        steps = next
        if (action == PhoneDictationSteps.Action.RESTORE_FIELD) composer().restore()
        speech.cancel()   // its end comes back through [ended] with nothing to do
        stopTimers()
        publish()
    }

    /** The recognizer's words so far (partial or final): they go into the field at the caret. */
    internal fun words(text: String) {
        if (steps.listening) composer().deliver(text, AfterSpeech.FIELD)
    }

    /** The recognizer stopped. ↑ sends the field when nothing went wrong; a problem is shown by the caller and nothing is sent. */
    internal fun ended(problem: SpeechProblem?) {
        val (next, action) = PhoneDictationSteps.step(steps, PhoneDictationSteps.Event.Ended(problem != null))
        steps = next
        if (action == PhoneDictationSteps.Action.SEND_FIELD) composer().target?.let { t -> t.send(t.get().text) }
        stopTimers()
        trail.clear()
        publish()
        onEnded(problem)
    }

    private fun stopTimers() {
        ticker?.cancel()
        ticker = null
        watcher?.cancel()
        watcher = null
    }

    private fun elapsed(): Long = if (steps.listening) SystemClock.elapsedRealtime() - startedAt else 0L

    /** Moves the observed levels onto the trail, one per [TICK_MS]. */
    private fun pushLevels() {
        val now = elapsed()
        val levels = PhoneBarSampler.sample(observed, TICK_MS, pushedUntil, now)
        pushedUntil = (now / TICK_MS) * TICK_MS
        val at = System.nanoTime()
        levels.forEach { trail.push(it, at) }
        publish()
    }

    private fun publish() {
        _bar.value = if (steps.listening) {
            RecordingBarState(RecordingBarState.Stage.LISTENING, elapsedMs = elapsed(), endsInSeconds = null, held = heldNow)
        } else null
    }

    companion object {
        const val TICK_MS = 80L
    }
}
