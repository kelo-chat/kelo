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

    @Test fun `the first piece is cut at a comma after 80 characters`() {
        val text = "a".repeat(30) + ", " + "b".repeat(70) + ", then more words follow here without any end"
        val pieces = all(text)
        assertTrue(pieces[0].startsWith("a".repeat(30) + ","))
        assertTrue(pieces[0].endsWith(","))
        assertTrue(pieces[0].length > 80)
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
        assertEquals(listOf("Some bold and a link."), all("Some **bold** and a [link](https://d.io)."))
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
