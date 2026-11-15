package chat.kelo.app.followup

import chat.kelo.app.chat.ActionOutcome
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.core.KeloApi
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FollowupRulesTest {
    private fun a(kind: String, name: String = "", approval: String = "", worker: String = "", fid: String = "", minutes: Int? = null) =
        FollowupAction(kind, name, approval, worker, fid, minutes)

    @Test fun `each action kind maps to its step`() {
        assertEquals(FollowupStep.OpenLogin("login-naver"), FollowupRules.step(a("open_login", name = "login-naver")))
        assertEquals(FollowupStep.Answer("ap1", true), FollowupRules.step(a("approve", approval = "ap1")))
        assertEquals(FollowupStep.Answer("ap1", false), FollowupRules.step(a("deny", approval = "ap1")))
        assertEquals(FollowupStep.ResumeWorker("w9"), FollowupRules.step(a("resume", worker = "w9", fid = "4")))
        // resume is the job's own call: without a worker there is nothing to resume
        assertNull(FollowupRules.step(a("resume", fid = "4")))
        assertEquals(FollowupStep.Drop("4"), FollowupRules.step(a("drop", fid = "4")))
        assertEquals(FollowupStep.Snooze("4", 30), FollowupRules.step(a("snooze", fid = "4", minutes = 30)))
        assertEquals(FollowupStep.Snooze("4", FollowupRules.DEFAULT_SNOOZE_MINUTES), FollowupRules.step(a("snooze", fid = "4")))
    }

    @Test fun `actions missing what they need, or unknown kinds, show no button`() {
        assertNull(FollowupRules.step(a("open_login")))
        assertNull(FollowupRules.step(a("approve")))
        assertNull(FollowupRules.step(a("drop")))
        assertNull(FollowupRules.step(a("resume")))
        assertNull(FollowupRules.step(a("launch_rocket", fid = "1")))
        // the follow-up's own id stands in
        assertEquals(FollowupStep.Drop("7"), FollowupRules.step(a("drop"), ownId = "7"))
    }

    @Test fun `buttons are unique and capped`() {
        val list = listOf(a("drop", fid = "1"), a("drop", fid = "1"), a("snooze", fid = "1", minutes = 10), a("snooze", fid = "1", minutes = 60),
            a("resume", worker = "w"), a("approve", approval = "x"), a("nope"))
        val b = FollowupRules.buttons(list)
        assertEquals(FollowupRules.MAX_BUTTONS, b.size)
        assertEquals(b.map { it.second }.distinct(), b.map { it.second })
    }

    @Test fun `on hold cards show exactly what the server sent`() {
        val f = Followup("5", "worker", "도서관 책 연장", Followup.ON_HOLD, null, 1.0,
            listOf(a("resume", worker = "w1"), a("snooze", fid = "5", minutes = 60), a("drop", fid = "5")))
        assertEquals(listOf(FollowupStep.ResumeWorker("w1"), FollowupStep.Snooze("5", 60), FollowupStep.Drop("5")), FollowupRules.holdButtons(f).map { it.second })
        assertEquals(listOf(FollowupStep.Drop("5")), FollowupRules.holdButtons(f.copy(actions = listOf(a("drop")))).map { it.second })
    }

    @Test fun `on hold list and delays`() {
        val items = listOf(
            Followup("1", "reply", "", Followup.FIRST, null, 5.0, emptyList()),
            Followup("2", "reply", "", Followup.ON_HOLD, null, 9.0, emptyList()),
            Followup("3", "reply", "", Followup.ON_HOLD, null, 2.0, emptyList()),
            Followup("4", "reply", "", Followup.ON_HOLD, null, 1.0, emptyList(), status = Followup.DROPPED),
        )
        assertEquals(listOf("3", "2"), FollowupRules.onHold(items).map { it.id })
        assertEquals(30 to 30, FollowupRules.delays(30, 10))
        assertEquals(30 to 180, FollowupRules.delays(30, 180))
        assertEquals(1 to 5, FollowupRules.delays(0, 5))
        assertEquals(listOf(10, 15, 30, 45, 60), FollowupRules.choices(listOf(10, 15, 30, 60), 45))
    }

    @Test fun `server calls per step`() {
        assertNull(FollowupRules.call(FollowupStep.OpenLogin("x")))
        assertEquals("/api/approvals/a%20b", FollowupRules.call(FollowupStep.Answer("a b", true))!!.second)
        assertEquals("""{"approve":false}""", FollowupRules.call(FollowupStep.Answer("a", false))!!.third.toString())
        assertEquals("/api/workers/w1/resume", FollowupRules.call(FollowupStep.ResumeWorker("w1"))!!.second)
        assertEquals("/api/followups/3/retry", FollowupRules.call(FollowupStep.Retry("3"))!!.second)
        assertEquals("/api/followups/3/drop", FollowupRules.call(FollowupStep.Drop("3"))!!.second)
        val snooze = FollowupRules.call(FollowupStep.Snooze("3", 45))!!
        assertEquals("/api/followups/3/snooze", snooze.second)
        assertEquals("""{"minutes":45}""", snooze.third.toString())
        assertTrue(FollowupRules.gone(404) && FollowupRules.gone(409) && FollowupRules.gone(410))
        assertFalse(FollowupRules.gone(500))
    }

    @Test fun `lists and actions parse leniently`() {
        val list = Followup.parseList(Json.parseToJsonElement(
            """{"items":[{"id":12,"kind":"login","title":"네이버 로그인","stage":"on_hold","next_at":null,"created_at":100.5,""" +
                """"message_id":88,"status":"open",""" +
                """"actions":[{"kind":"open_login","name":"login-naver"},{"kind":"snooze","followup_id":12,"minutes":60},{"bad":1}]},{"kind":"x"}]}""",
        ))
        assertEquals(1, list.size)
        val f = list[0]
        assertEquals("12", f.id)
        assertTrue(f.onHold)
        assertEquals(2, f.actions.size)
        assertEquals("12", f.actions[1].followupId)
        assertEquals(60, f.actions[1].minutes)
        assertEquals(88L, f.messageId)
        assertTrue(f.open)
        assertEquals(1, Followup.parseList(Json.parseToJsonElement("""[{"id":"a"}]""")).size)
    }

    @Test fun `each message gets its follow-up, an open one before a closed one`() {
        val items = listOf(
            Followup("1", "login", "", Followup.FIRST, null, 0.0, emptyList(), messageId = 10, status = Followup.RESOLVED),
            Followup("2", "login", "", Followup.SECOND, null, 0.0, listOf(a("drop", fid = "2")), messageId = 10),
            Followup("3", "reply", "", Followup.FIRST, null, 0.0, emptyList(), messageId = 11, status = Followup.DROPPED),
            Followup("4", "reply", "", Followup.FIRST, null, 0.0, emptyList()),
        )
        val map = FollowupRules.byMessage(items)
        assertEquals(setOf(10L, 11L), map.keys)
        assertEquals("2", map[10]!!.id)
        assertEquals("3", map[11]!!.id)
    }

    @Test fun `a closed follow-up leaves one line, what was pressed here first`() {
        val open = Followup("2", "login", "", Followup.SECOND, null, 0.0, emptyList(), messageId = 10)
        assertNull(ChatReducer.closedOutcome(open, null))
        assertEquals(ActionOutcome.SNOOZED, ChatReducer.closedOutcome(open, ActionOutcome.SNOOZED))
        assertEquals(ActionOutcome.DROPPED, ChatReducer.closedOutcome(open.copy(status = Followup.DROPPED), null))
        assertEquals(ActionOutcome.RESOLVED, ChatReducer.closedOutcome(open.copy(status = Followup.RESOLVED), null))
        assertEquals(ActionOutcome.DROPPED, ChatReducer.outcome(FollowupStep.Drop("1"), StepResult.DONE))
        assertEquals(ActionOutcome.RESUMED, ChatReducer.outcome(FollowupStep.ResumeWorker("w"), StepResult.DONE))
        assertEquals(ActionOutcome.GONE, ChatReducer.outcome(FollowupStep.Snooze("1", 5), StepResult.GONE))
        assertNull(ChatReducer.outcome(FollowupStep.Snooze("1", 5), StepResult.FAILED))
    }

    @Test fun `a followup turn event is read, and settings use the server's names`() {
        val ev = TurnEvent.parse(Json.parseToJsonElement("""{"type":"followup","id":"c1","followup_id":7,"kind":"reply","status":"open"}"""))
        assertEquals(TurnEvent.Followup("7", "open"), ev)
        val st = KeloApi.json.decodeFromString<FollowupSettings>("""{"enabled":false,"first_delay_min":15,"second_delay_min":240,"awake":true}""")
        assertEquals(FollowupSettings(false, 15, 240), st)
    }
}

class FollowupRetryTest {
    @Test fun `an expired approval offers retry and shows in asks without a made-up resume`() {
        val f = Followup.parseList(Json.parseToJsonElement(
            """{"items":[{"id":12,"kind":"approval_expired","title":"장보기 주문","stage":"first","origin":"schedule","schedule_id":4,"created_at":1,""" +
                """"actions":[{"kind":"retry","followup_id":12,"label":"다시 시도"},{"kind":"snooze","followup_id":12,"minutes":60},{"kind":"drop","followup_id":12}]}]}""",
        )).single()
        assertEquals("schedule", f.origin)
        assertEquals(4L, f.scheduleId)
        assertEquals(listOf(f), FollowupRules.onHold(listOf(f)))
        val b = FollowupRules.holdButtons(f).map { it.second }
        assertEquals(listOf(FollowupStep.Retry("12"), FollowupStep.Snooze("12", 60), FollowupStep.Drop("12")), b)
        assertEquals("/api/followups/12/retry", FollowupRules.call(FollowupStep.Retry("12"))!!.second)
        // retry absent (the run cannot start again): only what came
        assertEquals(listOf(FollowupStep.Drop("12")), FollowupRules.holdButtons(f.copy(actions = listOf(FollowupAction("drop", followupId = "12")))).map { it.second })
    }

    @Test fun `retry results`() = kotlinx.coroutines.runBlocking {
        val server = okhttp3.mockwebserver.MockWebServer().apply { start() }
        try {
            val api = KeloApi(server.url("/").toString(), "t")
            server.enqueue(okhttp3.mockwebserver.MockResponse().setBody("""{"ok":true,"how":"worker_resumed"}"""))
            assertEquals(StepResult.DONE, FollowupCalls(api).run(FollowupStep.Retry("12")).first)
            server.enqueue(okhttp3.mockwebserver.MockResponse().setResponseCode(404).setBody("""{"detail":"no"}"""))
            assertEquals(StepResult.GONE, FollowupCalls(api).run(FollowupStep.Retry("12")).first)
            server.enqueue(okhttp3.mockwebserver.MockResponse().setResponseCode(409).setBody("""{"detail":"이 작업은 다시 시작할 수 없어요"}"""))
            val r = FollowupCalls(api).run(FollowupStep.Retry("12"))
            assertEquals(StepResult.FAILED to "이 작업은 다시 시작할 수 없어요", r)
        } finally { server.shutdown() }
    }
}
