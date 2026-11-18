package chat.kelo.app.shell

import chat.kelo.app.my.MyFakeServer
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.Json
import okhttp3.mockwebserver.MockResponse
import java.util.concurrent.TimeUnit
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Loading a surface, running its actions, and what each answer of docs/nest-ui.md Part 1 does. */
class NestScreenViewModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null
    private val entry = NestEntry(key = "habit-tracker/habits", packageId = "habit-tracker", surface = "habits", place = NestPlace.TAB, title = "Habits")
    private val dataPath = "/api/nest/extensions/habit-tracker/data?surface=habits"
    private val bodies = mutableListOf<String>()

    @BeforeTest fun main() { Dispatchers.setMain(Dispatchers.Default) }
    @AfterTest fun reset() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private fun vm(answer: (String, String, String) -> MockResponse?): NestScreenViewModel {
        val s = MyFakeServer { m, p, b -> if (m != "GET") bodies += b; answer(m, p, b) }.also { server = it }
        return vms.make { NestScreenViewModel(s.sessions(), entry) }
    }

    private fun nestForm(vararg pairs: Pair<String, String>): JsonObject = JsonObject(pairs.associate { (k, v) -> k to kotlinx.serialization.json.JsonPrimitive(v) })
    private fun gets() = server!!.requests.count { it == "GET $dataPath" }
    private fun posted(id: String) = server!!.requests.count { it == "POST /api/nest/extensions/habit-tracker/actions/$id" }
    private fun last(): JsonObject = Json.parseToJsonElement(bodies.last()).jsonObject

    @Test fun loadingReadsTheScreenAndKeepsTheNoticeOfDataThatFailed() {
        val vm = vm { m, p, _ -> if (m == "GET" && p == dataPath) ok(NestFixtures.HABITS) else null }
        vm.load()
        until("loaded") { vm.state.value.loaded && !vm.state.value.refreshing }
        val s = vm.state.value
        assertEquals("Habits", s.screen.title)
        assertEquals(4, s.screen.components.size)
        assertEquals(listOf("Connect Example Parcels first."), s.screen.errors)
        assertNull(s.error)
        assertFalse(s.gone)
    }

    @Test fun aSurfaceTheServerForbidsOrDoesNotKnowIsGone() {
        for (code in listOf(403, 404)) {
            val vm = vm { _, _, _ -> fail(code, "No longer shown") }
            vm.load()
            until("gone $code") { vm.state.value.loaded && !vm.state.value.refreshing }
            assertTrue(vm.state.value.gone, "$code")
            assertEquals("No longer shown", vm.state.value.error)
            server!!.shutdown()
        }
    }

    @Test fun tooManyRequestsShowTheServersWordsAndKeepWhatWasOnScreen() {
        var n = 0
        val vm = vm { m, p, _ -> if (m == "GET" && p == dataPath) { if (n++ == 0) ok(NestFixtures.HABITS) else fail(429, "Slow down, try again later") } else null }
        vm.load()
        until("first") { vm.state.value.screen.components.isNotEmpty() }
        vm.load()
        until("429") { vm.state.value.error != null }
        assertEquals("Slow down, try again later", vm.state.value.error)
        assertFalse(vm.state.value.gone)
        assertEquals(4, vm.state.value.screen.components.size)   // the screen stays
    }

    @Test fun aFormSubmitSendsTheFormAndReloadsWhenTheAnswerSaysRefresh() {
        val vm = vm { m, p, _ ->
            when {
                m == "GET" && p == dataPath -> ok(NestFixtures.HABITS)
                m == "POST" && p.endsWith("/actions/check_in") -> ok("""{"status":"done","message":"Logged. Keep it up!","refresh":true}""")
                else -> null
            }
        }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.run(NestAction("check_in", "store_add", false, "Check in"), nestForm("habit" to "Walk", "note" to "park"))
        until("reloaded") { gets() == 2 }
        assertEquals("Walk", last()["form"]!!.jsonObject["habit"]!!.jsonPrimitive.content)
        assertFalse(last().containsKey("item"))
        until("message") { vm.state.value.message == "Logged. Keep it up!" }
        until("not busy") { vm.state.value.busy.isEmpty() }
    }

    @Test fun aRowActionSendsOnlyTheRowsKeyAndDataId() {
        val vm = vm { m, p, _ -> if (m == "POST" && p.endsWith("/actions/remove")) ok("""{"status":"done","refresh":false}""") else null }
        vm.run(NestAction("remove", "store_delete", false, "Remove", item = "a1", data = "checkins"))
        until("posted") { posted("remove") == 1 }
        until("idle") { vm.state.value.busy.isEmpty() }
        val b = last()
        assertEquals("a1", b["item"]!!.jsonPrimitive.content)
        assertEquals("checkins", b["data"]!!.jsonPrimitive.content)
        assertFalse(b.containsKey("form"))
        assertEquals(0, gets())   // refresh was false
    }

    @Test fun anActionThatNeedsConfirmingAsksFirstAndThenSendsTheTokenOnly() {
        val vm = vm { m, p, b ->
            if (m == "POST" && p.endsWith("/actions/cancel")) {
                if ("tok-1" in b) ok("""{"status":"done","message":"Cancelled","refresh":false}""")
                else ok("""{"status":"confirm","token":"tok-1","title":"Cancel the parcel?","detail":"Goes to parcels.example","sends":{"id":"p1"},"expires_in":300}""")
            } else null
        }
        val a = NestAction("cancel", "connector", true, "Cancel", item = "p1", data = "parcels")
        vm.run(a)
        until("asked") { vm.state.value.confirm != null }
        val c = vm.state.value.confirm!!
        assertEquals("Cancel the parcel?", c.result.title)
        assertEquals(listOf("id" to "p1"), c.result.sends)
        assertEquals(1, posted("cancel"))
        vm.confirm()
        until("sent") { posted("cancel") == 2 }
        until("done") { vm.state.value.message == "Cancelled" }
        assertNull(vm.state.value.confirm)
        assertEquals(setOf("confirm"), last().keys)   // the token only, never the form or the row again
        assertEquals("tok-1", last()["confirm"]!!.jsonPrimitive.content)
    }

    @Test fun cancellingTheConfirmSendsNothingMore() {
        val vm = vm { m, p, _ -> if (m == "POST" && p.endsWith("/actions/cancel")) ok("""{"status":"confirm","token":"t","title":"Sure?","detail":"","sends":{},"expires_in":60}""") else null }
        vm.run(NestAction("cancel", "connector", true, "Cancel"))
        until("asked") { vm.state.value.confirm != null }
        vm.dismissConfirm()
        assertNull(vm.state.value.confirm)
        assertEquals(1, posted("cancel"))
    }

    @Test fun anExpiredConfirmationIsDroppedWithTheServersWords() {
        val vm = vm { m, p, b ->
            if (m == "POST" && p.endsWith("/actions/cancel")) {
                if ("tok" in b) fail(410, "That took too long, try again") else ok("""{"status":"confirm","token":"tok","title":"Sure?","detail":"","sends":{},"expires_in":1}""")
            } else null
        }
        vm.run(NestAction("cancel", "connector", true, "Cancel"))
        until("asked") { vm.state.value.confirm != null }
        vm.confirm()
        until("expired") { vm.state.value.message == "That took too long, try again" }
        assertNull(vm.state.value.confirm)
    }

    @Test fun anOpenAnswerGoesToTheSystemBrowserAndOnlyWebAddressesDo() {
        var url = "https://example.com/track"
        val vm = vm { m, p, _ -> if (m == "POST" && p.endsWith("/actions/open")) ok("""{"status":"open","url":"$url"}""") else null }
        vm.run(NestAction("open", "connector", false, "Open"))
        until("answered") { posted("open") == 1 && vm.state.value.busy.isEmpty() }
        assertEquals(NestEffect.OpenUrl("https://example.com/track"), vm.nextEffect())
        url = "javascript:alert(1)"
        vm.run(NestAction("open", "connector", false, "Open"))
        until("answered 2") { posted("open") == 2 && vm.state.value.busy.isEmpty() }
        assertNull(vm.nextEffect())
    }

    @Test fun anOpenUrlActionOpensItsOwnAddressWithoutAskingTheServer() {
        val vm = vm { _, _, _ -> null }
        vm.run(NestAction("track", "open_url", false, "Track", url = "https://example.com/track"))
        assertEquals(NestEffect.OpenUrl("https://example.com/track"), vm.nextEffect())
        vm.run(NestAction("track", "open_url", false, "Track", url = "file:///etc/passwd"))
        assertNull(vm.nextEffect())
        assertEquals(0, posted("track"))
    }

    @Test fun aStartedAnswerShowsItsMessageAndOffersTheChat() {
        val vm = vm { m, p, _ -> if (m == "POST" && p.endsWith("/actions/weekly")) ok("""{"status":"started","message":"Kelo is reviewing your week","turn_id":"turn-9"}""") else null }
        vm.run(NestAction("weekly", "agent", false, "Weekly review"))
        until("started") { vm.state.value.message == "Kelo is reviewing your week" }
        assertEquals(NestEffect.Started("Kelo is reviewing your week", "turn-9"), vm.nextEffect())
    }

    @Test fun aRefusedActionShowsTheServersWordsAndA403Or404ReadsTheScreenAgain() {
        for ((code, reads) in listOf(422 to 0, 409 to 0, 429 to 0, 502 to 0, 403 to 1, 404 to 1)) {
            val vm = vm { m, p, _ -> if (m == "POST") fail(code, "Words for $code") else if (p == dataPath) ok(NestFixtures.HABITS) else null }
            vm.run(NestAction("check_in", "store_add", false, "Check in"), nestForm("habit" to ""))
            until("refused $code") { vm.state.value.message == "Words for $code" }
            until("idle $code") { vm.state.value.busy.isEmpty() }
            if (reads > 0) until("reread $code") { gets() == reads } else Thread.sleep(50)
            assertEquals(reads, gets(), "$code")
            assertNull(vm.state.value.confirm)
            server!!.shutdown()
            bodies.clear()
        }
    }

    @Test fun aButtonIsNotSentTwiceWhileItsRequestRuns() {
        val vm = vm { m, p, _ ->
            if (m == "POST" && p.endsWith("/actions/slow")) MockResponse().setBody("""{"status":"done"}""").setBodyDelay(400, TimeUnit.MILLISECONDS) else null
        }
        val a = NestAction("slow", "store_add", false, "Slow")
        vm.run(a)
        assertTrue(a.key in vm.state.value.busy)
        vm.run(a)
        vm.run(a)
        until("done") { vm.state.value.busy.isEmpty() }
        assertEquals(1, posted("slow"))
        // another row of the same action is another button
        val b = a.copy(item = "r2", data = "d")
        assertFalse(b.key in vm.state.value.busy)
    }

    @Test fun nestChangedForThisPackageReloadsTheOpenScreenAndOthersDoNot() {
        val vm = vm { m, p, _ -> if (m == "GET" && p == dataPath) ok(NestFixtures.HABITS) else null }
        vm.load()
        until("loaded") { gets() == 1 && vm.state.value.loaded }
        NestLive.changed("another-package")
        Thread.sleep(150)
        assertEquals(1, gets())
        NestLive.changed("habit-tracker")
        until("reloaded") { gets() == 2 }
        assertNotNull(vm.state.value.screen)
    }

    @Test fun anActionWithoutAnIdIsIgnored() {
        val vm = vm { _, _, _ -> null }
        vm.run(NestAction("", "store_add"))
        assertTrue(vm.state.value.busy.isEmpty())
        assertEquals(0, server!!.requests.size)
    }
}
