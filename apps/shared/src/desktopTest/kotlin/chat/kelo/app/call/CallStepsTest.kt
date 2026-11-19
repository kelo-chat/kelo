package chat.kelo.app.call

import chat.kelo.app.call.CallSteps.Action
import chat.kelo.app.call.CallSteps.EndReason
import chat.kelo.app.call.CallSteps.Event
import chat.kelo.app.call.CallSteps.Phase
import chat.kelo.app.call.CallSteps.State
import org.junit.Assert.assertEquals
import org.junit.Test

class CallStepsTest {
    private fun s(phase: Phase, held: String? = null, busy: Boolean = false) = State(phase, held, busy)
    private fun run(from: State, e: Event, bargeIn: Boolean = true) = CallSteps.step(from, e, bargeIn)

    @Test fun `ringing accept goes to preparing, decline and ring timeout end it`() {
        assertEquals(s(Phase.PREPARING) to emptyList<Action>(), run(s(Phase.RINGING), Event.Accept))
        assertEquals(State(Phase.ENDED, ended = EndReason.DECLINED) to listOf(Action.END), run(s(Phase.RINGING), Event.Decline))
        assertEquals(State(Phase.ENDED, ended = EndReason.MISSED) to listOf(Action.END), run(s(Phase.RINGING), Event.RingTimeout))
    }

    @Test fun `prepared opens the mic and sends the answer`() {
        assertEquals(s(Phase.CONNECTING) to listOf(Action.OPEN_MIC, Action.SEND_ANSWERED), run(s(Phase.PREPARING), Event.Prepared))
    }

    @Test fun `not possible while preparing ends with its reason`() {
        assertEquals(State(Phase.ENDED, ended = EndReason.NO_STT) to listOf(Action.END),
            run(s(Phase.PREPARING), Event.NotPossible(EndReason.NO_STT)))
    }

    @Test fun `turn started while connecting or waiting marks a busy reply`() {
        assertEquals(s(Phase.WAITING, busy = true), run(s(Phase.CONNECTING), Event.TurnStarted).first)
        assertEquals(s(Phase.WAITING, busy = true), run(s(Phase.WAITING), Event.TurnStarted).first)
    }

    @Test fun `first sentence while waiting starts speaking`() {
        assertEquals(Phase.SPEAKING, run(s(Phase.WAITING, busy = true), Event.FirstSentence).first.phase)
    }

    @Test fun `queue idle goes to waiting while the reply runs, else listening`() {
        assertEquals(Phase.WAITING, run(s(Phase.SPEAKING, busy = true), Event.QueueIdle(true)).first.phase)
        assertEquals(Phase.LISTENING, run(s(Phase.SPEAKING), Event.QueueIdle(false)).first.phase)
    }

    @Test fun `reply ended with nothing queued listens again`() {
        assertEquals(Phase.LISTENING, run(s(Phase.WAITING, busy = true), Event.ReplyEnded).first.phase)
    }

    @Test fun `listening then speech starts hearing, end transcribes`() {
        val (h, a0) = run(s(Phase.LISTENING), Event.SpeechStarted)
        assertEquals(Phase.HEARING, h.phase); assertEquals(emptyList<Action>(), a0)
        assertEquals(Phase.TRANSCRIBING to listOf(Action.TRANSCRIBE),
            run(h, Event.SpeechEnded(ByteArray(0))).let { it.first.phase to it.second })
    }

    @Test fun `speech while speaking is barge-in only when allowed`() {
        assertEquals(Phase.HEARING to listOf(Action.STOP_PLAYBACK), run(s(Phase.SPEAKING), Event.SpeechStarted, bargeIn = true).let { it.first.phase to it.second })
        assertEquals(s(Phase.SPEAKING) to emptyList<Action>(), run(s(Phase.SPEAKING), Event.SpeechStarted, bargeIn = false))
    }

    @Test fun `speech while waiting hears and keeps the reply busy`() {
        assertEquals(s(Phase.HEARING, busy = true), run(s(Phase.WAITING, busy = true), Event.SpeechStarted).first)
    }

    @Test fun `dropped speech goes back to waiting or listening`() {
        assertEquals(Phase.WAITING, run(s(Phase.HEARING, busy = true), Event.SpeechDropped).first.phase)
        assertEquals(Phase.LISTENING, run(s(Phase.HEARING), Event.SpeechDropped).first.phase)
    }

    @Test fun `empty transcript is noise`() {
        assertEquals(Phase.LISTENING, run(s(Phase.TRANSCRIBING), Event.Transcribed("")).first.phase)
        assertEquals(Phase.WAITING, run(s(Phase.TRANSCRIBING, busy = true), Event.Transcribed("")).first.phase)
    }

    @Test fun `text while idle is sent now`() {
        assertEquals(Phase.WAITING to listOf(Action.SEND_TEXT),
            run(s(Phase.TRANSCRIBING), Event.Transcribed("hi")).let { it.first.phase to it.second })
    }

    @Test fun `text during a reply is held and the newest replaces the older`() {
        val (h1, a1) = run(s(Phase.TRANSCRIBING, busy = true), Event.Transcribed("first"))
        assertEquals(listOf(Action.HOLD_TEXT), a1); assertEquals("first", h1.held)
        val (h2, _) = run(h1.copy(phase = Phase.TRANSCRIBING), Event.Transcribed("second"))
        assertEquals("second", h2.held)
    }

    @Test fun `held text is sent when the reply ends`() {
        val (after, actions) = run(s(Phase.SPEAKING, held = "later", busy = true), Event.ReplyEnded)
        assertEquals(listOf(Action.SEND_TEXT), actions)
        assertEquals(Phase.WAITING, after.phase); assertEquals(null, after.held)
    }

    @Test fun `transcribe failure goes back to listening`() {
        assertEquals(Phase.LISTENING, run(s(Phase.TRANSCRIBING), Event.TranscribeFailed(offline = true)).first.phase)
    }

    @Test fun `hang up in any phase ends the call and stops playback`() {
        for (p in Phase.values().filter { it != Phase.ENDED }) {
            val (next, actions) = run(s(p), Event.HangUp)
            assertEquals(Phase.ENDED, next.phase)
            assertEquals(EndReason.HUNG_UP, next.ended)
            assertEquals(listOf(Action.STOP_PLAYBACK, Action.CLOSE_MIC, Action.END), actions)
        }
    }

    @Test fun `background, interruption and quiet timeout end with their reason`() {
        assertEquals(EndReason.BACKGROUND, run(s(Phase.LISTENING), Event.Background).first.ended)
        assertEquals(EndReason.INTERRUPTED, run(s(Phase.SPEAKING), Event.Interrupted).first.ended)
        assertEquals(EndReason.QUIET, run(s(Phase.LISTENING), Event.QuietTimeout).first.ended)
    }

    @Test fun `an ended call changes nothing`() {
        val ended = State(Phase.ENDED, ended = EndReason.HUNG_UP)
        assertEquals(ended to emptyList<Action>(), run(ended, Event.SpeechStarted))
    }
}
