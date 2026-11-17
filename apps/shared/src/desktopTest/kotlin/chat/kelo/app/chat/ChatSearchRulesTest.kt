package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The chat search rules: query trimming, the snippet's marked match, result mapping, paging and the scroll-to-message decision. */
class ChatSearchRulesTest {
    @Test fun queryIsTrimmedAndInnerSpacesCollapse() {
        assertEquals("hello world", ChatSearchRules.query("  hello   world \n"))
        assertEquals("", ChatSearchRules.query("   \t "))
        assertFalse(ChatSearchRules.hasQuery("   "))
        assertTrue(ChatSearchRules.hasQuery(" 날씨 "))
    }

    @Test fun snippetKeepsThePlainTextAndMarksTheMatch() {
        val parts = ChatSearchRules.snippet("비가 «온다» 고 해요")
        assertEquals(listOf("비가 " to false, "온다" to true, " 고 해요" to false), parts.map { it.text to it.match })
    }

    @Test fun snippetWithoutMarksIsOnePlainPart() {
        val parts = ChatSearchRules.snippet("no marks here")
        assertEquals(listOf("no marks here" to false), parts.map { it.text to it.match })
    }

    @Test fun snippetGuillemetsNeverShowAsText() {
        // a stray or unclosed mark: the characters go, the rest of the text stays (an open mark runs to the end)
        val stray = ChatSearchRules.snippet("a » b « c")
        assertEquals("a  b  c", stray.joinToString("") { it.text })
        val open = ChatSearchRules.snippet("start «open match")
        assertEquals(listOf("start " to false, "open match" to true), open.map { it.text to it.match })
        assertTrue(ChatSearchRules.snippet("« one » two « three »").none { it.text.contains('«') || it.text.contains('»') })
    }

    @Test fun createdAtSecondsBecomeMillisecondsForTheRow() {
        assertEquals(1_700_000_000_500L, ChatSearchRules.timeMs(1_700_000_000.5))
        assertEquals(0L, ChatSearchRules.timeMs(0.0))
    }

    @Test fun userRowsAreMine() {
        assertTrue(ChatSearchRules.isMine("user"))
        assertFalse(ChatSearchRules.isMine("assistant"))
        assertFalse(ChatSearchRules.isMine(""))
    }

    @Test fun aFullPageMayHaveMoreAndAShortOneDoesNot() {
        assertTrue(ChatSearchRules.hasMore(ChatSearchRules.PAGE))
        assertFalse(ChatSearchRules.hasMore(ChatSearchRules.PAGE - 1))
        assertFalse(ChatSearchRules.hasMore(0))
    }

    @Test fun thePagingCursorIsTheSmallestIdOnThePage() {
        val page = listOf(hit(90), hit(70), hit(81))
        assertEquals(70L, ChatSearchRules.nextBefore(page))
        assertNull(ChatSearchRules.nextBefore(emptyList()))
    }

    @Test fun pagesAppendWithoutRepeatingAHit() {
        val shown = listOf(hit(90), hit(80))
        val merged = ChatSearchRules.merge(shown, listOf(hit(80), hit(70)))
        assertEquals(listOf(90L, 80L, 70L), merged.map { it.id })
    }

    @Test fun aLoadedMessageNeedsNoWindowAndAMissingOneDoes() {
        val loaded = listOf(10L, 11L, 12L)
        assertEquals(ChatSearchRules.Anchor.Loaded, ChatSearchRules.anchor(loaded, 11L))
        assertEquals(ChatSearchRules.Anchor.Missing, ChatSearchRules.anchor(loaded, 5L))
        assertEquals(ChatSearchRules.Anchor.Missing, ChatSearchRules.anchor(emptyList(), 5L))
    }

    @Test fun searchPathEncodesTheQueryAndAddsTheCursorOnlyWhenGiven() {
        assertEquals("/api/messages/search?q=hello%20world&limit=20", ChatSearchRules.searchPath("hello world", before = null))
        assertEquals("/api/messages/search?q=%EB%82%A0%EC%94%A8&limit=20&before=41",
            ChatSearchRules.searchPath("날씨", before = 41))
        assertEquals("/api/messages/around?id=7&n=10", ChatSearchRules.aroundPath(7))
    }

    @Test fun newerKeepsOnlyTheMessagesAfterTheWindowsNewestOneInOrder() {
        val page = listOf(stored(33), stored(31), stored(35), stored(32), stored(34))
        val w = ChatSearchRules.newer(page, after = 32)
        assertEquals(listOf(33L, 34L, 35L), w.rows.map { it.id })
        assertFalse(w.more)
    }

    @Test fun newerSaysMoreOnlyOnAFullPage() {
        val full = (33L..(32L + ChatSearchRules.NEWER_PAGE)).map { stored(it) }
        assertTrue(ChatSearchRules.newer(full, after = 32).more)
        assertFalse(ChatSearchRules.newer(full.dropLast(1), after = 32).more)
        assertFalse(ChatSearchRules.newer(emptyList(), after = 32).more)
    }

    private fun stored(id: Long) = StoredMessage(id = id, role = "user", content = "m$id")

    private fun hit(id: Long) = chat.kelo.app.chat.SearchHit(id = id, role = "user", snippet = "x", createdAt = 1.0)
}
