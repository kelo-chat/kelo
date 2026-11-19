package chat.kelo.app.call

import chat.kelo.app.voice.VoiceView
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CallLogicTest {
    private fun stt(state: String, runtime: String = "ready") = VoiceView(state = state, runtime = runtime)
    private fun voice(state: String, runtime: String = "ready") = SpeechView(state = state, runtime = runtime, languages = listOf("en", "ko", "pt"))

    @Test fun `server speech language needs ready state, runtime and a supported primary subtag`() {
        assertEquals("pt", SpeechLang.server("pt-BR", voice("ready")))
        assertEquals("ko", SpeechLang.server("ko", voice("ready")))
        assertNull(SpeechLang.server("zh-Hans", voice("ready")))
        assertNull(SpeechLang.server("ko", voice("downloading")))
        assertNull(SpeechLang.server("ko", null))
        assertNull(SpeechLang.server("ko", voice("ready", runtime = "missing")))
    }

    @Test fun `clock formats`() {
        assertEquals("0:07", CallRules.clock(7_000))
        assertEquals("12:34", CallRules.clock(754_000))
        assertEquals("1:02:03", CallRules.clock(3_723_000))
        assertEquals("0:00", CallRules.clock(-5))
    }

    @Test fun `moods follow the phase`() {
        assertEquals(chat.kelo.app.ui.character.KeloMood.Listening, CallRules.mood(CallSteps.Phase.HEARING))
        assertEquals(chat.kelo.app.ui.character.KeloMood.Thinking, CallRules.mood(CallSteps.Phase.WAITING))
        assertEquals(chat.kelo.app.ui.character.KeloMood.Reading, CallRules.mood(CallSteps.Phase.SPEAKING))
        assertEquals(chat.kelo.app.ui.character.KeloMood.Idle, CallRules.mood(CallSteps.Phase.RINGING))
    }

    @Test fun `ears prefer the server when ready, then the phone, then nobody`() {
        assertEquals(CallEars.Way.SERVER, CallEars.pick(stt("ready"), phoneRecognizer = true))
        assertEquals(CallEars.Way.PHONE, CallEars.pick(stt("downloading"), phoneRecognizer = true))
        assertEquals(CallEars.Way.PHONE, CallEars.pick(stt("ready", runtime = "missing"), phoneRecognizer = true))
        assertEquals(CallEars.Way.NONE, CallEars.pick(stt("failed"), phoneRecognizer = false))
        assertEquals(CallEars.Way.NONE, CallEars.pick(null, phoneRecognizer = false))
    }

    @Test fun `readiness asks the stt question first, then the voice question once`() {
        assertEquals(CallReadiness.Need.MIC_PERMISSION,
            CallReadiness.next(stt("ready"), voice("ready"), micAllowed = false, phoneRecognizer = true, askedStt = false, askedVoice = false))
        assertEquals(CallReadiness.Need.STT_QUESTION,
            CallReadiness.next(stt("offered"), voice("offered"), true, true, askedStt = false, askedVoice = false))
        assertEquals(CallReadiness.Need.VOICE_QUESTION,
            CallReadiness.next(stt("offered"), voice("offered"), true, true, askedStt = true, askedVoice = false))
        assertEquals(CallReadiness.Need.NOTHING,
            CallReadiness.next(stt("offered"), voice("offered"), true, true, askedStt = true, askedVoice = true))
    }

    @Test fun `a declined voice is asked on the first call only`() {
        assertEquals(CallReadiness.Need.VOICE_QUESTION,
            CallReadiness.next(stt("ready"), voice("off"), true, true, askedStt = true, askedVoice = false))
        assertEquals(CallReadiness.Need.NOTHING,
            CallReadiness.next(stt("ready"), voice("off"), true, true, askedStt = true, askedVoice = true))
    }

    @Test fun `desktop without a server stt cannot take a call`() {
        assertEquals(CallReadiness.Need.IMPOSSIBLE,
            CallReadiness.next(stt("failed"), voice("ready"), true, phoneRecognizer = false, askedStt = true, askedVoice = true))
    }

    @Test fun `the better-voice line shows once the device voice spoke and the voice is not ready`() {
        assertTrue(CallVoiceLine.show(voice("downloading"), deviceSpokeThisCall = true, dismissed = false))
        assertFalse(CallVoiceLine.show(voice("ready"), deviceSpokeThisCall = true, dismissed = false))
        assertFalse(CallVoiceLine.show(voice("offered"), deviceSpokeThisCall = false, dismissed = false))
        assertFalse(CallVoiceLine.show(voice("offered"), deviceSpokeThisCall = true, dismissed = true))
        assertFalse(CallVoiceLine.show(null, deviceSpokeThisCall = true, dismissed = false))
    }

    @Test fun `availability is false on desktop without server stt and true on a phone with a recognizer`() {
        assertFalse(CallAvailability.of(stt("failed"), micAvailable = true, phoneRecognizer = false))
        assertTrue(CallAvailability.of(stt("failed"), micAvailable = true, phoneRecognizer = true))
        assertTrue(CallAvailability.of(stt("offered"), micAvailable = true, phoneRecognizer = false))
        assertFalse(CallAvailability.of(stt("ready"), micAvailable = false, phoneRecognizer = true))
    }
}
