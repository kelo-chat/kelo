package chat.kelo.app.my

import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import java.util.concurrent.ConcurrentHashMap
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** My → Nest → Install from a link: the rules, Look, Install (with the permissions the owner saw), the installed list, Update, Remove. */
class NestLinksTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null
    /** The body of the last call per "METHOD /path". */
    private val bodies = ConcurrentHashMap<String, String>()

    @BeforeTest fun main() { Dispatchers.setMain(Dispatchers.Default) }
    @AfterTest fun reset() { server?.shutdown(); vms.clear(); Dispatchers.resetMain() }

    private val card = """{"token":"tok-1","id":"reader","name":"Reader","description":"Reads things","kind":"skill","version":"1.0.0",
        "author":"me","repo":"me/reader","installed":null,
        "permissions":{"hash":"h-1","connect":"","words":[{"key":"net","text":"Reads the web","level":"caution"}]}}"""
    private val row = """{"id":"reader","version":"1.0.0","name":"Reader","description":"d","author":"me","repo":"me/reader","enabled":true,
        "pinned":false,"permission_words":[{"key":"net","text":"Reads the web","level":"caution"}],"update":null}"""
    private val rowsNoNewPerms = """[{"id":"reader","version":"1.0.0","name":"Reader","repo":"me/reader","enabled":true,"pinned":false,
        "permission_words":[],"update":{"version":"1.1.0","widened":[],"permissions_hash":"h-2","words":[],"pinned":false}}]"""
    private val rowsMorePerms = """[{"id":"reader","version":"1.0.0","name":"Reader","repo":"me/reader","enabled":true,"pinned":false,
        "permission_words":[],"update":{"version":"1.1.0","widened":["fs"],"permissions_hash":"h-2",
        "words":[{"key":"fs","text":"Writes files","level":"caution"}],"pinned":false}}]"""

    private fun vm(answer: (String, String, String) -> MockResponse?): NestLinksViewModel {
        val s = MyFakeServer { m, p, b -> bodies["$m $p"] = b; answer(m, p, b) }.also { server = it }
        return vms.make { NestLinksViewModel(s.sessions()) }
    }

    private fun posts(path: String) = server!!.requests.count { it == "POST $path" }
    private fun gets(path: String) = server!!.requests.count { it == "GET $path" }

    /** A string field of the body last sent to "METHOD /path". */
    private fun field(route: String, key: String): String? =
        bodies[route]?.let { Json.parseToJsonElement(it).jsonObject[key]?.jsonPrimitive?.content }

    @Test fun aPastedLinkIsCleanedAndOnlyAGitHubRepositoryLooksRight() {
        assertEquals("https://github.com/me/reader", NestLinkRules.clean("  github.com/me/reader.git "))
        assertEquals("https://github.com/me/reader", NestLinkRules.clean("https://github.com/me/reader"))
        assertTrue(NestLinkRules.looksRight("https://github.com/me/reader/tree/main"))
        assertFalse(NestLinkRules.looksRight("https://gitlab.com/me/reader"))
        assertFalse(NestLinkRules.looksRight("hello there"))
    }

    @Test fun aBadLinkIsRefusedBeforeAnyCall() {
        val vm = vm { _, _, _ -> null }
        vm.setLink("https://gitlab.com/me/reader")
        vm.check()
        assertEquals(NestLinkProblem.NOT_A_LINK, vm.state.value.problem)
        assertNull(vm.state.value.card)
        assertTrue(server!!.requests.isEmpty())
    }

    @Test fun lookSendsTheCleanedLinkAndShowsTheCard() {
        val vm = vm { m, p, _ -> if (m == "POST" && p == "/api/nest/install-from-link") ok(card) else null }
        vm.setLink("github.com/me/reader.git")
        vm.check()
        until("card") { vm.state.value.card != null && !vm.state.value.checking }
        assertEquals("https://github.com/me/reader", field("POST /api/nest/install-from-link", "url"))
        assertEquals("tok-1", vm.state.value.card?.token)
        assertEquals("h-1", vm.state.value.card?.permissions?.hash)
        assertEquals("caution", vm.state.value.card?.permissions?.words?.first()?.level)
        assertNull(vm.state.value.problem)
    }

    @Test fun aRepositoryWithoutAPackageSaysSo() {
        val vm = vm { m, _, _ -> if (m == "POST") fail(422, "not a package") else null }
        vm.setLink("https://github.com/me/notes")
        vm.check()
        until("problem") { vm.state.value.problem != null && !vm.state.value.checking }
        assertEquals(NestLinkProblem.NOT_A_PACKAGE, vm.state.value.problem)
        assertNull(vm.state.value.card)
    }

    @Test fun installPostsTheTokenAndTheHashTheOwnerSawThenReadsTheList() {
        var installed = false
        val vm = vm { m, p, _ ->
            when {
                m == "POST" && p == "/api/nest/install-from-link" -> ok(card)
                m == "POST" && p == "/api/nest/install-from-link/confirm" -> { installed = true; ok(row) }
                m == "GET" && p == "/api/nest/installed" -> ok(if (installed) "[$row]" else "[]")
                else -> null
            }
        }
        vm.setLink("github.com/me/reader")
        vm.check()
        until("card") { vm.state.value.card != null }
        vm.install()
        until("installed") { vm.state.value.installed.isNotEmpty() && !vm.state.value.installing }
        assertEquals("tok-1", field("POST /api/nest/install-from-link/confirm", "token"))
        assertEquals("h-1", field("POST /api/nest/install-from-link/confirm", "accept"))
        assertNull(vm.state.value.card)
        assertEquals("", vm.state.value.link)
        assertNull(vm.state.value.problem)
        assertEquals(listOf("reader"), vm.state.value.installed.map { it.id })
    }

    @Test fun aLinkThatTookTooLongIsExpiredAndNothingIsRead() {
        val vm = vm { m, p, _ ->
            when {
                m == "POST" && p == "/api/nest/install-from-link" -> ok(card)
                m == "POST" && p == "/api/nest/install-from-link/confirm" ->
                    MockResponse().setResponseCode(410).setBody("""{"detail":"expired","code":"link_expired"}""")
                else -> null
            }
        }
        vm.setLink("github.com/me/reader")
        vm.check()
        until("card") { vm.state.value.card != null }
        vm.install()
        until("problem") { vm.state.value.problem != null && !vm.state.value.installing }
        assertEquals(NestLinkProblem.EXPIRED, vm.state.value.problem)
        assertNull(vm.state.value.card)
        assertEquals(0, gets("/api/nest/installed"))
    }

    @Test fun aRepositoryThatChangedWhileLookingSaysSo() {
        val vm = vm { m, p, _ ->
            when {
                m == "POST" && p == "/api/nest/install-from-link" -> ok(card)
                m == "POST" && p == "/api/nest/install-from-link/confirm" ->
                    MockResponse().setResponseCode(409).setBody("""{"detail":"changed","code":"changed"}""")
                else -> null
            }
        }
        vm.setLink("github.com/me/reader")
        vm.check()
        until("card") { vm.state.value.card != null }
        vm.install()
        until("problem") { vm.state.value.problem != null && !vm.state.value.installing }
        assertEquals(NestLinkProblem.CHANGED, vm.state.value.problem)
    }

    @Test fun anUpdateThatAsksForNothingNewGoesStraightThrough() {
        val vm = vm { m, p, _ ->
            when {
                m == "GET" && p == "/api/nest/installed" -> ok(rowsNoNewPerms)
                m == "POST" && p == "/api/nest/packages/reader/update" -> ok("""{"id":"reader"}""")
                else -> null
            }
        }
        vm.loadInstalled()
        until("loaded") { vm.state.value.loaded }
        vm.update(vm.state.value.installed[0])
        until("updated") { posts("/api/nest/packages/reader/update") == 1 && vm.state.value.busy.isEmpty() }
        assertNull(field("POST /api/nest/packages/reader/update", "accept"))
        assertNull(vm.state.value.look)
    }

    @Test fun anUpdateThatAsksForMoreOpensTheLookAndPostsOnlyWhenAccepted() {
        val vm = vm { m, p, _ ->
            when {
                m == "GET" && p == "/api/nest/installed" -> ok(rowsMorePerms)
                m == "POST" && p == "/api/nest/packages/reader/update" -> ok("""{"id":"reader"}""")
                else -> null
            }
        }
        vm.loadInstalled()
        until("loaded") { vm.state.value.loaded }
        val row = vm.state.value.installed[0]
        vm.update(row)
        assertEquals("reader", vm.state.value.look?.id)
        assertEquals(0, posts("/api/nest/packages/reader/update"))
        vm.update(row, accepted = true)
        until("updated") { posts("/api/nest/packages/reader/update") == 1 && vm.state.value.busy.isEmpty() }
        assertEquals("h-2", field("POST /api/nest/packages/reader/update", "accept"))
        assertNull(vm.state.value.look)
    }

    @Test fun aLookAgainAnswerOpensTheLookInsteadOfFailing() {
        val vm = vm { m, p, _ ->
            when {
                m == "GET" && p == "/api/nest/installed" -> ok(rowsNoNewPerms)
                m == "POST" && p == "/api/nest/packages/reader/update" ->
                    MockResponse().setResponseCode(409).setBody("""{"detail":"look again","code":"look_again"}""")
                else -> null
            }
        }
        vm.loadInstalled()
        until("loaded") { vm.state.value.loaded }
        vm.update(vm.state.value.installed[0])
        until("look") { vm.state.value.look != null && vm.state.value.busy.isEmpty() }
        assertNull(vm.state.value.problem)
    }

    @Test fun removeDeletesThePackageThenReadsTheList() {
        val vm = vm { m, p, _ ->
            when {
                m == "DELETE" && p == "/api/nest/packages/reader" -> ok("""{"ok":true}""")
                m == "GET" && p == "/api/nest/installed" -> ok("[$row]")
                else -> null
            }
        }
        vm.loadInstalled()
        until("loaded") { vm.state.value.loaded }
        vm.remove(vm.state.value.installed[0])
        until("removed and read again") { server!!.saw("DELETE /api/nest/packages/reader") && gets("/api/nest/installed") == 2 && vm.state.value.busy.isEmpty() }
        assertNull(vm.state.value.problem)
    }
}
