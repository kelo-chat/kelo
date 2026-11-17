package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SpokenTextTest {
    @Test fun `marks, links, code and emoji are not spoken`() {
        val md = "# Title\n\nSome **bold** and `code`, see [the docs](https://d.io) or https://x.y now 😀.\n\n- first\n- second item!\n\n```kt\nval x = 1\n```\n\n> quoted"
        assertEquals("Title.\n\nSome bold and code, see the docs or now.\n\nfirst.\nsecond item!\n\nquoted.", SpokenText.from(md))
    }

    @Test fun `a table reads row by row`() =
        assertEquals("Name, Age.\nAnna, 30.", SpokenText.from("| Name | Age |\n|---|---|\n| Anna | 30 |"))

    @Test fun `message breaks become pauses`() =
        assertEquals("One.\n\nTwo.", SpokenText.from("One<message_break/>Two"))

    @Test fun `only marks give nothing`() = assertEquals("", SpokenText.from("---\n\n```\ncode\n```"))

    @Test fun `the script tells the language`() {
        assertEquals("ko", SpokenText.language("오늘 일정이 세 개 있어요", "en-US"))
        assertEquals("ja", SpokenText.language("今日の予定は三つあります", "en-US"))
        assertEquals("zh", SpokenText.language("今天有三个日程安排", "en-US"))
        assertEquals("ru", SpokenText.language("Сегодня три встречи", "en-US"))
        assertEquals("ar", SpokenText.language("لديك ثلاثة مواعيد اليوم", "en-US"))
        assertEquals("th", SpokenText.language("วันนี้มีนัดสามอย่าง", "en-US"))
        assertEquals("hi", SpokenText.language("आज तीन बैठकें हैं", "en-US"))
    }

    @Test fun `Latin text takes the app's language`() {
        assertEquals("fr-FR", SpokenText.language("Vous avez trois rendez-vous aujourd'hui.", "fr-FR"))
        assertEquals("en-US", SpokenText.language("You have three meetings today.", "en-US"))
    }

    @Test fun `a few foreign words do not change the language`() {
        assertEquals("en-US", SpokenText.language("The Korean word is 안녕 and it means hello, a common greeting in Seoul.", "en-US"))
        assertEquals("ko", SpokenText.language("메일 제목은 Weekly report 예요", "en-US"))
    }

    @Test fun `Han with kana is Japanese, with Hangul Korean`() {
        assertEquals("ja", SpokenText.language("私は東京へ行きます", "ko-KR"))
        assertEquals("ko", SpokenText.language("한국어 大韓民國 입니다", "en-US"))
    }

    @Test fun `Ukrainian and Chinese app languages keep their own tag for the same script`() {
        assertEquals("uk-UA", SpokenText.language("Сьогодні три зустрічі", "uk-UA"))
        assertEquals("zh-TW", SpokenText.language("今天有三個行程", "zh-TW"))
    }

    @Test fun `a text with no letters takes the fallback`() = assertEquals("de-DE", SpokenText.language("12:30 – 14:00", "de-DE"))

    @Test fun `a short text is one piece`() = assertEquals(listOf("Hello there."), SpokenText.chunks("  Hello there. "))

    @Test fun `a long text is cut at sentence ends within the limit`() {
        val sentence = "This is a sentence of moderate length. "
        val text = sentence.repeat(200).trim()
        val parts = SpokenText.chunks(text, max = 500)
        assertTrue(parts.size > 1)
        assertTrue(parts.all { it.length <= 500 })
        assertTrue(parts.all { it.endsWith(".") })
        assertEquals(text.replace(Regex("\\s+"), " "), parts.joinToString(" "))
    }

    @Test fun `a text without any break is still cut`() {
        val parts = SpokenText.chunks("x".repeat(1200), max = 500)
        assertTrue(parts.all { it.length <= 500 })
        assertEquals(1200, parts.sumOf { it.length })
    }

    @Test fun `nothing gives no pieces`() = assertTrue(SpokenText.chunks("  \n ").isEmpty())
}
