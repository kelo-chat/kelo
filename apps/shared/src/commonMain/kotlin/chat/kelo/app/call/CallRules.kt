package chat.kelo.app.call

import chat.kelo.app.ui.character.KeloMood

/** The call's constants (brief call-mode §4.1). Each is one place to change: the owner may decide differently later. */
object CallRules {
    const val RING_ON_BUTTON = true          // C4: the header button shows the incoming screen
    const val RING_MS = 30_000L              // C16: the ringing stops and the call is declined after this
    const val QUIET_END_MS = 5 * 60_000L     // C8: the call ends after this long with nobody speaking
    const val BARGE_IN_PHONES = true         // C5 (desktop has no echo canceller: barge-in is off there by itself)
    const val PREFETCH = 2                   // sentences fetched ahead of the one playing
    const val ECHO_TAIL_MS = 250L            // the mic is ignored this long after playback stops (room echo)

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
