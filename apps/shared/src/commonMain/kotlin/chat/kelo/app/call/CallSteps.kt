package chat.kelo.app.call

/**
 * The call's state machine (brief call-mode §4.4). Pure: an event in, the new state and the actions out. The app runs the actions.
 * Any event not in the table changes nothing.
 */
object CallSteps {
    enum class Phase { RINGING, PREPARING, CONNECTING, LISTENING, HEARING, TRANSCRIBING, WAITING, SPEAKING, ENDED }
    enum class EndReason { DECLINED, MISSED, HUNG_UP, BACKGROUND, INTERRUPTED, QUIET, NO_STT, MIC_DENIED, FAILED }

    sealed interface Event {
        data object Accept : Event
        data object Decline : Event
        data object RingTimeout : Event
        data object Prepared : Event                       // STT ready (+ the voice question answered), mic open
        data class NotPossible(val why: EndReason) : Event
        data object TurnStarted : Event                    // a reply of this call started (answered, or after the owner's words)
        data object SpeechStarted : Event
        data class SpeechEnded(val pcm: ByteArray) : Event
        data object SpeechDropped : Event
        data class Transcribed(val text: String) : Event
        data class TranscribeFailed(val offline: Boolean) : Event
        data object FirstSentence : Event                  // the speech queue started playing
        data class QueueIdle(val replyBusy: Boolean) : Event // nothing left to play
        data object ReplyEnded : Event
        data object HangUp : Event
        data object Background : Event
        data object Interrupted : Event
        data object QuietTimeout : Event
    }

    enum class Action { NONE, OPEN_MIC, CLOSE_MIC, SEND_ANSWERED, TRANSCRIBE, SEND_TEXT, HOLD_TEXT, STOP_PLAYBACK, END }

    data class State(
        val phase: Phase,
        val held: String? = null,
        val replyBusy: Boolean = false,
        val ended: EndReason? = null,
    )

    fun step(s: State, e: Event, bargeIn: Boolean): Pair<State, List<Action>> {
        if (s.phase == Phase.ENDED) return s to emptyList()
        val p = s.phase
        return when (e) {
            is Event.HangUp -> end(s, EndReason.HUNG_UP, stop = true)
            is Event.Background -> end(s, EndReason.BACKGROUND, stop = true)
            is Event.Interrupted -> end(s, EndReason.INTERRUPTED, stop = true)
            is Event.QuietTimeout -> end(s, EndReason.QUIET, stop = true)
            is Event.Accept -> if (p == Phase.RINGING) s.copy(phase = Phase.PREPARING) to emptyList() else s to emptyList()
            is Event.Decline, is Event.RingTimeout ->
                if (p == Phase.RINGING) {
                    val why = if (e is Event.Decline) EndReason.DECLINED else EndReason.MISSED
                    s.copy(phase = Phase.ENDED, ended = why) to listOf(Action.END)
                } else s to emptyList()
            is Event.Prepared ->
                if (p == Phase.PREPARING) s.copy(phase = Phase.CONNECTING) to listOf(Action.OPEN_MIC, Action.SEND_ANSWERED)
                else s to emptyList()
            is Event.NotPossible ->
                if (p == Phase.PREPARING) s.copy(phase = Phase.ENDED, ended = e.why) to listOf(Action.END)
                else s to emptyList()
            is Event.TurnStarted ->
                if (p == Phase.CONNECTING || p == Phase.WAITING) s.copy(phase = Phase.WAITING, replyBusy = true) to emptyList()
                else s to emptyList()
            is Event.FirstSentence ->
                if (p == Phase.WAITING) s.copy(phase = Phase.SPEAKING) to emptyList() else s to emptyList()
            is Event.QueueIdle ->
                if (p == Phase.SPEAKING) s.copy(phase = if (e.replyBusy) Phase.WAITING else Phase.LISTENING, replyBusy = e.replyBusy) to emptyList()
                else s to emptyList()
            is Event.ReplyEnded -> replyEnded(s)
            is Event.SpeechStarted -> when (p) {
                Phase.LISTENING -> s.copy(phase = Phase.HEARING) to emptyList()
                Phase.WAITING -> s.copy(phase = Phase.HEARING) to emptyList()
                Phase.SPEAKING -> if (bargeIn) s.copy(phase = Phase.HEARING) to listOf(Action.STOP_PLAYBACK) else s to emptyList()
                else -> s to emptyList()
            }
            is Event.SpeechEnded ->
                if (p == Phase.HEARING) s.copy(phase = Phase.TRANSCRIBING) to listOf(Action.TRANSCRIBE) else s to emptyList()
            is Event.SpeechDropped ->
                if (p == Phase.HEARING) s.copy(phase = idlePhase(s)) to emptyList() else s to emptyList()
            is Event.Transcribed ->
                if (p != Phase.TRANSCRIBING && p != Phase.LISTENING && p != Phase.HEARING) s to emptyList()
                else if (e.text.isEmpty()) s.copy(phase = idlePhase(s)) to emptyList()
                else if (s.replyBusy) s.copy(phase = Phase.WAITING, held = e.text) to listOf(Action.HOLD_TEXT)
                else s.copy(phase = Phase.WAITING) to listOf(Action.SEND_TEXT)
            is Event.TranscribeFailed ->
                if (p == Phase.TRANSCRIBING) s.copy(phase = Phase.LISTENING) to emptyList() else s to emptyList()
        }
    }

    /** Where the call goes when the owner's turn ends without words: back to waiting while a reply runs, else listening. */
    private fun idlePhase(s: State): Phase = if (s.replyBusy) Phase.WAITING else Phase.LISTENING

    private fun replyEnded(s: State): Pair<State, List<Action>> {
        if (s.phase != Phase.WAITING && s.phase != Phase.SPEAKING) return s to emptyList()
        val held = s.held
        if (held != null) {
            // The owner's words held during the reply go out now; the next reply starts with TurnStarted.
            return s.copy(phase = Phase.WAITING, held = null, replyBusy = false) to listOf(Action.SEND_TEXT)
        }
        val next = when (s.phase) {
            Phase.WAITING -> Phase.LISTENING   // nothing is playing and nothing is held
            else -> Phase.SPEAKING             // still playing: QueueIdle moves on to listening
        }
        return s.copy(phase = next, replyBusy = false) to emptyList()
    }

    private fun end(s: State, why: EndReason, stop: Boolean): Pair<State, List<Action>> {
        val actions = buildList {
            if (stop) add(Action.STOP_PLAYBACK)
            add(Action.CLOSE_MIC)
            add(Action.END)
        }
        return s.copy(phase = Phase.ENDED, ended = why) to actions
    }
}
