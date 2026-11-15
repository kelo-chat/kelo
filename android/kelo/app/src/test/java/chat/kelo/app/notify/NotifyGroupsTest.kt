package chat.kelo.app.notify

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NotifyGroupsTest {
    @Test fun `each channel bundles on its own`() {
        val groups = Channel.entries.map(NotifyRules::group)
        assertEquals(groups.size, groups.toSet().size)
        assertEquals("kelo.approvals", NotifyRules.group(Channel.APPROVALS))
    }

    @Test fun `a bundle opens its own list`() {
        assertEquals(OpenTarget.Activity, NotifyRules.summaryTarget(Channel.REPLIES))
        assertEquals(OpenTarget.Chat, NotifyRules.summaryTarget(Channel.APPROVALS))   // every waiting approval is in the chat
        assertEquals(OpenTarget.Wallet, NotifyRules.summaryTarget(Channel.PAYMENTS))
        assertEquals(OpenTarget.Schedules, NotifyRules.summaryTarget(Channel.SCHEDULES))
    }

    @Test fun `summary ids differ from each other and from notification ids`() {
        val ids = Channel.entries.map(NotifyRules::summaryId)
        assertEquals(ids.size, ids.toSet().size)
        assertNotEquals(NotifyRules.idFor("task_done", "replies"), NotifyRules.summaryId(Channel.REPLIES))
    }

    @Test fun `new targets survive the intent`() {
        listOf(OpenTarget.Activity, OpenTarget.Schedules, OpenTarget.OwnTab, OpenTarget.Tab("task:ab12"), OpenTarget.Tab("7"), OpenTarget.Wallet)
            .forEach { assertEquals(it, OpenTarget.decode(it.encode())) }
        assertNull(OpenTarget.decode("tab:"))
    }

    @Test fun `push token goes again when changed or a day old`() {
        val day = PushRules.DAY_MS
        assertTrue(PushRules.due(null, "a", 0, 1000))
        assertTrue(PushRules.due("b", "a", 1000, 1000))
        assertFalse(PushRules.due("a", "a", 1000, 1000 + day - 1))
        assertTrue(PushRules.due("a", "a", 1000, 1000 + day))
        assertTrue(PushRules.due("a", "a", 5000, 1000))   // the clock went back
    }

    @Test fun `403 means push needs a paired phone`() {
        assertTrue(PushRules.needsDevice(403))
        assertFalse(PushRules.needsDevice(404))
        assertFalse(PushRules.needsDevice(500))
    }
}
