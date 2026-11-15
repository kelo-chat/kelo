package chat.kelo.desktop

import chat.kelo.app.agent.LiveRules
import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.TurnEvent
import chat.kelo.desktop.chat.CheckPlace
import chat.kelo.desktop.chat.Handover
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A human check handed to the user, as the desktop app takes it (the phone's HandoverTest, where the desktop has the same
 * parts): the pending item, the stream's event, how a chat card ends, the live view taking the tab over, and approval titles.
 */
class HandoverTest {
    private val list = Json.parseToJsonElement(
        """{"items":[{"type":"approval_request","kind":"human_check","approval_id":"h1","name":"browser_check","label":"Human check",
           "ask":"shop.example.com wants to confirm a person is there","site":"shop.example.com","tab":"task:ab12","asked_at":10,
           "unattended":true,"source":"worker","worker_id":"w1","title":"Find tickets","check":{"vendor":"cloudflare"}},
           {"type":"approval_request","approval_id":"a1","name":"browser_login","label":"Signing in","ask":"Sign in?","asked_at":5}],
           "resolved":[{"approval_id":"h0","kind":"human_check","outcome":"solved"},{"approval_id":"","outcome":"x"}]}""",
    )

    @Test fun aHumanCheckIsReadFromThePendingList() {
        val items = PendingApproval.parseList(list)
        val h = items.first { it.id == "h1" }
        assertTrue(h.isHumanCheck); assertEquals("task:ab12", h.tab); assertEquals("shop.example.com", h.site)
        val a = items.first { it.id == "a1" }
        assertFalse(a.isHumanCheck); assertEquals("owner", a.tab); assertEquals("Sign in?", a.event.ask)
        assertEquals(mapOf("h0" to "solved"), PendingApproval.parseResolved(list))
        assertEquals(emptyMap<String, String>(), PendingApproval.parseResolved(Json.parseToJsonElement("""{"items":[]}""")))   // an older server
    }

    @Test fun theStreamsHandOverEventIsKnown() {
        val ev = TurnEvent.parse(Json.parseToJsonElement("""{"type":"human_check","approval_id":"h1","state":"solved","site":"s.example","tab":"owner"}"""))
        assertEquals(TurnEvent.HumanCheck("h1", "solved", "s.example", "owner"), ev)
    }

    @Test fun aChatCardKnowsItsSiteAndTabAndFoldsToHowItEnded() {
        val ask = TurnEvent.parse(Json.parseToJsonElement(
            """{"type":"approval_request","kind":"human_check","approval_id":"h1","name":"browser_check","ask":"x","tab":"task:ab12"}""",
        ))
        val waiting = TurnEvent.HumanCheck("h1", "waiting", "shop.example.com", "task:ab12")
        var places = Handover.places(emptyMap(), ask)
        assertEquals(CheckPlace("", "task:ab12"), places["h1"])
        places = Handover.places(places, waiting)
        assertEquals(CheckPlace("shop.example.com", "task:ab12"), places["h1"])
        assertEquals(places, Handover.places(places, TurnEvent.Text("hi")))

        val items = ChatReducer.apply(emptyList(), ask) { "k" }
        val card = items.single() as ChatItem.Approval
        assertTrue(Handover.isCheck(card)); assertTrue(card.open)
        assertEquals(items, Handover.settle(items, waiting))
        assertEquals(true, (Handover.settle(items, waiting.copy(state = "solved")).single() as ChatItem.Approval).answer)
        assertEquals(false, (Handover.settle(items, waiting.copy(state = "skipped")).single() as ChatItem.Approval).answer)
        val gone = Handover.settle(items, waiting.copy(state = "expired")).single() as ChatItem.Approval
        assertTrue(gone.expired); assertFalse(gone.open)
        // answered already here: the server's later word changes nothing
        val skipped = ChatReducer.answer(items, "h1", false)
        assertEquals(skipped, Handover.settle(skipped, waiting.copy(state = "solved")))
    }

    @Test fun aSignInVerificationKeepsItsKindAndStepForTheChatCard() {
        val ask = TurnEvent.parse(Json.parseToJsonElement(
            """{"type":"approval_request","kind":"human_check","approval_id":"v1","name":"browser_check","tab":"owner",
               "check":{"vendor":"kakao","name":"휴대폰에서 로그인을 승인해 주세요","kind":"verification"}}""")) as TurnEvent.ApprovalRequest
        var places = Handover.places(emptyMap(), ask)
        assertTrue(places["v1"]!!.check!!.isVerification)
        // the stream's human_check without a check object keeps the one already known
        places = Handover.places(places, TurnEvent.HumanCheck("v1", "waiting", "accounts.kakao.com", "owner"))
        assertEquals("휴대폰에서 로그인을 승인해 주세요", places["v1"]!!.check!!.name)
        assertEquals("accounts.kakao.com", places["v1"]!!.site)
        // an older server: no check, a person check as before
        val old = TurnEvent.parse(Json.parseToJsonElement(
            """{"type":"approval_request","kind":"human_check","approval_id":"h9","name":"browser_check"}""")) as TurnEvent.ApprovalRequest
        assertNull(Handover.places(emptyMap(), old)["h9"]!!.check)
    }

    @Test fun theLiveViewTakesTheTabOverOnceWhenAskedTo() {
        assertTrue(LiveRules.autoTake(asked = true, done = false, hasFrame = true, controlling = false, someoneElse = false, busy = false))
        assertFalse(LiveRules.autoTake(true, done = true, hasFrame = true, controlling = false, someoneElse = false, busy = false))   // once only
        assertFalse(LiveRules.autoTake(true, false, hasFrame = false, controlling = false, someoneElse = false, busy = false))       // no picture yet
        assertFalse(LiveRules.autoTake(true, false, true, controlling = false, someoneElse = true, busy = false))                    // another device holds it
        assertFalse(LiveRules.autoTake(asked = false, done = false, hasFrame = true, controlling = false, someoneElse = false, busy = false))
    }

    @Test fun anApprovalTitleIsTheServersQuestionWhenItSendsOne() {
        assertEquals("로그인할까요?", Handover.question(" 로그인할까요? "))
        assertNull(Handover.question(""))
        val ev = TurnEvent.parse(Json.parseToJsonElement("""{"type":"approval_request","approval_id":"a","name":"browser_click","ask":"버튼을 누를까요?"}"""))
        assertEquals("버튼을 누를까요?", (ev as TurnEvent.ApprovalRequest).ask)
        val old = TurnEvent.parse(Json.parseToJsonElement("""{"type":"approval_request","approval_id":"a","name":"browser_click"}""")) as TurnEvent.ApprovalRequest
        assertEquals("", old.ask); assertEquals("", old.kind)
        val item = ChatReducer.apply(emptyList(), ev) { "k" }.single() as ChatItem.Approval
        assertEquals("버튼을 누를까요?", item.ask)
        assertFalse(Handover.isCheck(item))
    }
}
