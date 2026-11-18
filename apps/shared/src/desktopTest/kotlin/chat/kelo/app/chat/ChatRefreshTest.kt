package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The news check while the newest page is on screen keeps the rows that are not stored messages (refreshNewest). */
class ChatRefreshTest {
    private fun shown(key: String, mine: Boolean, text: String, id: Long?) = ChatItem.Msg(key, mine, text, id)
    private fun stored(id: Long, mine: Boolean, text: String) = ChatItem.Msg("m$id", mine, text, id)
    private fun card(key: String) = ChatItem.Browser(key, key, "Coupang", "coupang.com", "https://coupang.com", "", null, tab = "owner")

    @Test fun `a browser card after the reply stays when a message is stored after it`() {
        val shownRows = listOf(shown("u828", true, "q", 828), shown("a829", false, "reply", 829), card("card"))
        val storedRows = listOf(stored(828, true, "q"), stored(829, false, "reply"), stored(830, false, "promise line"))
        val out = refreshNewest(shownRows, storedRows)!!
        assertEquals(listOf("u828", "a829", "card", "m830"), out.map { it.key })
    }

    @Test fun `no shown message on the page means the page replaces the list`() {
        val shownRows = listOf(shown("old", true, "x", 1), card("card"))
        assertNull(refreshNewest(shownRows, listOf(stored(900, true, "y"))))
    }

    @Test fun `a shown message the page no longer has goes when it is inside the page's id range and an older one stays`() {
        val shownRows = listOf(shown("old", true, "x", 5), shown("gone", false, "deleted", 7), shown("kept", true, "q", 8))
        val storedRows = listOf(stored(6, true, "a"), stored(8, true, "q"))
        val out = refreshNewest(shownRows, storedRows)!!
        assertEquals(listOf("old", "kept"), out.map { it.key })
    }
}
