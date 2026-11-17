package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReadAloudTest {
    private class FakeEngine : SpeechEngine {
        val spoken = mutableListOf<Pair<String, String>>()
        var stops = 0
        /** the end callback of each text handed over, in order */
        val ends = mutableListOf<() -> Unit>()
        override fun speak(text: String, language: String, onDone: () -> Unit) { spoken += text to language; ends += onDone }
        override fun stop() { stops++ }
        /** the engine reaches the end of the newest text */
        fun finish() { ends.last().invoke() }
    }

    private fun reader(engine: FakeEngine) = ReadAloud(engine) { "en-US" }

    @Test fun `reading a message speaks its plain text in its language`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("안녕하세요, **오늘** 일정이에요.")
        assertEquals(listOf("안녕하세요, 오늘 일정이에요." to "ko"), e.spoken)
        assertEquals("안녕하세요, **오늘** 일정이에요.", r.reading)
    }

    @Test fun `tapping the same message again stops it`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("Hello there.")
        r.toggle("Hello there.")
        assertNull(r.reading)
        assertEquals(1, e.spoken.size)
        assertTrue(e.stops >= 2)
    }

    @Test fun `only one message speaks at a time`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("First one.")
        val stopsBefore = e.stops
        r.toggle("Second one.")
        assertEquals("Second one.", r.reading)
        assertEquals(listOf("First one." to "en-US", "Second one." to "en-US"), e.spoken)
        assertTrue(e.stops > stopsBefore)   // the first is cut off before the second starts
    }

    @Test fun `reading ends by itself when the engine is done`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("Done soon.")
        e.finish()
        assertNull(r.reading)
    }

    @Test fun `the end of a text that was cut off does not clear the one reading now`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("One.")
        r.toggle("Two.")
        e.ends[0]()   // the old text's end arrives late
        assertEquals("Two.", r.reading)
        e.ends[1]()
        assertNull(r.reading)
    }

    @Test fun `leaving the screen stops reading`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("Anything.")
        r.stop()
        assertNull(r.reading)
        assertTrue(e.stops > 0)
    }

    @Test fun `a message with nothing to say is not spoken`() {
        val e = FakeEngine(); val r = reader(e)
        r.toggle("```kt\nval x = 1\n```")
        assertNull(r.reading)
        assertTrue(e.spoken.isEmpty())
    }

    @Test fun `without an engine the action is not offered`() {
        val all = listOf(MsgAction.COPY, MsgAction.READ_ALOUD, MsgAction.SHARE)
        assertEquals(all, MessageActions.offered(all, canShare = true, canSpeak = true))
        assertEquals(listOf(MsgAction.COPY, MsgAction.SHARE), MessageActions.offered(all, canShare = true, canSpeak = false))
    }
}
