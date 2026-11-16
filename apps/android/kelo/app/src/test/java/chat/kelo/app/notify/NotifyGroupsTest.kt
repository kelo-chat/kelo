package chat.kelo.app.notify

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NotifyGroupsTest {
    @Test fun `every notification is in one bundle that opens the chat`() {
        assertEquals("kelo", NotifyRules.GROUP)
        assertEquals(OpenTarget.Chat, NotifyRules.SUMMARY_TARGET)
        assertNotEquals(NotifyRules.idFor("task_done", "1"), NotifyRules.SUMMARY_ID)
    }

    @Test fun `only news is taken down when the app opens`() {
        assertTrue(NotifyRules.isNews(Channel.REPLIES))
        assertTrue(NotifyRules.isNews(Channel.SCHEDULES))
        assertTrue(NotifyRules.isNews(Channel.PAYMENTS))
        assertFalse(NotifyRules.isNews(Channel.APPROVALS))
        assertFalse(NotifyRules.isNews(Channel.URGENT))
        assertFalse(NotifyRules.isNews(Channel.WORKING))
    }

    @Test fun `a posted channel id maps back to its channel`() {
        Channel.entries.forEach { assertEquals(it, NotifyRules.channelOfId(it.id)) }
        assertNull(NotifyRules.channelOfId("replies"))   // a retired id
        assertNull(NotifyRules.channelOfId(null))
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
