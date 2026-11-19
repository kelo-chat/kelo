package chat.kelo.app.call

import chat.kelo.app.ui.character.KeloMood

/** The call's constants (brief call-mode §4.1). Each is one place to change: the owner may decide differently later. */
object CallRules {
    const val RING_ON_BUTTON = true          // C4: the header button shows the incoming screen
    const val RING_MS = 30_000L              // C16: the ringing stops and the call is declined after this
    const val QUIET_END_MS = 5 * 60_000L     // C8: the call ends after this long with nobody speaking
    const val BARGE_IN_PHONES = true         // C5 (desktop has no echo canceller: barge-in is off there by itself)
    const val PREFETCH = 2                   // sentences fetched ahead of the one playing
    const val PHONE_RETRY_MS = 1_000L        // the phone's recognizer waits this long after a try that heard nothing, doubling each time
    const val PHONE_RETRY_MAX_MS = 8_000L    // and never longer than this
    const val PHONE_ERRORS_NOTE = 3          // this many recognizer errors in a row show call_ears_failed on the call screen
    const val MODELS_WAIT_MS = 3_000L        // answering waits at most this long for the server's voice models to be read
    const val MUTE_WHEN_MINIMIZED = true     // call-mode §5.2: the mic is muted while the call is a pill over the chat (owner may flip)

    /** The wait before the phone's recognizer listens again after [misses] tries in a row that heard nothing (0: at once). */
    fun phoneRetryWait(misses: Int): Long =
        if (misses <= 0) 0L else minOf(PHONE_RETRY_MAX_MS, PHONE_RETRY_MS shl (minOf(misses, 10) - 1))

    const val ECHO_TAIL_MS = 250L            // the mic is ignored this long after playback stops (room echo)
    const val PHONE_END_SILENCE_MS = 1_800L  // the quiet the phone's recognizer is asked to wait for before it ends (iPhone: its own timer)

    /** A reply event that is an answer (text, a step, a card): after it a call's words are no longer taken back. */
    fun isAnswer(ev: chat.kelo.app.chat.TurnEvent): Boolean = when (ev) {
        is chat.kelo.app.chat.TurnEvent.Thinking, is chat.kelo.app.chat.TurnEvent.Retry, is chat.kelo.app.chat.TurnEvent.Reconnecting,
        is chat.kelo.app.chat.TurnEvent.Fallback, is chat.kelo.app.chat.TurnEvent.UserMessage, is chat.kelo.app.chat.TurnEvent.Reaction,
        is chat.kelo.app.chat.TurnEvent.Unknown, is chat.kelo.app.chat.TurnEvent.Done, is chat.kelo.app.chat.TurnEvent.Error -> false
        else -> true
    }

    /** The elapsed time of a call: "0:07", "12:34", "1:02:03". */
    fun clock(elapsedMs: Long): String {
        val total = (maxOf(0L, elapsedMs) / 1000)
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        val ss = if (s < 10) "0$s" else "$s"
        return if (h > 0) {
            val mm = if (m < 10) "0$m" else "$m"
            "$h:$mm:$ss"
        } else "$m:$ss"
    }

    /** The avatar's mood for a call phase. */
    fun mood(phase: CallSteps.Phase): KeloMood = when (phase) {
        CallSteps.Phase.LISTENING, CallSteps.Phase.HEARING -> KeloMood.Listening
        CallSteps.Phase.TRANSCRIBING, CallSteps.Phase.WAITING, CallSteps.Phase.CONNECTING -> KeloMood.Thinking
        CallSteps.Phase.SPEAKING -> KeloMood.Reading
        else -> KeloMood.Idle
    }
}
