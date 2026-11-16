package chat.kelo.app.voice

import org.junit.Assert.assertEquals
import org.junit.Test

class SpeechMergeTest {
    private fun merge(text: String, start: Int, end: Int = start, spoken: String) = SpeechMerge.merge(SpeechMerge.anchor(text, start, end), spoken)

    @Test fun intoAnEmptyField() {
        assertEquals(SpeechMerge.Merged("내일 일정 알려 줘", 10), merge("", 0, spoken = " 내일  일정 알려 줘 "))
    }

    @Test fun atTheEndAddsASpace() {
        assertEquals(SpeechMerge.Merged("안녕 반가워", 6), merge("안녕", 2, spoken = "반가워"))
        assertEquals(SpeechMerge.Merged("안녕 반가워", 6), merge("안녕 ", 3, spoken = "반가워"))
    }

    @Test fun inTheMiddleKeepsWordsApartButNotBeforePunctuation() {
        assertEquals(SpeechMerge.Merged("내일 서울 날씨", 5), merge("내일 날씨", 3, spoken = "서울"))
        assertEquals(SpeechMerge.Merged("정말 고마워.", 6), merge("정말.", 2, spoken = "고마워"))
        assertEquals(SpeechMerge.Merged("ab cd ef", 5), merge("abef", 2, spoken = "cd"))
    }

    @Test fun replacesTheSelection() {
        assertEquals(SpeechMerge.Merged("오늘 저녁 메뉴", 5), merge("오늘 점심 메뉴", 3, 5, spoken = "저녁"))
        // a selection made backwards counts the same
        assertEquals(SpeechMerge.Merged("오늘 저녁 메뉴", 5), merge("오늘 점심 메뉴", 5, 3, spoken = "저녁"))
    }

    @Test fun partialResultsRebuildFromTheSameAnchor() {
        val a = SpeechMerge.anchor("메모: ", 4, 4)
        assertEquals("메모: 우유", SpeechMerge.merge(a, "우유").text)
        assertEquals("메모: 우유 사 오기", SpeechMerge.merge(a, "우유 사 오기").text)
        // nothing heard (yet): the text is as it was, the cursor where it was
        assertEquals(SpeechMerge.Merged("메모: ", 4), SpeechMerge.merge(a, "  "))
    }

    @Test fun cursorOutsideTheTextIsClamped() {
        assertEquals(SpeechMerge.Merged("abc de", 6), merge("abc", 99, spoken = "de"))
    }

    @Test fun soundLevel() {
        assertEquals(0f, SpeechMerge.level(-5f), 0f)
        assertEquals(1f, SpeechMerge.level(20f), 0f)
        assertEquals(0.5f, SpeechMerge.level(4f), 0.001f)
    }
}
