package chat.kelo.app.work

import chat.kelo.app.agent.Worker
import chat.kelo.app.core.KeloApi
import java.time.Instant
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RetryRulesTest {
    private val utc = ZoneId.of("UTC")
    private fun epoch(iso: String): Double = Instant.parse(iso).epochSecond.toDouble()
    private val now = epoch("2026-11-16T10:00:00Z")

    @Test fun `a retry the same day is today, the next day is tomorrow, later is a later day`() {
        assertEquals(RetryRules.Day.TODAY, RetryRules.day(epoch("2026-11-16T15:40:00Z"), now, utc))
        assertEquals(RetryRules.Day.TOMORROW, RetryRules.day(epoch("2026-11-17T00:10:00Z"), now, utc))
        assertEquals(RetryRules.Day.LATER, RetryRules.day(epoch("2026-11-18T09:10:00Z"), now, utc))
    }

    @Test fun `the day is the local day, not the UTC one`() {
        val seoul = ZoneId.of("Asia/Seoul")   // 10:00 UTC is 19:00 in Seoul; 16:30 UTC on the 16th is 01:30 on the 17th there
        assertEquals(RetryRules.Day.TODAY, RetryRules.day(epoch("2026-11-16T15:40:00Z"), now, utc))
        assertEquals(RetryRules.Day.TOMORROW, RetryRules.day(epoch("2026-11-16T16:30:00Z"), now, seoul))
        assertEquals(RetryRules.Day.TODAY, RetryRules.day(epoch("2026-11-16T12:00:00Z"), now, seoul))
    }

    @Test fun `a job waiting to try again is parsed with its time, is active, and is not asking the user`() {
        val w = KeloApi.json.decodeFromString(Worker.serializer(), """{"id":"w1","kind":"browser","title":"t","status":"waiting","wake_at":1794855600.0,"steps":2}""")
        assertEquals(Worker.WAITING, w.status)
        assertEquals(1794855600.0, w.wakeAt)
        assertTrue(w.active)
        assertFalse(w.needsYou)
        assertFalse(w.canResume)
    }

    @Test fun `a worker without wake_at parses with none`() {
        val w = KeloApi.json.decodeFromString(Worker.serializer(), """{"id":"w2","status":"running"}""")
        assertEquals(null, w.wakeAt)
    }
}
