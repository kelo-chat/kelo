package chat.kelo.app.chat

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/** The chat's call cards: one row per in-app call, opened to its lines (owner 2026-11-20). */
class CallCardsTest {
    private val t = 1_700_000_000_000L

    private fun msg(key: String, mine: Boolean, text: String, id: Long? = null, at: Long = 0L, call: String? = null, fromCall: Boolean = false) =
        ChatItem.Msg(key, mine = mine, text = text, id = id, at = at, callId = call, fromCall = fromCall)

    private fun tool(key: String) = ChatItem.Tool(key, id = "t-$key", name = "email_read", state = ToolState.OK)

    @Test fun `the lines of one call fold into one card and stay out of the chat as bubbles`() {   // the owner's report
        val a = msg("a", mine = true, text = "typed", id = 1)
        val m2 = msg("m2", true, "여보세요.", id = 2, at = t, call = "c1", fromCall = true)
        val tl = tool("tool")
        val m3 = msg("m3", false, "네, 남호님. 잘 들려요.", id = 3, at = t + 60_000, call = "c1")
        val m4 = msg("m4", true, "나 목소리 너무 끊겨.", id = 4, at = t + 190_000, call = "c1")
        val b = msg("b", true, "typed 2", id = 5)
        val m6 = msg("m6", false, "안녕하세요", id = 6, call = "c2")
        val rows = CallCards.fold(listOf(a, m2, tl, m3, m4, b, m6))
        assertEquals(5, rows.size)
        assertEquals(a, rows[0])
        assertEquals(ChatItem.CallCard("call:c1:m2", "c1", listOf(m2, m3, m4)), rows[1])
        assertEquals(tl, rows[2])
        assertEquals(b, rows[3])
        assertEquals(ChatItem.CallCard("call:c2:m6", "c2", listOf(m6)), rows[4])
        assertTrue(rows.none { it is ChatItem.Msg && it.callId != null })
    }

    @Test fun `a call split by another message makes two cards`() {
        val rows = CallCards.fold(listOf(msg("c1a", false, "a", call = "c1"), msg("typed", true, "x"), msg("c1c", false, "c", call = "c1")))
        assertEquals(listOf("call:c1:c1a", "typed", "call:c1:c1c"), rows.map { it.key })
    }

    @Test fun `rows without call ids pass through as they are`() {
        val items = listOf(msg("a", true, "x"), tool("t"))
        assertSame(items, CallCards.fold(items))
    }

    @Test fun `new rows of a call's reply take its call id`() {
        val before = listOf(msg("k1", true, "hi", call = "c1", fromCall = true))
        val after = before + msg("k2", false, "네")
        val tagged = CallCards.tagNew(before, after, "c1")
        val k2 = tagged[1] as ChatItem.Msg
        assertEquals("c1", k2.callId)
        assertTrue(k2.fromCall)
        val old = msg("k0", false, "old")
        assertNull((CallCards.tagNew(listOf(old), listOf(old), "c1")[0] as ChatItem.Msg).callId)
    }

    @Test fun `minutes and preview`() {
        fun card(vararg at: Long) = ChatItem.CallCard("k", "c", at.map { msg("m$it", false, "x", at = it) })
        assertEquals(1, CallCards.minutes(card(t, t + 40_000)))
        assertEquals(3, CallCards.minutes(card(t, t + 190_000)))
        assertEquals(4, CallCards.minutes(card(t, t + 211_000)))
        assertEquals(1, CallCards.minutes(card(0L, t + 600_000)))
        val folded = ChatItem.CallCard("k", "c", listOf(
            msg("a", false, "네,\n남호님   잘 들려요.", call = "c"), msg("b", true, "owner words", call = "c")))
        assertEquals("owner words", CallCards.preview(folded))
        val kelo = ChatItem.CallCard("k", "c", listOf(msg("a", false, "네,\n남호님   잘 들려요.", call = "c")))
        assertEquals("네, 남호님 잘 들려요.", CallCards.preview(kelo))
        val owners = ChatItem.CallCard("k", "c", listOf(msg("a", false, "네"), msg("b", true, "여보세요  ", call = "c")))
        assertEquals("여보세요", CallCards.preview(owners))
    }

    @Test fun `cardWith finds the card holding a message`() {
        val card = ChatItem.CallCard("call:c1:a", "c1", listOf(msg("a", false, "x", id = 7, call = "c1")))
        val rows = listOf(msg("z", true, "y", id = 1), card)
        assertEquals(card, CallCards.cardWith(rows, 7))
        assertNull(CallCards.cardWith(rows, 1))
    }

    @Test fun `a stored message reads its call`() {
        val json = Json { ignoreUnknownKeys = true }
        val with = json.decodeFromString<StoredMessage>("""{"id":5,"role":"user","content":"x","via":"call","call":"c1"}""")
        assertEquals("c1", with.callId)
        assertEquals("c1", toChatItem(with).callId)
        val without = json.decodeFromString<StoredMessage>("""{"id":5,"role":"user","content":"x","via":"call"}""")
        assertNull(without.callId)
    }
}
