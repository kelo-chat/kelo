package chat.kelo.app.offers

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OfferStepsTest {
    private val steps = listOf("a", "b", "c", "d", "e")

    @Test fun collapsedShowsThreeAndOffersToggle() {
        assertEquals(listOf("a", "b", "c"), OfferSteps.visible(steps, expanded = false))
        assertTrue(OfferSteps.collapsible(steps))
    }

    @Test fun expandedShowsAll() {
        assertEquals(steps, OfferSteps.visible(steps, expanded = true))
    }

    @Test fun threeOrFewerNeverCollapse() {
        val three = steps.take(3)
        assertEquals(three, OfferSteps.visible(three, expanded = false))
        assertFalse(OfferSteps.collapsible(three))
        assertFalse(OfferSteps.collapsible(emptyList()))
    }

    @Test fun cardPayloadCarriesRequestAndSteps() {
        val json = Json { ignoreUnknownKeys = true }
        val o = json.decodeFromString<Offer>("""{"id":"1","sentence":"s","request":"do it","steps":["x","y"],"created_at":1.0}""")
        assertEquals("do it", o.request)
        assertEquals(listOf("x", "y"), o.steps)
        val old = json.decodeFromString<Offer>("""{"id":"1","sentence":"s"}""")
        assertEquals("", old.request)
        assertTrue(old.steps.isEmpty())
    }
}

class ApprovalLineBlockTest {
    @Test fun shortPairStaysOnOneLine() {
        assertFalse(chat.kelo.app.chat.ApprovalLine("Site", "github.com").block)
    }

    @Test fun wholeScheduleTaskIsABlock() {
        assertTrue(chat.kelo.app.chat.ApprovalLine("Task", "x".repeat(200)).block)
        assertTrue(chat.kelo.app.chat.ApprovalLine("Task", "step one\nstep two").block)
        assertTrue(chat.kelo.app.chat.ApprovalLine("Text", "hi", full = true).block)
        assertEquals(8, chat.kelo.app.chat.ApprovalLine.BLOCK_LINES)
    }
}
