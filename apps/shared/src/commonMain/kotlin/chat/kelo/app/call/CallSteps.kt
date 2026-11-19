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
        data class SendRefused(val text: String, val amend: Boolean = false) : Event   // the chat could not take the words now
        data class Typed(val text: String) : Event         // words typed into the call (the keyboard)
        data object QuestionOpened : Event                 // a secret or consent question stopped the call's turn: listen again, words held
        data object Answered : Event                       // the first answer of this call's turn came (text, a step, a card)
        data object TakeBackRefused : Event                // the chat could not take back the unanswered turn (it was answered meanwhile)
        data object HangUp : Event
        data object Background : Event
        data object Interrupted : Event
        data object QuietTimeout : Event
    }

    enum class Action { NONE, OPEN_MIC, CLOSE_MIC, SEND_ANSWERED, TRANSCRIBE, SEND_TEXT, HOLD_TEXT, STOP_PLAYBACK, END, TAKE_BACK }

    /** What a SEND_TEXT sends: [text], and [amend] = it replaces the call's unanswered words (POST /api/turns "amend"). */
    data class Outgoing(val text: String, val amend: Boolean)

    data class State(
        val phase: Phase,
        val held: String? = null,           // the owner's words not sent yet (heard or typed)
        val replyBusy: Boolean = false,
        val ended: EndReason? = null,
        val inFlight: Int = 0,              // pieces of the owner's speech still being turned into text
        val again: String? = null,          // words of a taken-back turn: sent again in front of [held], as an amend
        val unanswered: String? = null,     // the words of the call's last sent turn while nothing of the answer has come
        val sent: Outgoing? = null,         // what this step's SEND_TEXT sends
    )

    fun step(s: State, e: Event, bargeIn: Boolean): Pair<State, List<Action>> {
        if (s.phase == Phase.ENDED) return s to emptyList()
        return next(s.copy(sent = null), e, bargeIn)
    }

    private fun next(s: State, e: Event, bargeIn: Boolean): Pair<State, List<Action>> {
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
                if (p != Phase.SPEAKING) s to emptyList()
                else if (!e.replyBusy && words(s) != null) send(s.copy(replyBusy = false))
                else s.copy(phase = if (e.replyBusy) Phase.WAITING else Phase.LISTENING, replyBusy = e.replyBusy) to emptyList()
            is Event.Typed -> when (p) {
                Phase.LISTENING, Phase.HEARING, Phase.TRANSCRIBING -> {
                    val t = s.copy(held = join(s.held, e.text))
                    if (t.replyBusy) t.copy(phase = Phase.WAITING) to listOf(Action.HOLD_TEXT) else send(t)
                }
                Phase.WAITING, Phase.SPEAKING -> s.copy(held = join(s.held, e.text), replyBusy = true) to listOf(Action.HOLD_TEXT)
                else -> s to emptyList()
            }
            is Event.SendRefused ->
                if (p != Phase.WAITING) s to emptyList()
                else (if (e.amend) s.copy(again = e.text) else s.copy(held = join(e.text, s.held)))
                    .copy(replyBusy = true, unanswered = null) to listOf(Action.HOLD_TEXT)
            is Event.ReplyEnded -> replyEnded(s)
            is Event.QuestionOpened ->
                if (p == Phase.WAITING) s.copy(phase = Phase.LISTENING, replyBusy = true, unanswered = null) to emptyList() else s to emptyList()
            is Event.Answered -> s.copy(unanswered = null) to emptyList()
            is Event.TakeBackRefused -> s.copy(again = null) to emptyList()
            is Event.SpeechStarted -> when (p) {
                Phase.LISTENING, Phase.TRANSCRIBING -> s.copy(phase = Phase.HEARING) to emptyList()
                Phase.WAITING ->   // the owner goes on before Kelo answered: those words come back to be sent with the rest (2026-11-20)
                    if (s.unanswered != null) s.copy(phase = Phase.HEARING, again = s.unanswered, unanswered = null) to listOf(Action.TAKE_BACK)
                    else s.copy(phase = Phase.HEARING) to emptyList()
                Phase.SPEAKING -> if (bargeIn) s.copy(phase = Phase.HEARING) to listOf(Action.STOP_PLAYBACK) else s to emptyList()
                else -> s to emptyList()
            }
            is Event.SpeechEnded ->
                if (p == Phase.HEARING) s.copy(phase = Phase.TRANSCRIBING, inFlight = s.inFlight + 1) to listOf(Action.TRANSCRIBE)
                else s to emptyList()
            is Event.SpeechDropped ->
                if (p != Phase.HEARING) s to emptyList()
                else if (s.inFlight > 0) s.copy(phase = Phase.TRANSCRIBING) to emptyList()
                else finish(s)
            is Event.Transcribed -> {
                val takes = p == Phase.TRANSCRIBING || p == Phase.LISTENING || p == Phase.HEARING || (p == Phase.WAITING && s.inFlight > 0)
                if (!takes) return s to emptyList()
                val t = s.copy(inFlight = maxOf(0, s.inFlight - 1), held = join(s.held, e.text))
                when {
                    p == Phase.HEARING && s.inFlight > 0 -> t to emptyList()   // the owner talks again: these words wait for the rest
                    t.inFlight > 0 -> (if (p == Phase.WAITING) t else t.copy(phase = Phase.TRANSCRIBING)) to emptyList()
                    else -> finish(t)
                }
            }
            is Event.TranscribeFailed -> {
                val takes = p == Phase.TRANSCRIBING || p == Phase.HEARING || (p == Phase.WAITING && s.inFlight > 0)
                if (!takes) return s to emptyList()
                val t = s.copy(inFlight = maxOf(0, s.inFlight - 1))
                when {
                    p != Phase.TRANSCRIBING || t.inFlight > 0 -> t to emptyList()
                    words(t) != null -> finish(t)
                    else -> t.copy(phase = Phase.LISTENING) to emptyList()
                }
            }
        }
    }

    /** Words joined with one space, blank parts left out; null when nothing is left. */
    fun join(vararg parts: String?): String? =
        parts.mapNotNull { it?.trim()?.takeIf(String::isNotEmpty) }.joinToString(" ").ifEmpty { null }

    private fun words(s: State): String? = join(s.again, s.held)

    /** The owner finished: their words go now, or wait for the running reply; nothing heard goes back to idle. */
    private fun finish(s: State): Pair<State, List<Action>> = when {
        words(s) == null -> s.copy(phase = idlePhase(s), held = null, again = null) to emptyList()
        s.replyBusy -> s.copy(phase = Phase.WAITING) to listOf(Action.HOLD_TEXT)
        else -> send(s)
    }

    private fun send(s: State): Pair<State, List<Action>> {
        val text = words(s) ?: return s.copy(phase = idlePhase(s)) to emptyList()
        return s.copy(phase = Phase.WAITING, held = null, again = null, unanswered = text, sent = Outgoing(text, amend = s.again != null)) to
            listOf(Action.SEND_TEXT)
    }

    private fun replyEnded(s: State): Pair<State, List<Action>> {
        val free = s.copy(replyBusy = false, unanswered = null)
        return when (s.phase) {
            Phase.HEARING, Phase.TRANSCRIBING -> free to emptyList()   // the owner's words go once they finish (2026-11-20)
            Phase.LISTENING, Phase.WAITING -> if (words(s) != null) send(free) else free.copy(phase = Phase.LISTENING) to emptyList()
            Phase.SPEAKING -> if (words(s) != null) send(free) else free to emptyList()   // still playing: QueueIdle moves on to listening
            else -> s to emptyList()
        }
    }

    /** Where the call goes when the owner's turn ends without words: back to waiting while a reply runs, else listening. */
    private fun idlePhase(s: State): Phase = if (s.replyBusy) Phase.WAITING else Phase.LISTENING

    private fun end(s: State, why: EndReason, stop: Boolean): Pair<State, List<Action>> {
        val actions = buildList {
            if (stop) add(Action.STOP_PLAYBACK)
            add(Action.CLOSE_MIC)
            add(Action.END)
        }
        return s.copy(phase = Phase.ENDED, ended = why) to actions
    }
}
