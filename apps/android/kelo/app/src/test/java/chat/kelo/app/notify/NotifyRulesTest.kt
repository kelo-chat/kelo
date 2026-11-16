package chat.kelo.app.notify

import android.content.ContextWrapper
import chat.kelo.app.agent.ServerNotification
import chat.kelo.app.chat.PaymentInfo
import chat.kelo.app.chat.TurnEvent
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NotifyRulesTest {
    private fun note(id: Long, type: String = NoteTypes.TASK_DONE, seen: Boolean = false, task: Long? = null, schedule: Long? = null, approval: String? = null) =
        ServerNotification(id = id, type = type, seen = seen, taskId = task, scheduleId = schedule, approvalId = approval)

    @Test fun nothingWhileTheAppIsOnScreenSeenOrPostedBefore() {
        assertTrue(NotifyRules.shouldPost(foreground = false, seen = false, alreadyPosted = false))
        assertFalse(NotifyRules.shouldPost(foreground = true, seen = false, alreadyPosted = false))
        assertFalse(NotifyRules.shouldPost(foreground = false, seen = true, alreadyPosted = false))
        assertFalse(NotifyRules.shouldPost(foreground = false, seen = false, alreadyPosted = true))
    }

    @Test fun channels() {
        assertEquals(Channel.REPLIES, NotifyRules.channel(NoteTypes.TASK_DONE, null))
        assertEquals(Channel.SCHEDULES, NotifyRules.channel(NoteTypes.TASK_FAILED, 3))
        assertEquals(Channel.APPROVALS, NotifyRules.channel(NoteTypes.APPROVAL_WAITING, null))
        assertEquals(Channel.PAYMENTS, NotifyRules.channel(NoteTypes.PAYMENT_AUTO, null))
        assertEquals(Channel.SCHEDULES, NotifyRules.channel(NoteTypes.SCHEDULE_DISABLED, 3))
    }

    @Test fun whereATapGoes() {
        assertEquals(OpenTarget.Task(9), NotifyRules.target(note(1, task = 9)))
        assertEquals(OpenTarget.Chat, NotifyRules.target(note(1)))
        // approvals open the chat, where they wait as live cards (a payment is never answered from a notification)
        assertEquals(OpenTarget.Chat, NotifyRules.target(note(1, NoteTypes.APPROVAL_WAITING, approval = "a", task = 9)))
        assertEquals(OpenTarget.Wallet, NotifyRules.target(note(1, NoteTypes.PAYMENT_AUTO)))
        assertEquals(OpenTarget.ModelSettings(), NotifyRules.target(note(1, NoteTypes.CLI_UPDATE)))   // an older server names no provider
        assertEquals(OpenTarget.ModelSettings("antigravity-oauth"),
            NotifyRules.target(ServerNotification(id = 2, type = NoteTypes.CLI_UPDATE, provider = "antigravity-oauth")))
        assertEquals(OpenTarget.Schedule(4), NotifyRules.target(note(1, NoteTypes.SCHEDULE_DISABLED, schedule = 4)))
        assertEquals(OpenTarget.ServerUpdate, NotifyRules.target(note(1, NoteTypes.SERVER_UPDATE)))   // My → 서버 업데이트
    }

    @Test fun openTargetsSurviveTheIntent() {
        listOf(OpenTarget.Chat, OpenTarget.Task(12), OpenTarget.Approvals, OpenTarget.Schedule(3), OpenTarget.Wallet, OpenTarget.ModelSettings(),
            OpenTarget.ModelSettings("claude-subscription-directsdk-experimental"), OpenTarget.ServerUpdate).forEach {
            assertEquals(it, OpenTarget.decode(it.encode()))
        }
        assertNull(OpenTarget.decode("task:x"))
        assertNull(OpenTarget.decode(null))
        assertNull(OpenTarget.decode("elsewhere"))
    }

    @Test fun `a type the app does not know is posted with the server's own title`() {
        val n = ServerNotification(id = 3, type = "security", title = "New sign-in on your server")
        assertEquals("New sign-in on your server", NotifyRules.titleOf(n, own = "Schedule off", hint = "Open Kelo"))
        assertEquals("Open Kelo", NotifyRules.titleOf(n.copy(title = " "), own = "Schedule off", hint = "Open Kelo"))
        assertEquals("Task done", NotifyRules.titleOf(note(1), own = "Task done", hint = "Open Kelo"))   // a known type: the app's own words
        assertEquals(Channel.REPLIES, NotifyRules.channel("security", null))
        assertEquals(OpenTarget.Chat, NotifyRules.target(n))
    }

    @Test fun freshNotificationsOldestFirst() {
        val page = listOf(note(9), note(8, seen = true), note(7, type = "something_new"), note(6), note(4))
        assertEquals(listOf(6L, 7L, 9L), NotifyRules.fresh(page, lastPosted = 5).map { it.id })   // a type the app does not know is kept
        assertEquals(emptyList<Long>(), NotifyRules.fresh(page, lastPosted = 9).map { it.id })
    }

    @Test fun cursorNeverGoesBack() {
        assertEquals(12, NotifyRules.nextCursor(5, 12, listOf(note(9))))
        assertEquals(20, NotifyRules.nextCursor(20, 12, emptyList()))
        assertEquals(15, NotifyRules.nextCursor(5, 0, listOf(note(15))))
    }

    @Test fun firstLineOfAnAnswer() {
        assertEquals("오늘 날씨는 맑아요.", NotifyRules.firstLine("\n\n## 오늘 날씨는 **맑아요**.\n자세히는…"))
        assertEquals("링크 보기", NotifyRules.firstLine("- [링크 보기](https://x.example)"))
        assertEquals("표 다음 줄", NotifyRules.firstLine("```\ncode\n```\n|---|\n표 다음 줄"))
        assertEquals("", NotifyRules.firstLine("   \n "))
        val long = "가".repeat(200)
        assertEquals(120, NotifyRules.firstLine(long).length)
        assertTrue(NotifyRules.firstLine(long).endsWith("…"))
    }

    @Test fun idsAreStableAndPositive() {
        assertEquals(NotifyRules.idFor("task_done", "5"), NotifyRules.idFor("task_done", "5"))
        assertTrue(NotifyRules.idFor("x", "y") >= 0)
        assertTrue(NotifyRules.idFor("task_done", "5") != NotifyRules.idFor("task_failed", "5"))
    }

    @Test fun theNewKindsOpenWhereTheyBelong() {
        assertEquals(OpenTarget.Schedule(4), NotifyRules.target(note(1, NoteTypes.SCHEDULE_PAUSED, schedule = 4)))
        assertEquals(OpenTarget.Schedules, NotifyRules.target(note(1, NoteTypes.SCHEDULE_PAUSED)))
        assertEquals(OpenTarget.Watchers, NotifyRules.target(note(1, NoteTypes.WATCH_FIRED)))
        assertEquals(OpenTarget.Watchers, NotifyRules.target(note(1, NoteTypes.WATCH_DISABLED)))
        assertEquals(OpenTarget.Chat, NotifyRules.target(note(1, NoteTypes.PROACTIVE, task = 9)))
        assertEquals(Channel.SCHEDULES, NotifyRules.channel(NoteTypes.WATCH_FIRED, null))
        assertEquals(Channel.REPLIES, NotifyRules.channel(NoteTypes.PROACTIVE, null))
        assertEquals(OpenTarget.Watchers, OpenTarget.decode(OpenTarget.Watchers.encode()))
        listOf(NoteTypes.SCHEDULE_PAUSED, NoteTypes.WATCH_FIRED, NoteTypes.WATCH_DISABLED, NoteTypes.PROACTIVE).forEach {
            assertEquals(it, PushData.parse(mapOf("type" to it, "id" to "3"))?.type)
        }
    }
}

class FollowupNotifyTest {
    @Test fun `a follow-up opens the chat at its message`() {
        val n = ServerNotification(id = 4, type = NoteTypes.FOLLOWUP, messageId = 77)
        assertEquals(OpenTarget.ChatAt(77), NotifyRules.target(n))
        assertEquals(OpenTarget.Chat, NotifyRules.target(n.copy(messageId = null)))
        assertEquals(Channel.APPROVALS, NotifyRules.channel(NoteTypes.FOLLOWUP, null))   // it waits for the owner: may badge the icon
        assertEquals("chat:77", OpenTarget.ChatAt(77).encode())
        assertEquals(OpenTarget.ChatAt(77), OpenTarget.decode("chat:77"))
        assertEquals(OpenTarget.Chat, OpenTarget.decode("chat:x"))
        val push = PushData.parse(mapOf("type" to "followup", "id" to "4", "message_id" to "77"))!!
        assertEquals(77L, push.messageId)
        assertTrue(NotifyRules.fresh(listOf(n), 0).isNotEmpty())
    }
}

class PushDataTest {
    @Test fun readsKeloDataMessages() {
        assertEquals(PushMessage("task_done", 42, null, null), PushData.parse(mapOf("type" to "task_done", "id" to "42")))
        assertEquals(PushMessage("approval_waiting", 7, "t", "b"), PushData.parse(mapOf("type" to " approval_waiting ", "id" to "7", "title" to "t", "body" to "b")))
        assertEquals(PushMessage("check", null, null, null), PushData.parse(mapOf("type" to "check")))
        assertEquals(PushMessage("payment_auto", null, null, null), PushData.parse(mapOf("type" to "payment_auto", "id" to "x", "title" to " ")))
    }

    @Test fun ignoresAnythingElse() {
        assertNull(PushData.parse(emptyMap()))
        assertNull(PushData.parse(mapOf("type" to "Not An Id!")))
        assertEquals("security", PushData.parse(mapOf("type" to "security", "id" to "5"))?.type)   // an unknown kind that looks like an id word
        assertNull(PushData.parse(mapOf("id" to "1")))
    }
}

class ReplyRulesTest {
    @Test fun replyDoneOnlyWhileAway() {
        assertNull(ReplyRules.onFinish(ok = true, sawError = false, visible = true, answer = "안녕"))
        assertEquals(ReplyRules.Post.Done("안녕하세요"), ReplyRules.onFinish(ok = true, sawError = false, visible = false, answer = "안녕하세요\n둘째 줄"))
        assertEquals(ReplyRules.Post.Failed, ReplyRules.onFinish(ok = false, sawError = false, visible = false, answer = ""))
        // the reply ended but said it failed (an error event)
        assertEquals(ReplyRules.Post.Failed, ReplyRules.onFinish(ok = true, sawError = true, visible = false, answer = "부분"))
    }

    @Test fun approvalsWhileAwayPaymentsMarked() {
        val plain = TurnEvent.ApprovalRequest("a", "browser_click", JsonObject(emptyMap()), null, label = "사이트에서 누르기")
        val pay = plain.copy(name = "confirm_purchase", label = "", payment = PaymentInfo("s", "책", 1.0, "KRW", "1원"))
        assertNull(ReplyRules.onApproval(plain, visible = true))
        assertEquals(ReplyRules.Post.Approval("사이트에서 누르기", false), ReplyRules.onApproval(plain, visible = false))
        assertEquals(ReplyRules.Post.Approval("confirm_purchase", true), ReplyRules.onApproval(pay, visible = false))
    }

    @Test fun addressQuestionWhileAway() {
        val ask = TurnEvent.ShippingForm(chat.kelo.app.wallet.ShippingAsk("call_5", "sf_1", site = "shop.example.com", what = "블루 머그 2개"))
        assertNull(ReplyRules.onShipping(ask, visible = true))
        assertEquals(ReplyRules.Post.Shipping("블루 머그 2개", "sf_1"), ReplyRules.onShipping(ask, visible = false))
        val noWhat = TurnEvent.ShippingForm(chat.kelo.app.wallet.ShippingAsk("c", "sf_2", site = "shop.example.com"))
        assertEquals(ReplyRules.Post.Shipping("shop.example.com", "sf_2"), ReplyRules.onShipping(noWhat, visible = false))
    }

    @Test fun serviceOnlyWhenFollowingAndAway() {
        assertTrue(ReplyRules.startService(following = true, visible = false))
        assertFalse(ReplyRules.startService(following = true, visible = true))
        assertFalse(ReplyRules.startService(following = false, visible = false))
    }

    @Test fun askOnceAndOnlyWhenOff() {
        assertTrue(AskRules.shouldAsk(askedBefore = false, enabled = false))
        assertFalse(AskRules.shouldAsk(askedBefore = true, enabled = false))
        assertFalse(AskRules.shouldAsk(askedBefore = false, enabled = true))
    }
}

class LauncherBadgeTest {
    @Test fun `only what waits for the owner may badge the launcher icon`() {
        assertTrue(Channel.APPROVALS.badges && Channel.URGENT.badges)
        listOf(Channel.REPLIES, Channel.SCHEDULES, Channel.PAYMENTS, Channel.WORKING).forEach { assertTrue(!it.badges) }
        assertEquals(Channel.REPLIES, NotifyRules.channel(NoteTypes.TASK_DONE, null))        // finished work: no icon dot
        assertEquals(Channel.REPLIES, NotifyRules.channel(NoteTypes.BRIEFING, null))
        assertEquals(Channel.PAYMENTS, NotifyRules.channel(NoteTypes.PAYMENT_AUTO, null))
        assertEquals(Channel.APPROVALS, NotifyRules.channel(NoteTypes.APPROVAL_WAITING, null))
    }

    @Test fun `the quiet channels have new ids and the old ones go`() {
        // a channel's badge setting cannot change after it is made: the old ids (made with a badge) are deleted
        assertEquals(listOf("replies", "payments", "schedules"), Channel.RETIRED)
        assertTrue(Channel.entries.none { it.id in Channel.RETIRED })
    }
}

class ReplyWatchStopTest {
    private fun watch(): ReplyWatch {
        val ctx = ContextWrapper(null)
        return ReplyWatch(ctx, Notifier(ctx), AppVisibility())
    }

    @Test fun `a notification update is at most one a second`() {
        assertEquals(1000L, ReplyRules.UPDATE_EVERY_MS)
    }

    @Test fun `stopNow calls the way to stop, and does nothing after the reply was cancelled`() {
        // started() and finished() talk to the system's notification manager, which a plain unit test has not: not called here
        val w = watch()
        var stopped = 0
        w.stopWith { stopped++ }
        w.stopNow()
        assertEquals(1, stopped)
        w.cancelled()
        w.stopNow()
        assertEquals(1, stopped)
    }
}
