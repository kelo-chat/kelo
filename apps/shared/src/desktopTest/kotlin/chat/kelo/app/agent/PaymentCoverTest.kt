package chat.kelo.app.agent

import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.my.MyFakeServer
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import java.util.Collections
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
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

/**
 * Payments approved in advance: the permissions section (orders, schedule allows, the switch), the order_preapprove card summary,
 * the forget card's plan id, and the payment notice that says why no card was asked.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PaymentCoverTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun tearDown() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private val orders = mutableListOf(
        """{"id":"op_b","owner_kind":"schedule","owner_id":"3","owner_name":"Weekly shop","intent":"Groceries","items":[{"description":"Milk","qty":2}],
            "max_total":60.5,"currency":"EUR","expires_at":2000,"state":"active","created_at":1}""",
        """{"id":"op_a","owner_kind":"task","owner_id":"9","owner_name":"","intent":"Running shoes","items":[],
            "max_total":120,"currency":"USD","expires_at":1000,"state":"active","created_at":2}""",
    )
    private val allows = mutableListOf("""{"id":"sa_1","schedule_id":3,"schedule_name":"Weekly shop","merchant_origin":"https://shop.example","payment_label":"Visa ••4242","currency":"EUR","created_at":5}""")
    @Volatile private var enabled = true
    @Volatile private var deleteStatus = 200
    @Volatile private var putStatus = 200
    @Volatile private var old = false
    private val bodies = Collections.synchronizedList(mutableListOf<String>())

    private fun fake() = MyFakeServer { m, p, body ->
        when {
            p == "/api/approvals/pending" -> ok("""{"items":[]}""")
            p == "/api/approvals/rules" -> ok("""{"items":[]}""")
            p.startsWith("/api/approvals/history") -> ok("""{"items":[],"next_before":null}""")
            p == "/api/task-permissions" || p == "/api/permissions/recipients" -> ok("""{"items":[]}""")
            old && p.startsWith("/api/permissions/") -> null
            m == "GET" && p == "/api/permissions/preapprovals" -> ok("""{"enabled":$enabled,"items":[${orders.joinToString(",")}]}""")
            m == "GET" && p == "/api/permissions/pay-allows" -> ok("""{"enabled":$enabled,"items":[${allows.joinToString(",")}]}""")
            m == "GET" && p == "/api/permissions/payment-cover" -> ok("""{"enabled":$enabled}""")
            m == "PUT" && p == "/api/permissions/payment-cover" -> {
                bodies += body
                if (putStatus != 200) fail(putStatus, "no") else { enabled = Json.parseToJsonElement(body).jsonObject["enabled"]!!.jsonPrimitive.content.toBoolean(); ok("""{"enabled":$enabled}""") }
            }
            m == "DELETE" && p.startsWith("/api/permissions/preapprovals/") -> {
                if (deleteStatus == 200) orders.removeAll { it.contains("\"${p.substringAfterLast('/')}\"") }
                if (deleteStatus == 200) ok("""{"ok":true}""") else fail(deleteStatus, "gone")
            }
            m == "DELETE" && p.startsWith("/api/permissions/pay-allows/") -> {
                if (deleteStatus == 200) allows.clear()
                if (deleteStatus == 200) ok("""{"ok":true}""") else fail(deleteStatus, "gone")
            }
            else -> null
        }
    }.also { server = it }

    private fun vm(s: MyFakeServer): AgentViewModel { val sessions = s.sessions(); return vms.make { AgentViewModel(sessions) } }

    private fun loaded(vm: AgentViewModel): Permissions { until("perms") { vm.perms.value.data != null }; return vm.perms.value.data!! }

    @Test fun theSectionListsOrdersByEndDateAndScheduleAllows() {
        val vm = vm(fake())
        vm.loadPerms()
        val cover = loaded(vm).cover!!
        assertTrue(cover.enabled)
        assertEquals(listOf("op_a", "op_b"), cover.orders.map { it.id })   // ends first on top
        val schedule = cover.orders[1]
        assertTrue(schedule.forSchedule)
        assertEquals("Weekly shop", schedule.ownerName)
        assertEquals(60.5, schedule.maxTotal, 0.0)
        assertEquals("EUR", schedule.currency)
        assertEquals(2000L, schedule.expiresAt)
        assertEquals("Groceries", PaymentCoverRules.what(schedule.intent, schedule.items))
        assertEquals("2 × Milk", PaymentCoverRules.what("", schedule.items))
        val allow = cover.allows.single()
        assertEquals("Weekly shop", allow.scheduleName)
        assertEquals("https://shop.example", allow.merchantOrigin)
        assertEquals("Visa ••4242", allow.paymentLabel)
    }

    @Test fun anOlderServerWithoutTheRoutesHasNoSection() {
        old = true
        val vm = vm(fake())
        vm.loadPerms()
        assertNull(loaded(vm).cover)
    }

    @Test fun removingAnOrderDeletesItAndKeepsTheRest() {
        val s = fake()
        val vm = vm(s)
        vm.loadPerms()
        val cover = loaded(vm).cover!!
        vm.removePreapproval(cover.orders.first { it.id == "op_a" })
        until("delete") { s.saw("DELETE /api/permissions/preapprovals/op_a") }
        assertEquals(listOf("op_b"), vm.perms.value.data!!.cover!!.orders.map { it.id })
    }

    @Test fun a404OnDeleteMeansAlreadyGoneTheListLoadsAgainAndNothingIsSaid() {
        val s = fake()
        val vm = vm(s)
        vm.loadPerms()
        loaded(vm)
        var said = 0
        val scope = CoroutineScope(Dispatchers.Unconfined)
        scope.launch { vm.messages.collect { said++ } }
        deleteStatus = 404
        orders.removeAll { it.contains("op_a") }   // it really is gone on the server
        vm.removePreapproval(vm.perms.value.data!!.cover!!.orders.first { it.id == "op_a" })
        until("delete") { s.saw("DELETE /api/permissions/preapprovals/op_a") }
        until("reloaded") { s.requests.count { it == "GET /api/permissions/preapprovals" } >= 2 }
        until("list") { vm.perms.value.data?.cover?.orders?.map { it.id } == listOf("op_b") }
        assertEquals(0, said)
        scope.cancel()
    }

    @Test fun anotherFailureOnDeleteIsToldAndTheListLoadsAgain() {
        val s = fake()
        val vm = vm(s)
        vm.loadPerms()
        loaded(vm)
        var said = 0
        val scope = CoroutineScope(Dispatchers.Unconfined)
        scope.launch { vm.messages.collect { said++ } }
        deleteStatus = 500
        vm.removePayAllow(vm.perms.value.data!!.cover!!.allows.single())
        until("told") { said == 1 }
        until("reloaded") { vm.perms.value.data?.cover?.allows?.size == 1 && s.requests.count { it == "GET /api/permissions/pay-allows" } >= 2 }
        scope.cancel()
    }

    @Test fun removingAScheduleAllowDeletesIt() {
        val s = fake()
        val vm = vm(s)
        vm.loadPerms()
        vm.removePayAllow(loaded(vm).cover!!.allows.single())
        until("delete") { s.saw("DELETE /api/permissions/pay-allows/sa_1") }
        assertTrue(vm.perms.value.data!!.cover!!.allows.isEmpty())
    }

    @Test fun theSwitchSendsEnabledOffAndOnAndAFailurePutsItBack() {
        val s = fake()
        val vm = vm(s)
        vm.loadPerms()
        loaded(vm)
        vm.setPaymentCover(false)
        until("off") { bodies.size == 1 }
        until("shown off") { vm.perms.value.data?.cover?.enabled == false }
        assertEquals(false, Json.parseToJsonElement(bodies[0]).jsonObject["enabled"]!!.jsonPrimitive.content.toBoolean())
        assertEquals(2, vm.perms.value.data!!.cover!!.orders.size)   // rows stay listed while it is off
        vm.setPaymentCover(true)
        until("on") { bodies.size == 2 }
        until("shown on") { vm.perms.value.data?.cover?.enabled == true }
        assertEquals(true, Json.parseToJsonElement(bodies[1]).jsonObject["enabled"]!!.jsonPrimitive.content.toBoolean())
        putStatus = 500
        vm.setPaymentCover(false)
        until("tried") { bodies.size == 3 }
        until("put back") { vm.perms.value.data?.cover?.enabled == true }
    }

    // ---- the card of order_preapprove
    private fun event(json: String) = TurnEvent.parse(Json.parseToJsonElement(json))

    private val card = """
        {"type":"approval_request","approval_id":"a1","name":"order_preapprove","arguments":{},"detail":"x",
         "preapproval":{"intent":"Two pairs of running shoes","items":[{"description":"Running shoes, size 42","qty":2},{"description":"Socks","qty":1}],
           "requirements":["Delivery before Friday"],"shipping":"Home, 3 days","merchant_policy":"Any shop that meets the requirements",
           "delegated_choices":["colour"],"max_total":240.5,"currency":"eur","max_total_text":"240.50 EUR","expires_at":1794744000,"owner_kind":"schedule"}}
    """.trimIndent()

    @Test fun theCardSaysWhatTheAllInCapTheEndAndThatItIsOneOrder() {
        val ev = event(card) as TurnEvent.ApprovalRequest
        val p = ev.preapproval!!
        assertEquals(listOf("Two pairs of running shoes", "2 × Running shoes, size 42", "Socks"), PaymentCoverRules.whatLines(p))
        assertEquals("240.50 EUR", PaymentCoverRules.capText(p))
        assertEquals(240.5, p.maxTotal, 0.0)
        assertEquals("EUR", p.currency)
        assertEquals(1794744000L, p.expiresAt)
        assertTrue(p.forSchedule)
        assertEquals(listOf("Delivery before Friday"), p.requirements)
        assertEquals("Home, 3 days", p.shipping)
        assertEquals(listOf("colour"), p.delegatedChoices)
        val item = ChatReducer.apply(emptyList(), ev) { "k1" }.single() as ChatItem.Approval
        assertEquals(p, item.preapproval)
        assertEquals("", item.forgetPlan)
    }

    @Test fun theCapFallsBackToTheNumberAndCurrencyWithoutTheServersText() {
        val p = (event(card.replace(""""max_total_text":"240.50 EUR",""", "")) as TurnEvent.ApprovalRequest).preapproval!!
        assertEquals("240.50 EUR", PaymentCoverRules.capText(p))
        assertEquals("120 USD", PaymentCoverRules.amount(120.0, "USD"))
    }

    @Test fun anOrdinaryCardOrOneWithoutACapHasNoSummary() {
        assertNull((event("""{"type":"approval_request","approval_id":"a2","name":"send","arguments":{}}""") as TurnEvent.ApprovalRequest).preapproval)
        assertNull((event(card.replace("\"max_total\":240.5", "\"max_total\":0")) as TurnEvent.ApprovalRequest).preapproval)
    }

    @Test fun theForgetCardKeepsThePlanIdToShowItsList() {
        val ev = event("""{"type":"approval_request","approval_id":"a3","name":"forget_confirm","arguments":{"plan_id":"p9"}}""") as TurnEvent.ApprovalRequest
        assertEquals("p9", ev.forgetPlan)
        assertEquals("p9", (ChatReducer.apply(emptyList(), ev) { "k1" }.single() as ChatItem.Approval).forgetPlan)
        val other = event("""{"type":"approval_request","approval_id":"a4","name":"send","arguments":{"plan_id":"p9"}}""") as TurnEvent.ApprovalRequest
        assertEquals("", other.forgetPlan)
    }

    // ---- the payment notice
    @Test fun thePaymentNoticeSaysWhetherTheOrderOrTheSchedulesAllowCoveredIt() {
        val order = event("""{"type":"payment_auto","id":"c1","payment":{"site":"shop.example","amount_text":"24.00 EUR","summary":"Shoes"},"preapproval":{"kind":"preapproval","via":"Visa ••4242"}}""")
        val item = ChatReducer.apply(emptyList(), order) { "k1" }.single() as ChatItem.PaymentAuto
        assertEquals(PreapprovalCover("preapproval", "Visa ••4242"), item.cover)
        assertFalse(item.cover!!.bySchedule)
        val sched = event("""{"type":"payment_auto","id":"c2","payment":{"site":"shop.example","amount_text":"24.00 EUR"},"preapproval":{"kind":"schedule_allow","via":"shop.example"}}""")
        assertTrue((ChatReducer.apply(emptyList(), sched) { "k1" }.single() as ChatItem.PaymentAuto).cover!!.bySchedule)
        val plain = event("""{"type":"payment_auto","id":"c3","payment":{"site":"shop.example","amount_text":"24.00 EUR"}}""")
        assertNull((ChatReducer.apply(emptyList(), plain) { "k1" }.single() as ChatItem.PaymentAuto).cover)
        assertNotNull(PreapprovalCover.parse(Json.parseToJsonElement("""{"kind":"preapproval"}""")))
        assertNull(PreapprovalCover.parse(Json.parseToJsonElement("""{"via":"x"}""")))
    }
}
