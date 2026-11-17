package chat.kelo.app.notify

import chat.kelo.app.agent.ServerNotification
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class ItemIdsTest {
    @Test fun aWaitingNotificationIsIdentifiedByItsApprovalId() {
        assertEquals("ab12-x", ItemIds.of(ServerNotification(id = 5, type = "approval_waiting", approvalId = " ab12-x ")))
        assertNull(ItemIds.of(ServerNotification(id = 6, type = "task_done", taskId = 3)))
        assertNull(ItemIds.of(ServerNotification(id = 7, type = "approval_waiting", approvalId = " ")))
    }

    @Test fun theAskForSettledItemsListsEachShownIdOnce() {
        assertNull(ItemIds.settledPath(emptyList()))
        assertEquals("/api/notifications/settled?ids=a,b", ItemIds.settledPath(listOf("a", "b", "a", " ")))
    }

    @Test fun aSettledPushCarriesItemIdsAndIsParsedSafely() {
        val m = PushData.parse(mapOf("type" to "settled", "id" to "ap1", "ids" to "ap1, ap2,ap1,bad id,<x>"))!!
        assertEquals(PushData.SETTLED, m.type)
        assertEquals(listOf("ap1", "ap2"), m.ids)
        assertNull(m.id)   // item ids are words, not numbers
        assertEquals(listOf("ap1"), PushData.parse(mapOf("type" to "settled", "id" to "ap1"))!!.ids)
        assertEquals(10, PushData.parse(mapOf("type" to "settled", "ids" to (1..30).joinToString(",") { "i$it" }))!!.ids.size)
        assertEquals(emptyList(), PushData.parse(mapOf("type" to "task_done", "id" to "3", "ids" to "a"))!!.ids)   // only a settled push has ids
    }

    @Test fun aPushKindAnOlderAppDoesNotKnowIsStillAReadableMessage() {
        // what an app before this one gets: an unknown kind that looks like an id word passes the parser, and its fallback
        // (NotifyRules.titleOf) never shows an empty title
        val n = ServerNotification(id = 1, type = "settled")
        assertEquals("Open Kelo", NotifyRules.titleOf(n, own = "x", hint = "Open Kelo"))
    }
}
