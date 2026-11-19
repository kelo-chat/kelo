package chat.kelo.app.call

import org.junit.Assert.assertEquals
import org.junit.Test

class CallTimingsTest {
    @Test fun `full turn line format matches brief specification`() {
        val t = CallTimings()
        t.mark(CallTimings.Step.SPEECH_END, 1000)
        t.mark(CallTimings.Step.STT_DONE, 1420)
        t.mark(CallTimings.Step.TURN_STARTED, 1480)
        t.mark(CallTimings.Step.FIRST_TEXT, 3310)
        t.mark(CallTimings.Step.FIRST_SENTENCE, 3620)
        t.mark(CallTimings.Step.FIRST_AUDIO, 4140)
        t.mark(CallTimings.Step.PLAY_START, 4180)
        assertEquals("speech_end→stt=420 stt→turn=60 turn→text=1830 text→sentence=310 sentence→audio=520 audio→play=40 total=3180", t.line())
    }

    @Test fun `missing marks are omitted gracefully`() {
        val t = CallTimings()
        t.mark(CallTimings.Step.SPEECH_END, 1000)
        t.mark(CallTimings.Step.STT_DONE, 1420)
        // missing intermediate steps
        t.mark(CallTimings.Step.PLAY_START, 4180)
        assertEquals("speech_end→stt=420 total=3180", t.line())
    }

    @Test fun `empty timings returns empty line`() {
        val t = CallTimings()
        assertEquals("", t.line())
    }
}
