package chat.kelo.app.voice

import kotlin.test.Test
import kotlin.test.assertEquals

class VoiceInsertTest {
    @Test fun wordsGoAtTheCaretWithASpaceOnlyWhereTwoWordsWouldTouch() {
        val m = VoiceInsert.merge(VoiceInsert.Anchor(before = "hello", after = " world"), "there")
        assertEquals("hello there world", m.text)
        assertEquals("hello there".length, m.cursor)
    }

    @Test fun noSpaceAfterAWhitespaceOrAtTheStart() {
        assertEquals("a b", VoiceInsert.merge(VoiceInsert.Anchor("a ", ""), "b").text)
        assertEquals("안녕", VoiceInsert.merge(VoiceInsert.Anchor("", ""), " 안녕 ").text)
    }

    @Test fun blankWordsLeaveTheFieldAsItWas() {
        val m = VoiceInsert.merge(VoiceInsert.Anchor("a", "b"), "  ")
        assertEquals("ab", m.text)
        assertEquals(1, m.cursor)
    }

    @Test fun clockReadsMinutesAndSeconds() {
        assertEquals("0:00", formatClock(0))
        assertEquals("0:12", formatClock(12_400))
        assertEquals("2:00", formatClock(120_000))
    }
}
