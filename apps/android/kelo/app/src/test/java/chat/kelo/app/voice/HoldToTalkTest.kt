package chat.kelo.app.voice

import chat.kelo.app.voice.HoldToTalk.Action
import chat.kelo.app.voice.HoldToTalk.Event
import chat.kelo.app.voice.HoldToTalk.Phase
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HoldToTalkTest {
    /** Plays [events] from IDLE and returns the actions that were not NONE, with the last phase. */
    private fun play(vararg events: Event): Pair<List<Action>, Phase> {
        var phase = Phase.IDLE
        val actions = mutableListOf<Action>()
        for (e in events) {
            val s = HoldToTalk.step(phase, e)
            phase = s.phase
            if (s.action != Action.NONE) actions += s.action
        }
        return actions to phase
    }

    @Test fun `a short touch is a tap`() =
        assertEquals(listOf(Action.TAP) to Phase.IDLE, play(Event.Down(false), Event.Up))

    @Test fun `a tap while listening is the tap that stops it`() =
        assertEquals(listOf(Action.TAP) to Phase.IDLE, play(Event.Down(true), Event.Up))

    @Test fun `holding starts dictating and letting go on the button finishes`() =
        assertEquals(listOf(Action.START, Action.FINISH) to Phase.IDLE, play(Event.Down(false), Event.HoldElapsed, Event.Up))

    @Test fun `letting go after sliding off the button cancels`() =
        assertEquals(
            listOf(Action.START, Action.CANCEL) to Phase.IDLE,
            play(Event.Down(false), Event.HoldElapsed, Event.Move(false), Event.Up),
        )

    @Test fun `sliding back onto the button before letting go finishes again`() =
        assertEquals(
            listOf(Action.START, Action.FINISH) to Phase.IDLE,
            play(Event.Down(false), Event.HoldElapsed, Event.Move(false), Event.Move(true), Event.Up),
        )

    @Test fun `the phase shows whether the finger is on the button while held`() {
        val held = HoldToTalk.step(Phase.PRESSED, Event.HoldElapsed)
        assertEquals(Phase.HOLDING, held.phase)
        assertEquals(Phase.HOLDING_AWAY, HoldToTalk.step(held.phase, Event.Move(false)).phase)
        assertEquals(Phase.HOLDING, HoldToTalk.step(Phase.HOLDING_AWAY, Event.Move(true)).phase)
    }

    @Test fun `the system taking the touch away while held cancels`() =
        assertEquals(listOf(Action.START, Action.CANCEL) to Phase.IDLE, play(Event.Down(false), Event.HoldElapsed, Event.Cancel))

    @Test fun `the system taking the touch away before the hold does nothing`() =
        assertEquals(emptyList<Action>() to Phase.IDLE, play(Event.Down(false), Event.Cancel))

    @Test fun `sliding off before the hold time does nothing, not even a tap`() =
        assertEquals(emptyList<Action>() to Phase.IDLE, play(Event.Down(false), Event.Move(false), Event.Up))

    @Test fun `staying on the button before the hold time changes nothing`() =
        assertEquals(listOf(Action.TAP) to Phase.IDLE, play(Event.Down(false), Event.Move(true), Event.Up))

    @Test fun `a hold that begins while listening already started by a tap does not start again`() {
        val s = HoldToTalk.step(Phase.PRESSED_LISTENING, Event.HoldElapsed)
        assertEquals(Action.NONE, s.action)
        assertEquals(Phase.PRESSED_LISTENING, s.phase)
        // and lifting it is the tap that stops listening
        assertEquals(Action.TAP, HoldToTalk.step(s.phase, Event.Up).action)
    }

    @Test fun `the hold time is not the tap time`() = assertTrue(HoldToTalk.HOLD_MS in 200L..600L)

    @Test fun `on the button means inside its bounds plus the slack`() {
        assertTrue(HoldToTalk.onButton(24f, 24f, 48f, 48f, slack = 0f))
        assertFalse(HoldToTalk.onButton(60f, 24f, 48f, 48f, slack = 0f))
        assertTrue(HoldToTalk.onButton(60f, 24f, 48f, 48f, slack = 24f))
        assertFalse(HoldToTalk.onButton(24f, -40f, 48f, 48f, slack = 24f))
        assertTrue(HoldToTalk.onButton(-10f, 50f, 48f, 48f, slack = 24f))
    }
}
