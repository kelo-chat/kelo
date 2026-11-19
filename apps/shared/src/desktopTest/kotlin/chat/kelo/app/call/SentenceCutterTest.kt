package chat.kelo.app.call

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SentenceCutterTest {
    private fun all(vararg deltas: String): List<String> {
        val c = SentenceCutter()
        val out = ArrayList<String>()
        for (d in deltas) out += c.add(d)
        out += c.finish()
        return out
    }

    @Test fun `sentences end at Korean, English and Japanese marks`() {
        assertEquals(listOf("안녕하세요.", "반가워요!", "Hi there."), all("안녕하세요. 반가워요! Hi there."))
        assertEquals(listOf("今日は晴れです。", "明日は雨です。"), all("今日は晴れです。明日は雨です。"))
    }

    @Test fun `decimals and abbreviations are not cut`() {
        assertEquals(listOf("It takes 3.5 hours.", "U.S. is big."), all("It takes 3.5 hours. U.S. is big."))
    }

    private fun steps(vararg deltas: String): List<List<String>> {
        val c = SentenceCutter()
        val out = ArrayList<List<String>>()
        for (d in deltas) out += c.add(d)
        out += c.finish()
        return out
    }

    @Test fun `the first piece goes out at the first clause break of a short phrase`() {   // owner 2026-11-20: start speaking sooner
        assertEquals(
            listOf(emptyList(), listOf("좋은 질문이에요,"), listOf("내일 오전 회의는 10시에 시작해요."), emptyList(), listOf("끝나는 시간은 정해지지 않았어요.")),
            steps("좋은 질문이", "에요, 내일 오전 ", "회의는 10시에 시작해요. 끝나는 시간은 ", "정해지지 않았어요."))
        assertEquals(listOf("Good question,", "the meeting starts at ten."), all("Good question, the meeting starts at ten."))
    }

    @Test fun `one word is never the first piece`() {   // "Well," is one word: the opening runs on to the word cut at 32 characters
        assertEquals(listOf("Well, I think the meeting is at", "ten tomorrow morning in the big room."),
            all("Well, I think the meeting is at ten tomorrow morning in the big room."))
    }

    @Test fun `with no break soon the first piece ends at a word after a few words`() {
        val got = steps("Kelo is a self hosted proactive agent that lives on your own server and ", "keeps working when you are away.")
        assertEquals(listOf("Kelo is a self hosted proactive"), got[0])
        assertEquals(listOf("agent that lives on your own server and keeps working when you are away."), got[1] + got[2])
    }

    @Test fun `a short whole sentence goes at once and the next phrase follows quickly`() {
        assertEquals(listOf("네.", "지금 확인해 볼게요,", "잠시만요."), all("네. 지금 확인해 볼게요, 잠시만요."))
    }

    @Test fun `a long sentence later in the reply is cut at a clause break`() {
        assertEquals(
            listOf("Here is the plan.", "First we check the calendar for tomorrow morning,",
                "then we book the room on the second floor, and finally we send the invite to everyone."),
            all("Here is the plan. First we check the calendar for tomorrow morning, then we book the room on the second floor, and finally we send the invite to everyone."))
    }

    @Test fun `numbers, times and abbreviations are never cut`() {
        assertEquals(listOf("It costs 1,000 won in total,", "which is fine at 10:30."), all("It costs 1,000 won in total, which is fine at 10:30."))
    }

    @Test fun `japanese without spaces waits for its mark`() {
        assertEquals(listOf("はい、明日の会議は十時からです。"), all("はい、明日の会議は十時からです。"))
    }

    @Test fun `a word cut never lands inside a word or a link`() {
        val text = "Open the page called [the monthly budget overview](https://d.io/b) and check the totals."
        val pieces = all(text)
        assertEquals("Open the page called", pieces.first())   // not "…called [the": never inside an open link
        assertTrue(pieces.none { it.contains("[") || it.contains("](") })
    }

    @Test fun `the new rules give the same pieces streamed one character at a time`() {
        for (text in listOf("좋은 질문이에요, 내일 오전 회의는 10시에 시작해요. 끝나는 시간은 정해지지 않았어요.",
                "Kelo is a self hosted proactive agent that lives on your own server and keeps working when you are away.",
                "Here is the plan. First we check the calendar for tomorrow morning, then we book the room on the second floor, and finally we send the invite to everyone.")) {
            assertEquals(all(text), all(*text.map { it.toString() }.toTypedArray()))
        }
    }

    @Test fun `a long run is cut at a space within the max`() {
        val text = ("word ").repeat(100).trim()
        val pieces = all(text)
        assertTrue(pieces.size > 1)
        assertTrue(pieces.all { it.length <= 300 })
        assertEquals(text, pieces.joinToString(" "))
    }

    @Test fun `code fences are dropped`() {
        val pieces = all("Look: \n```kt\nval x = 1.\n```\nDone.")
        assertEquals(listOf("Look:", "Done."), pieces)
    }

    @Test fun `markdown marks are spoken plain`() {
        assertEquals(listOf("A bold link."), all("A **bold** [link](https://d.io)."))
    }

    @Test fun `finish flushes the rest`() {
        assertEquals(listOf("no end here"), all("no end here"))
    }

    @Test fun `deltas split mid word give the same pieces as one string`() {
        val text = "Hello there. The meeting is at 3.5 hours. Bye."
        val whole = all(text)
        val split = all("Hel", "lo the", "re. The meeting is at 3", ".5 hours", ". Bye", ".")
        assertEquals(whole, split)
    }
}
