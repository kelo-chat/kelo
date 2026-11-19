package chat.kelo.app.voice

import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import org.junit.Assert.assertEquals
import org.junit.Test

/** 2026-11-19: errors of an old recognizer or a cancelled one restarted the listening; a real client error stopped it for good. */
class CallPhoneListenerRulesTest {
    private fun out(error: Int, cancelled: Boolean = false, current: Boolean = true) = CallPhoneListenerRules.outcome(error, cancelled, current)

    @Test fun theRecognizerIsAskedToWaitForAClearPause() {   // owner 2026-11-20: sentences were cut at their pauses
        val e = CallPhoneListenerRules.silenceExtras()
        assertEquals(1_800L, e[RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS])
        assertEquals(1_800L, e[RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS])
    }

    @Test fun `no match and silence are nothing heard`() {
        assertEquals(CallPhoneListenerRules.Outcome.NOTHING_HEARD, out(SpeechRecognizer.ERROR_NO_MATCH))
        assertEquals(CallPhoneListenerRules.Outcome.NOTHING_HEARD, out(SpeechRecognizer.ERROR_SPEECH_TIMEOUT))
    }
    @Test fun `other errors of the current recognizer are failures, the client error included`() {
        assertEquals(CallPhoneListenerRules.Outcome.FAILED, out(SpeechRecognizer.ERROR_CLIENT))
        assertEquals(CallPhoneListenerRules.Outcome.FAILED, out(SpeechRecognizer.ERROR_RECOGNIZER_BUSY))
        assertEquals(CallPhoneListenerRules.Outcome.FAILED, out(SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS))
    }
    @Test fun `a cancelled or an old recognizer reports nothing`() {
        assertEquals(CallPhoneListenerRules.Outcome.IGNORE, out(SpeechRecognizer.ERROR_NO_MATCH, cancelled = true))
        assertEquals(CallPhoneListenerRules.Outcome.IGNORE, out(SpeechRecognizer.ERROR_CLIENT, current = false))
    }
}
