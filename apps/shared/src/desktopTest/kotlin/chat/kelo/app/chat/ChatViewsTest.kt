package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The views the server shows in the chat (chat_views.py): the cards follow them, and one card per tab is in the whole list. */
class ChatViewsTest {
    private var n = 0
    private val key = { "k${++n}" }
    private fun msg(id: Long, mine: Boolean) = ChatItem.Msg("m$id", mine, "t$id", id)
    private fun card(tab: String, at: Double = 100.0, status: String = "old") =
        ChatItem.Browser("card-$tab", "c", "Coupang", "coupang.com", "https://coupang.com", status, null, tab = tab, at = at)
    private fun view(key: String, anchor: Long, status: String = "보는 중", kind: String = "browser") =
        ChatViewItem(key = key, kind = kind, anchorId = anchor, title = "Coupang", url = "https://coupang.com", host = "coupang.com", status = status)

    @Test fun aCardWhoseViewIsListedTakesTheViewsWords() {
        val out = applyViews(listOf(msg(829, false), card("owner")), listOf(view("owner", 829, status = "새 상태")), checkedAt = 200.0)
        val c = out.filterIsInstance<ChatItem.Browser>().single()
        assertEquals("새 상태", c.status)
        assertEquals(listOf(msg(829, false).key, c.key), out.map { it.key })
    }

    @Test fun aCardWhoseViewIsGoneGoesWhenItIsOlderThanTheAnswerButAStillNewOneStays() {
        val old = applyViews(listOf(msg(1, true), card("owner", at = 100.0)), emptyList(), checkedAt = 200.0)
        assertTrue(old.none { it is ChatItem.Browser })
        val fresh = applyViews(listOf(msg(1, true), card("owner", at = 199.0)), emptyList(), checkedAt = 200.0)
        assertTrue(fresh.any { it is ChatItem.Browser })
    }

    @Test fun aViewWithoutACardGetsOneRightAfterItsAnchorMessage() {
        val items = listOf(msg(828, true), msg(829, false), msg(830, false))
        val out = applyViews(items, listOf(view("task:w1", 829)), checkedAt = 200.0)
        val c = out[2] as ChatItem.Browser
        assertEquals("task:w1", c.tab); assertEquals("Coupang", c.title)
        assertEquals(listOf(828L, 829L, null, 830L), out.map { (it as? ChatItem.Msg)?.id })
    }

    @Test fun aViewWhoseAnchorIsOlderThanThePageGoesAtTheTopAndNewerAtTheEnd() {
        val items = listOf(msg(828, true), msg(829, false))
        assertTrue(applyViews(items, listOf(view("owner", 5)), 200.0)[0] is ChatItem.Browser)
        assertTrue(applyViews(items, listOf(view("owner", 900)), 200.0).last() is ChatItem.Browser)
    }

    @Test fun withoutInsertNoCardIsAddedOnAPageThatIsNotTheNewest() {
        val out = applyViews(listOf(msg(1, true)), listOf(view("owner", 1)), 200.0, insert = false)
        assertEquals(1, out.size)
    }

    @Test fun aClosedViewTakesItsCardAndAnOpenOneDoesNot() {
        val items = listOf(msg(1, true), card("owner"), card("android").copy(device = true, tab = "android"))
        val closed = ChatReducer.apply(items, TurnEvent.View("owner", "closed", reason = "done"), key)
        assertEquals(listOf(true), closed.filterIsInstance<ChatItem.Browser>().map { it.device })
        val opened = ChatReducer.apply(items, TurnEvent.View("owner", "open"), key)
        assertEquals(2, opened.filterIsInstance<ChatItem.Browser>().size)
    }

    @Test fun aBrowserCardOfAnEarlierReplyMovesToThisReplyWhenTheTabIsUsedAgain() {
        val items = listOf(msg(1, true), ChatItem.Msg("a2", false, "answer", 2), card("owner"), msg(3, true))
        val out = ChatReducer.apply(items, TurnEvent.Browser("b9", "Coupang", "https://coupang.com", "coupang.com", "다시 보는 중", null, tab = "owner"), key)
        val cards = out.filterIsInstance<ChatItem.Browser>()
        assertEquals(1, cards.size)
        assertEquals("다시 보는 중", cards[0].status)
        assertEquals(out.last(), cards[0])
    }
}
