package chat.kelo.app.agent

import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.my.MyFakeServer
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import java.util.Collections
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The outgoing message inside an approval card (edit sheet rules, what Approve sends) and the Past approvals list (paging, empty, error). */
@OptIn(ExperimentalCoroutinesApi::class)
class ApprovalEditTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null
    private val posts = Collections.synchronizedList(mutableListOf<String>())

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()); ApprovalDrafts.clear("a1") }
    @After fun tearDown() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private fun item(editable: String = ""","editable":{"field":"body","text":"See you at five.","max":null}""", extra: String = ""): JsonObject =
        Json.parseToJsonElement(
            """{"type":"approval_request","approval_id":"a1","name":"email_send","arguments":{},"detail":"Mail","label":"Send mail","asked_at":1,"unattended":false,
               "lines":[{"label":"To","value":"bob@example.com"}]$editable$extra}""",
        ).jsonObject

    private fun pending(o: JsonObject = item()) = PendingApproval.parse(o)!!

    // ---- the card's data and the sheet's rules
    @Test fun onlyACardWithAnEditableObjectIsEditable() {
        assertEquals(EditableMessage("body", "See you at five.", null), pending().event.editable)
        assertEquals(1600, pending(item(""","editable":{"field":"text","text":"Hi","max":1600}""")).event.editable!!.max)
        assertNull(pending(item("")).event.editable)
        assertNull(pending(item(""","editable":{"field":"","text":"x"}""")).event.editable)
        // a payment is never editable, whatever the server sent
        assertNull(pending(item(extra = ""","payment":{"site":"shop.example","amount":10,"currency":"USD","amount_text":"$10"}""")).event.editable)
    }

    @Test fun anEmptyOrTooLongMessageCannotBeSaved() {
        assertEquals(EditProblem.EMPTY, ApprovalEditRules.problem("   \n", null))
        assertEquals(EditProblem.EMPTY, ApprovalEditRules.problem("", 160))
        assertEquals(EditProblem.TOO_LONG, ApprovalEditRules.problem("x".repeat(161), 160))
        assertNull(ApprovalEditRules.problem("x".repeat(160), 160))
        assertNull(ApprovalEditRules.problem("x".repeat(100_000), null))   // no limit: nothing is too long
        assertNull(ApprovalEditRules.counter("hello", null))
        assertEquals(5 to 160, ApprovalEditRules.counter(" hello ", 160))
    }

    @Test fun theMessageLineIsNotShownTwice() {
        val e = EditableMessage("text", "I am late.", 1600)
        val lines = listOf(chat.kelo.app.chat.ApprovalLine("To", "+82 10"), chat.kelo.app.chat.ApprovalLine("Message", "I am late.", true))
        assertEquals(listOf("To"), ApprovalEditRules.withoutMessage(lines, e).map { it.label })
    }

    @Test fun aSavedTextEqualToTheOriginalIsNoEdit() {
        val e = EditableMessage("body", "See you at five.", null)
        assertNull(ApprovalEditRules.toSend(e, null))
        assertNull(ApprovalEditRules.toSend(e, " See you at five. "))
        assertEquals("Late.", ApprovalEditRules.toSend(e, " Late. "))
        assertNull(ApprovalEditRules.toSend(null, "Late."))
    }

    // ---- what Approve sends
    private fun fake(past: (String) -> okhttp3.mockwebserver.MockResponse? = { ok("""{"items":[],"next_before":null}""") }) = MyFakeServer { m, p, body ->
        when {
            m == "POST" && p == "/api/approvals/a1" -> { posts += body; ok("""{"ok":true,"decision":"allowed_once","remembered":false,"rule":null}""") }
            p == "/api/approvals/pending" -> ok("""{"items":[]}""")
            p == "/api/approvals/rules" -> ok("""{"items":[]}""")
            p == "/api/task-permissions" || p == "/api/permissions/recipients" -> ok("""{"items":[]}""")
            p.startsWith("/api/approvals/history") -> past(p)
            else -> null
        }
    }.also { server = it }

    private fun vm(s: MyFakeServer): AgentViewModel { val sessions = s.sessions(); return vms.make { AgentViewModel(sessions) } }

    /** A view model whose permissions are loaded (an answer needs them). */
    private fun ready(s: MyFakeServer): AgentViewModel { val vm = vm(s); vm.loadPerms(); until("perms") { vm.perms.value.data != null }; return vm }

    @Test fun approveSendsTheFixedTextAndDenyNeverDoes() {
        val vm = ready(fake())
        val a = pending()
        ApprovalDrafts.set("a1", "Running ten minutes late.")
        vm.answer(a, approve = false)
        until("deny") { posts.size == 1 }
        assertFalse(Json.parseToJsonElement(posts[0]).jsonObject.containsKey("edited"))
        until("a deny clears the draft too") { ApprovalDrafts.get("a1") == null }
        until("the first answer settles") { vm.perms.value.data?.answering?.isEmpty() == true }
        ApprovalDrafts.set("a1", "Running ten minutes late.")
        vm.answer(a, approve = true)
        until("allow") { posts.size == 2 }
        val edited = Json.parseToJsonElement(posts[1]).jsonObject["edited"]!!.jsonObject
        assertEquals("Running ten minutes late.", edited["body"]!!.jsonPrimitive.content)
        assertEquals(1, edited.size)   // exactly that one argument
        until("cleared") { ApprovalDrafts.get("a1") == null }
    }

    @Test fun approveWithoutAChangeSendsNoEdit() {
        val vm = ready(fake())
        vm.answer(pending(), approve = true)
        until("allow") { posts.size == 1 }
        assertFalse(Json.parseToJsonElement(posts[0]).jsonObject.containsKey("edited"))
    }

    // ---- Past approvals
    private fun row(i: Int, extra: String = "") = """{"id":"p$i","task_id":null,"tool":"email_send","label":"Send mail","site":"","detail":"Mail $i","decision":"allowed_once","asked_at":${100 - i},"answered_at":${101 - i},"rule_id":null,"unattended":false,"can_remember":false,"kind":"send","where":"bob@example.com","via":"app","edited":${i == 0},"result":"allow"$extra}"""

    @Test fun pastApprovalsPageByCursorAndDoNotRepeat() {
        val s = fake { p ->
            if ("before=" in p) ok("""{"items":[${row(1)},${row(2)}],"next_before":null}""")
            else ok("""{"items":[${row(0)},${row(1)}],"next_before":99.0}""")
        }
        val vm = vm(s)
        vm.loadPast()
        until("first page") { vm.past.value.data != null }
        assertEquals(listOf("p0", "p1"), vm.past.value.data!!.items.map { it.id })
        assertTrue(vm.past.value.data!!.items[0].edited)
        assertEquals("bob@example.com", PastApprovalsRules.where(vm.past.value.data!!.items[0]))
        assertEquals("app", PastApprovalsRules.answeredBy(vm.past.value.data!!.items[0]))
        vm.loadMorePast()
        until("second page") { vm.past.value.data!!.next == null }
        assertEquals(listOf("p0", "p1", "p2"), vm.past.value.data!!.items.map { it.id })   // p1 came twice: shown once
        assertTrue(s.saw("GET /api/approvals/history?limit=50&before=99.0"))
    }

    @Test fun anEmptyListIsEmptyNotAnError() {
        val vm = vm(fake { ok("""{"items":[],"next_before":null}""") })
        vm.loadPast()
        until("empty") { vm.past.value.data != null }
        assertTrue(vm.past.value.data!!.items.isEmpty())
        assertFalse(vm.past.value.failed)
    }

    @Test fun aFailedLoadSaysSoAndCanBeTriedAgain() {
        var broken = true
        val vm = vm(fake { if (broken) fail(500, "boom") else ok("""{"items":[${row(0)}],"next_before":null}""") })
        vm.loadPast()
        until("failed") { vm.past.value.failed }
        assertNull(vm.past.value.data)
        broken = false
        vm.loadPast()
        until("loaded") { vm.past.value.data != null }
        assertFalse(vm.past.value.failed)
    }

    @Test fun aFailedMoreKeepsTheRowsAndSaysSo() {
        var more = false
        val vm = vm(fake { p -> if ("before=" in p) { more = true; fail(500, "boom") } else ok("""{"items":[${row(0)}],"next_before":50.0}""") })
        vm.loadPast()
        until("first") { vm.past.value.data != null }
        vm.loadMorePast()
        until("more failed") { more && vm.past.value.data!!.moreFailed }
        assertEquals(1, vm.past.value.data!!.items.size)
        assertNotNull(vm.past.value.data!!.next)   // the button stays: it can be tried again
    }

    @Test fun anOlderRowWithoutTheNewFieldsStillReads() {
        val old = Json { ignoreUnknownKeys = true }.decodeFromString<ApprovalRecord>("""{"id":"x","decision":"denied","site":"shop.example"}""")
        assertEquals("deny", PastApprovalsRules.result(old))
        assertEquals("shop.example", PastApprovalsRules.where(old))
        assertNull(PastApprovalsRules.answeredBy(old))
    }
}
