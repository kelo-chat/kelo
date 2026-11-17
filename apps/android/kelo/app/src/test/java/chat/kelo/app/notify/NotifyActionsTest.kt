package chat.kelo.app.notify

import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.chat.TurnApi
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.chat.TurnPoll
import chat.kelo.app.chat.TurnWatch
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ApprovalButtonsTest {
    @Test fun allowAndDenyGiveTwoButtonsInThatOrder() {
        val plan = ApprovalActionRules.plan("ap1", listOf("allow", "deny"))
        assertEquals(listOf(true, false), plan.buttons.map { it.approve })
        assertEquals(2, plan.buttons.map { it.requestCode }.toSet().size)   // one request code each, or one would replace the other
    }

    @Test fun aPaymentOffersDenyOnly() {
        assertEquals(listOf(false), ApprovalActionRules.plan("ap1", listOf("deny")).buttons.map { it.approve })
    }

    @Test fun aCardThatNeedsTheAppGetsNoButtons() {
        assertTrue(ApprovalActionRules.plan("ap1", emptyList()).buttons.isEmpty())
    }

    @Test fun namesThisAppDoesNotKnowAreIgnoredAndNothingIsRemembered() {
        val plan = ApprovalActionRules.plan("ap1", listOf("allow_always", "remember", "deny"))
        assertEquals(listOf(false), plan.buttons.map { it.approve })
    }

    @Test fun everyButtonNeedsTheUnlockedPhoneAndSwipingAnswersNothing() {
        for (offered in listOf(listOf("allow", "deny"), listOf("deny"), emptyList())) {
            val plan = ApprovalActionRules.plan("ap1", offered)
            assertTrue(plan.buttons.all { it.authRequired })
            assertFalse(plan.hasDeleteIntent)
        }
    }

    @Test fun requestCodesDifferPerApproval() {
        val a = ApprovalActionRules.plan("ap1", listOf("allow")).buttons.single().requestCode
        val b = ApprovalActionRules.plan("ap2", listOf("allow")).buttons.single().requestCode
        assertTrue(a != b)
    }

    @Test fun aPendingItemsNotifyActionsAreReadFromTheList() {
        val list = Json.parseToJsonElement(
            """{"items":[{"approval_id":"a","notify_actions":["allow","deny"]},{"approval_id":"b","notify_actions":["deny"]},{"approval_id":"c"}]}""",
        )
        assertEquals(listOf("allow", "deny"), PendingApproval.notifyActionsFor(list, "a"))
        assertEquals(listOf("deny"), PendingApproval.notifyActionsFor(list, "b"))
        assertTrue(PendingApproval.notifyActionsFor(list, "c").isEmpty())   // an older server sends none: no buttons
        assertTrue(PendingApproval.notifyActionsFor(list, "gone").isEmpty())
    }

    @Test fun onlyTheAssistantsAnswersCanBeRepliedTo() {
        assertTrue(ReplyActionRules.replyable(NoteTypes.TASK_DONE))
        assertFalse(ReplyActionRules.replyable(NoteTypes.APPROVAL_WAITING))
        assertFalse(ReplyActionRules.replyable(NoteTypes.STORAGE))
    }
}

private class Notes : ApprovalNotes {
    val log = mutableListOf<String>()
    override fun cancel(tag: String?, id: Int) { log += "cancel:$tag:$id" }
    override fun unlock(tag: String?, id: Int) { log += "unlock:$tag:$id" }
    override fun failed(tag: String?, id: Int) { log += "failed:$tag:$id" }
}

private class Sink(private val status: Int, private val waitMs: Long = 0) : ApprovalSink {
    val sent = mutableListOf<Pair<String, Boolean>>()
    override suspend fun answer(approvalId: String, approve: Boolean): Int {
        if (waitMs > 0) delay(waitMs)
        sent += approvalId to approve
        return status
    }
}

class ApprovalAnswererTest {
    private val both = setOf("allow", "deny")

    private fun answerer(sink: ApprovalSink, notes: Notes, locked: Boolean = false, sdk: Int = 34, budget: Long = 20_000) =
        ApprovalAnswerer(sink, notes, { locked }, sdk, budget)

    @Test fun aSuccessfulAnswerTakesTheNotificationDown() = runTest {
        val notes = Notes(); val sink = Sink(200)
        assertEquals(ApprovalOutcome.Sent, answerer(sink, notes).handle("ap1", true, both, "ap1", 7))
        assertEquals(listOf("ap1" to true), sink.sent)
        assertEquals(listOf("cancel:ap1:7"), notes.log)
    }

    @Test fun anApprovalAnsweredElsewhereAlsoTakesItDown() = runTest {
        val notes = Notes()
        assertEquals(ApprovalOutcome.Sent, answerer(Sink(404), notes).handle("ap1", false, both, "ap1", 7))
        assertEquals(listOf("cancel:ap1:7"), notes.log)
    }

    @Test fun aServerErrorKeepsTheNotificationWithAnOpenActionOnly() = runTest {
        val notes = Notes()
        assertEquals(ApprovalOutcome.Failed, answerer(Sink(500), notes).handle("ap1", true, both, "ap1", 7))
        assertEquals(listOf("failed:ap1:7"), notes.log)
    }

    @Test fun noConnectionIsAFailureToo() = runTest {
        val notes = Notes()
        assertEquals(ApprovalOutcome.Failed, answerer(Sink(ApiApprovalSinkNoSession), notes).handle("ap1", true, both, null, 7))
        assertEquals(listOf("failed:null:7"), notes.log)
    }

    @Test fun anAnswerThatTakesTooLongIsAFailure() = runTest {
        val notes = Notes()
        assertEquals(ApprovalOutcome.Failed, answerer(Sink(200, waitMs = 5_000), notes, budget = 1_000).handle("ap1", true, both, "ap1", 7))
        assertEquals(listOf("failed:ap1:7"), notes.log)
    }

    @Test fun aLockedPhoneBeforeAndroid12AsksToUnlockAndSendsNothing() = runTest {
        val notes = Notes(); val sink = Sink(200)
        assertEquals(ApprovalOutcome.Locked, answerer(sink, notes, locked = true, sdk = 30).handle("ap1", true, both, "ap1", 7))
        assertTrue(sink.sent.isEmpty())
        assertEquals(listOf("unlock:ap1:7"), notes.log)
    }

    @Test fun onAndroid12TheSystemAsksForTheUnlockSoNothingIsCheckedHere() = runTest {
        val sink = Sink(200)
        assertEquals(ApprovalOutcome.Sent, answerer(sink, Notes(), locked = true, sdk = 31).handle("ap1", true, both, "ap1", 7))
        assertEquals(1, sink.sent.size)
    }

    @Test fun aPaymentNeverSendsAllow() = runTest {
        val notes = Notes(); val sink = Sink(200)
        val offeredForAPayment = setOf("deny")
        assertEquals(ApprovalOutcome.Refused, answerer(sink, notes).handle("pay1", true, offeredForAPayment, "pay1", 7))
        assertTrue(sink.sent.isEmpty())
        assertTrue(notes.log.isEmpty())
        // its Deny is fine
        assertEquals(ApprovalOutcome.Sent, answerer(sink, notes).handle("pay1", false, offeredForAPayment, "pay1", 7))
        assertEquals(listOf("pay1" to false), sink.sent)
    }

    @Test fun anAllowWithNothingOfferedIsNotSent() = runTest {
        val sink = Sink(200)
        assertEquals(ApprovalOutcome.Refused, answerer(sink, Notes()).handle("ap1", true, ApprovalActionRules.offeredOf(null), "ap1", 7))
        assertTrue(sink.sent.isEmpty())
    }

    @Test fun offeredNamesAreReadFromTheIntentExtra() {
        assertEquals(setOf("allow", "deny"), ApprovalActionRules.offeredOf("allow,deny"))
        assertEquals(setOf("deny"), ApprovalActionRules.offeredOf(" deny "))
        assertTrue(ApprovalActionRules.offeredOf("").isEmpty())
    }

    private companion object { const val ApiApprovalSinkNoSession = -1 }
}

private class ReplyLog : ReplyNotes {
    val log = mutableListOf<String>()
    override fun sent(tag: String?, id: Int, text: String) { log += "sent:$id:$text" }
    override fun restore(tag: String?, id: Int) { log += "restore:$id" }
    override fun failed(tag: String?, id: Int) { log += "failed:$id" }
}

private class FakeTurns(private val startError: Exception? = null) : TurnApi {
    val started = mutableListOf<Pair<String, String>>()
    override suspend fun start(text: String, timezone: String): String {
        started += text to timezone
        startError?.let { throw it }
        return "turn1"
    }
    override suspend fun poll(id: String, after: Int) = TurnPoll(listOf(TurnEvent.Text("hi")), 1, true)
    override suspend fun stop(id: String) {}
}

private class Watch : TurnWatch {
    val log = mutableListOf<String>()
    override fun started() { log += "started" }
    override fun event(ev: TurnEvent) { log += "event" }
    override fun finished(ok: Boolean) { log += "finished:$ok" }
    override fun cancelled() { log += "cancelled" }
}

class ReplyRunnerTest {
    @Test fun blankTextIsIgnoredAndSendsNothing() = runTest {
        for (blank in listOf(null, "", "   \n")) {
            val api = FakeTurns(); val notes = ReplyLog(); val watch = Watch()
            assertFalse(ReplyRunner(api, watch, notes) { "Asia/Seoul" }.run(blank, null, 9))
            assertTrue(api.started.isEmpty())
            assertTrue(watch.log.isEmpty())
            assertEquals(listOf("restore:9"), notes.log)   // the spinner stops, nothing else
        }
        assertNull(ReplyActionRules.clean("  "))
    }

    @Test fun typedTextStartsATurnAndTheWatchFollowsIt() = runTest {
        val api = FakeTurns(); val notes = ReplyLog(); val watch = Watch()
        assertTrue(ReplyRunner(api, watch, notes) { "Asia/Seoul" }.run("  yes please ", null, 9))
        assertEquals(listOf("yes please" to "Asia/Seoul"), api.started)   // POST /api/turns {text, timezone}
        assertEquals(listOf("sent:9:yes please"), notes.log)               // shown as sent: the spinner stops
        assertEquals(listOf("started", "event", "finished:true"), watch.log)
    }

    @Test fun aFailureRepostsWithTheReplyKeptAndTheWatchStops() = runTest {
        val api = FakeTurns(startError = RuntimeException("offline")); val notes = ReplyLog(); val watch = Watch()
        assertFalse(ReplyRunner(api, watch, notes) { "Asia/Seoul" }.run("hello", null, 9))
        assertEquals(listOf("sent:9:hello", "failed:9"), notes.log)
        assertEquals(listOf("started", "cancelled"), watch.log)   // the foreground service ends with it
    }
}
