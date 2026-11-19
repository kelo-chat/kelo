package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Test

/** The rows after a reply are matched to the stored messages by id (mergeStoredRows). */
class ChatMergeTest {
    private fun shown(key: String, mine: Boolean, text: String, id: Long?) = ChatItem.Msg(key, mine, text, id)
    private fun stored(id: Long, mine: Boolean, text: String) = ChatItem.Msg("m$id", mine, text, id)

    private fun keys(items: List<ChatItem>) = items.map { it.key }
    private fun texts(items: List<ChatItem>) = items.filterIsInstance<ChatItem.Msg>().map { it.text }

    @Test fun `the same list takes the stored text and keeps the shown keys`() {
        val shownRows = listOf(shown("u1", true, "hi", 1), shown("a2", false, "hello", 2))
        val storedRows = listOf(stored(1, true, "hi!"), stored(2, false, "Hello there"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "a2"), keys(out))
        assertEquals(listOf("hi!", "Hello there"), texts(out))
    }

    @Test fun `a message stored by another device in the middle does not stop the rest from being swapped`() {
        val shownRows = listOf(shown("u1", true, "a", 1), shown("a3", false, "old", 3), shown("u4", true, "b", 4))
        val storedRows = listOf(stored(1, true, "a"), stored(2, true, "from the other phone"), stored(3, false, "new"), stored(4, true, "b"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "a3", "u4"), keys(out))
        assertEquals(listOf("a", "new", "b"), texts(out))   // the other phone's message is not shown; nothing jumps
    }

    @Test fun `a streamed reply without an id takes the newest stored message that is not shown`() {
        val shownRows = listOf(shown("u1", true, "q", 1), shown("stream", false, "streamed", null))
        val storedRows = listOf(stored(1, true, "q"), stored(2, false, "the saved reply"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "stream"), keys(out))
        assertEquals(listOf("q", "the saved reply"), texts(out))
    }

    @Test fun `a stored message newer than everything shown is appended at the end with its own key`() {
        val shownRows = listOf(shown("u1", true, "q", 1))
        val storedRows = listOf(stored(1, true, "q"), stored(2, false, "sent from the other phone"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "m2"), keys(out))
    }

    @Test fun `rows that are not messages stay where they are`() {
        val card = ChatItem.Connect("c1", "Gmail")
        val shownRows = listOf(shown("u1", true, "q", 1), card, shown("stream", false, "streamed", null))
        val storedRows = listOf(stored(1, true, "q"), stored(2, false, "saved"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "c1", "stream"), keys(out))
        assertEquals(card, out[1])
    }

    @Test fun `a role mismatch stops the position match and nothing wrong is swapped`() {
        val shownRows = listOf(shown("stream", false, "streamed", null))
        val storedRows = listOf(stored(1, true, "the user's"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("stream", "m1"), keys(out))
        assertEquals(listOf("streamed", "the user's"), texts(out))
    }

    private fun tool(key: String) = ChatItem.Tool(key, "t-$key", "email_update", ToolState.OK)

    @Test fun `a reply split by a tool chip is one bubble after reload (the stored row fills the last bubble)`() {
        // live: text, chip, text  (two id-less bubbles); the server stored one row with the whole reply
        val shownRows = listOf(shown("u1", true, "q", 1), shown("a2", false, "before the tool", null), tool("t3"), shown("a4", false, "after the tool", null))
        val storedRows = listOf(stored(1, true, "q"), stored(2, false, "before the tool\n\nafter the tool"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "t3", "a4"), keys(out))
        assertEquals(listOf("q", "before the tool\n\nafter the tool"), texts(out))
    }

    @Test fun `a turn with two stored rows (a real message break) keeps two bubbles`() {
        val shownRows = listOf(shown("u1", true, "q", 1), shown("a2", false, "part one", null), tool("t3"), shown("a4", false, "part two", null))
        val storedRows = listOf(stored(1, true, "q"), stored(2, false, "part one"), stored(3, false, "part two"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "a2", "t3", "a4"), keys(out))
        assertEquals(listOf("q", "part one", "part two"), texts(out))
    }

    @Test fun `a reply without a tool is matched as before`() {
        val shownRows = listOf(shown("u1", true, "q", 1), shown("a2", false, "streamed", null))
        val storedRows = listOf(stored(1, true, "q"), stored(2, false, "the saved reply"))
        val out = mergeStoredRows(shownRows, storedRows)
        assertEquals(listOf("u1", "a2"), keys(out))
        assertEquals(listOf("q", "the saved reply"), texts(out))
    }
}
