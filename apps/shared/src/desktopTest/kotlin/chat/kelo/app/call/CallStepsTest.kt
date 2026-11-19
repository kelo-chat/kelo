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

    @Test fun `text during a reply is held and joined to the earlier`() {
        val (h1, a1) = run(s(Phase.TRANSCRIBING, busy = true), Event.Transcribed("first"))
        assertEquals(listOf(Action.HOLD_TEXT), a1); assertEquals("first", h1.held)
        val (h2, _) = run(h1.copy(phase = Phase.TRANSCRIBING), Event.Transcribed("second"))
        assertEquals("first second", h2.held)
    }

    @Test fun `speech that goes on while the words are turned into text joins them into one turn`() {   // owner 2026-11-20
        var st = State(Phase.LISTENING)
        fun go(e: Event): List<Action> { val (n, a) = CallSteps.step(st, e, true); st = n; return a }
        go(Event.SpeechStarted)
        assertEquals(listOf(Action.TRANSCRIBE), go(Event.SpeechEnded(ByteArray(0))))
        go(Event.SpeechStarted); assertEquals(Phase.HEARING, st.phase)
        assertEquals(emptyList<Action>(), go(Event.Transcribed("나 목소리"))); assertEquals("나 목소리", st.held)
        assertEquals(listOf(Action.TRANSCRIBE), go(Event.SpeechEnded(ByteArray(0))))
        assertEquals(listOf(Action.SEND_TEXT), go(Event.Transcribed("너무 끊겨")))
        assertEquals(CallSteps.Outgoing("나 목소리 너무 끊겨", amend = false), st.sent)
        assertEquals("나 목소리 너무 끊겨", st.unanswered); assertEquals(null, st.held)
    }

    @Test fun `a cough after the first words still sends them`() {
        val heard = run(State(Phase.TRANSCRIBING, inFlight = 1), Event.SpeechStarted).first
        val gathered = run(heard, Event.Transcribed("hi")).first
        val (sent, a) = run(gathered, Event.SpeechDropped)
        assertEquals(listOf(Action.SEND_TEXT), a); assertEquals(CallSteps.Outgoing("hi", false), sent.sent)
    }

    @Test fun `talking again before Kelo answers takes the words back and sends them again with the rest`() {
        val (taking, a1) = run(State(Phase.WAITING, unanswered = "나 목소리"), Event.SpeechStarted)
        assertEquals(listOf(Action.TAKE_BACK), a1); assertEquals("나 목소리", taking.again); assertEquals(null, taking.unanswered)
        val freed = run(taking.copy(replyBusy = true), Event.ReplyEnded).first        // the stopped turn ends while the owner talks
        assertEquals(false, freed.replyBusy); assertEquals("나 목소리", freed.again)
        val ending = run(freed, Event.SpeechEnded(ByteArray(0))).first
        val (done, a2) = run(ending, Event.Transcribed("너무 끊겨"))
        assertEquals(listOf(Action.SEND_TEXT), a2); assertEquals(CallSteps.Outgoing("나 목소리 너무 끊겨", amend = true), done.sent)
        assertEquals(CallSteps.Outgoing("나 목소리", amend = true), run(State(Phase.HEARING, again = "나 목소리"), Event.SpeechDropped).first.sent)
        assertEquals(null, run(State(Phase.HEARING, again = "x"), Event.TakeBackRefused).first.again)
    }

    @Test fun `once Kelo answered new words wait for the next turn`() {
        val answered = run(State(Phase.WAITING, unanswered = "a"), Event.Answered).first
        assertEquals(null, answered.unanswered)
        assertEquals(emptyList<Action>(), run(answered, Event.SpeechStarted).second)
    }

    @Test fun `a reply that ends while the owner talks frees their words`() {   // they were held forever before
        val freed = run(State(Phase.HEARING, replyBusy = true), Event.ReplyEnded)
        assertEquals(false, freed.first.replyBusy); assertEquals(emptyList<Action>(), freed.second)
        val ending = run(freed.first, Event.SpeechEnded(ByteArray(0))).first
        assertEquals(listOf(Action.SEND_TEXT), run(ending, Event.Transcribed("x")).second)
    }

    @Test fun `refused amend words are held as an amend`() {
        val (s1, a1) = run(State(Phase.WAITING), Event.SendRefused("a b", amend = true))
        assertEquals(listOf(Action.HOLD_TEXT), a1); assertEquals("a b", s1.again)
        assertEquals(CallSteps.Outgoing("a b", amend = true), run(s1, Event.ReplyEnded).first.sent)
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

    @Test fun `refused words are held until the chat is free`() {
        // 2026-11-19: sendFromCall refused while a typed reply ran and the owner's words were dropped.
        val (s1, a1) = CallSteps.step(CallSteps.State(CallSteps.Phase.WAITING), CallSteps.Event.SendRefused("hi"), true)
        assertEquals(CallSteps.State(CallSteps.Phase.WAITING, held = "hi", replyBusy = true), s1)
        assertEquals(listOf(CallSteps.Action.HOLD_TEXT), a1)
        val (s2, a2) = CallSteps.step(s1, CallSteps.Event.ReplyEnded, true)
        assertEquals(CallSteps.Phase.WAITING, s2.phase)
        assertEquals(null, s2.held)
        assertEquals(listOf(CallSteps.Action.SEND_TEXT), a2)
    }

    @Test fun `typed words go now while listening and wait while Kelo answers`() {
        val (s1, a1) = CallSteps.step(State(Phase.LISTENING), Event.Typed("hi"), true)
        assertEquals(Phase.WAITING, s1.phase); assertEquals(listOf(Action.SEND_TEXT), a1)
        val (s2, a2) = CallSteps.step(State(Phase.SPEAKING), Event.Typed("more"), true)
        assertEquals(State(Phase.SPEAKING, held = "more", replyBusy = true), s2); assertEquals(listOf(Action.HOLD_TEXT), a2)
        val (s3, a3) = CallSteps.step(s2, Event.QueueIdle(replyBusy = false), true)
        assertEquals(Phase.WAITING, s3.phase); assertEquals(null, s3.held); assertEquals(listOf(Action.SEND_TEXT), a3)
        assertEquals(emptyList<Action>(), CallSteps.step(State(Phase.RINGING), Event.Typed("x"), true).second)
    }

    @Test fun `a question that stops the turn goes back to listening, and words spoken then are held`() {
        val waiting = run(s(Phase.WAITING, busy = true), Event.QuestionOpened).first
        assertEquals(Phase.LISTENING, waiting.phase)
        assertEquals(true, waiting.replyBusy)
        val (heard, actions) = run(waiting, Event.Transcribed("hello"))
        assertEquals(Phase.WAITING, heard.phase)
        assertEquals("hello", heard.held)
        assertEquals(listOf(Action.HOLD_TEXT), actions)
    }

    @Test fun `the question closing while listening frees the call, and held words go out`() {
        val freed = run(s(Phase.LISTENING, busy = true), Event.ReplyEnded).first
        assertEquals(Phase.LISTENING, freed.phase)
        assertEquals(false, freed.replyBusy)
        val held = run(s(Phase.WAITING, held = "hello", busy = true), Event.ReplyEnded)
        assertEquals(listOf(Action.SEND_TEXT), held.second)
    }
}
