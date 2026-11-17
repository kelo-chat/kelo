package chat.kelo.app.my

import chat.kelo.app.agent.ForgetPlanItem
import chat.kelo.app.agent.ForgetPlanView
import chat.kelo.app.agent.ForgetRules
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** My > Forgotten topics: the list, "Allow again" (also when the topic is already gone), and the list under the forget card. */
@OptIn(ExperimentalCoroutinesApi::class)
class ForgetTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun tearDown() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private val list = """{"items":[
        {"id":"f1","label":"My old job","created_at":1000.0,"removed":{"memory":3,"notes":2,"history":0}},
        {"id":"f2","label":"Trip to Rome","created_at":2000.0,"removed":{}}]}"""

    private fun serve(answer: (String, String, String) -> okhttp3.mockwebserver.MockResponse?) = MyFakeServer(answer).also { server = it }

    @Test fun theListLoadsNewestFirst() {
        val s = serve { m, p, _ -> if (m == "GET" && p == "/api/forget") ok(list) else null }
        val vm = vms.make { ForgetViewModel(s.sessions()) }
        vm.load()
        until("the list") { vm.state.value.items != null }
        assertEquals(listOf("f2", "f1"), vm.state.value.items!!.map { it.id })
        assertFalse(vm.state.value.failed)
    }

    @Test fun aFailedLoadSaysSoAndKeepsNothingShown() {
        val s = serve { _, _, _ -> fail(500, "boom") }
        val vm = vms.make { ForgetViewModel(s.sessions()) }
        vm.load()
        until("failed") { vm.state.value.failed }
        assertNull(vm.state.value.items)
    }

    @Test fun allowAgainDeletesAndTheTopicLeavesTheList() {
        val s = serve { m, p, _ ->
            when {
                m == "GET" && p == "/api/forget" -> ok(list)
                m == "DELETE" && p == "/api/forget/f1" -> ok("""{"ok":true}""")
                else -> null
            }
        }
        val vm = vms.make { ForgetViewModel(s.sessions()) }
        vm.load()
        until("the list") { vm.state.value.items != null }
        vm.allowAgain(vm.state.value.items!!.first { it.id == "f1" })
        until("delete") { s.saw("DELETE /api/forget/f1") }
        until("done") { vm.state.value.busy.isEmpty() }
        assertEquals(listOf("f2"), vm.state.value.items!!.map { it.id })
    }

    @Test fun aTopicThatIsAlreadyGoneCountsAsAllowedWithoutAnError() {
        val s = serve { m, p, _ -> if (m == "GET" && p == "/api/forget") ok(list) else null }   // the DELETE answers 404
        val vm = vms.make { ForgetViewModel(s.sessions()) }
        vm.load()
        until("the list") { vm.state.value.items != null }
        var said = false
        val scope = kotlinx.coroutines.CoroutineScope(Dispatchers.Unconfined)
        scope.launch { vm.messages.collect { said = true } }
        vm.allowAgain(vm.state.value.items!!.first { it.id == "f1" })
        until("delete") { s.saw("DELETE /api/forget/f1") }
        until("done") { vm.state.value.busy.isEmpty() }
        assertEquals(listOf("f2"), vm.state.value.items!!.map { it.id })
        until("the allowed message") { said }
        scope.cancel()
        assertEquals(1, s.requests.count { it == "GET /api/forget" })   // no reload: nothing went wrong
    }

    @Test fun aFailedDeletePutsTheListBack() {
        val s = serve { m, p, _ ->
            when {
                m == "GET" && p == "/api/forget" -> ok(list)
                m == "DELETE" -> fail(500, "boom")
                else -> null
            }
        }
        val vm = vms.make { ForgetViewModel(s.sessions()) }
        vm.load()
        until("the list") { vm.state.value.items != null }
        vm.allowAgain(vm.state.value.items!!.first { it.id == "f1" })
        until("reloaded") { s.requests.count { it == "GET /api/forget" } >= 2 && vm.state.value.busy.isEmpty() }
        until("both topics again") { vm.state.value.items?.size == 2 }
    }

    @Test fun whatWasRemovedIsToldPerCategoryInAFixedOrderWithoutZeros() {
        assertEquals(listOf("memory" to 3, "notes" to 2), ForgetRules.parts(mapOf("history" to 0, "notes" to 2, "memory" to 3, "unknown" to 9)))
        assertTrue(ForgetRules.parts(emptyMap()).isEmpty())
    }

    @Test fun thePlanListHasPreviewsOfAtMostSixtyCharactersAndDropsEmptyOnes() {
        val long = "x".repeat(40) + " " + "y".repeat(80)
        val plan = ForgetPlanView("p1", "Rome", listOf(
            ForgetPlanItem("memory", "  Flight\n to   Rome "), ForgetPlanItem("notes", long), ForgetPlanItem("skills", "   "),
        ))
        val lines = ForgetRules.lines(plan) { it.uppercase() }
        assertEquals(2, lines.size)
        assertEquals("MEMORY · Flight to Rome", lines[0])
        val preview = lines[1].removePrefix("NOTES · ")
        assertEquals(60, preview.length)
        assertTrue(preview.endsWith("…"))
    }

    @Test fun thePlanComesFromTheServerAndAGonePlanIsNull() {
        val s = serve { m, p, _ ->
            if (m == "GET" && p == "/api/forget/plans/p1") ok("""{"id":"p1","label":"Rome","items":[{"category":"memory","show":"Flight to Rome"}]}""") else null
        }
        val api = s.sessions().requireApi()
        kotlinx.coroutines.runBlocking {
            assertEquals("Flight to Rome", chat.kelo.app.agent.ForgetCalls.plan(api, "p1")!!.items.single().show)
            assertNull(chat.kelo.app.agent.ForgetCalls.plan(api, "gone"))
        }
    }
}
