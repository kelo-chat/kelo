package chat.kelo.app.voice

import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.TextFieldValue
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class ComposerVoiceTest {
    private class Field(var value: TextFieldValue) {
        val sent = mutableListOf<String>()
        val target get() = VoiceTarget(get = { value }, set = { value = it }, send = { sent += it })
    }

    private fun voice() = ComposerVoice(emptyList())

    @Test fun fieldDeliveryMergesAtTheCaretAndDoesNotSend() {
        val field = Field(TextFieldValue("hello world", TextRange(5)))
        val voice = voice()
        voice.target = field.target
        voice.begin()
        voice.deliver("big", AfterSpeech.FIELD)
        assertEquals("hello big world", field.value.text)
        assertTrue(field.sent.isEmpty())
    }

    @Test fun sendDeliveryMergesAndSendsTheMergedText() {
        val field = Field(TextFieldValue("hello", TextRange(5)))
        val voice = voice()
        voice.target = field.target
        voice.begin()
        voice.deliver("world", AfterSpeech.SEND)
        assertEquals(listOf("hello world"), field.sent)
        assertEquals("hello world", field.value.text)
    }

    @Test fun noTargetIsNoCrash() {
        voice().apply {
            begin()
            deliver("words", AfterSpeech.SEND)
        }
    }
}
