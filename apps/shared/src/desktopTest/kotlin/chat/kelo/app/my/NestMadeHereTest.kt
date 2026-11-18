package chat.kelo.app.my

import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import chat.kelo.app.shell.NestLive
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.MockResponse
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** My → Nest → Made here: the list, Share (202 / 409), Delete, and the live reload. */
class NestMadeHereTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @BeforeTest fun main() { Dispatchers.setMain(Dispatchers.Default) }
    @AfterTest fun reset() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private val drafts = """{"items":[
        {"id":"old-one","kind":"skill","name":"Old one","version":"0.0.2","updated_at":100.0,"shared":null},
        {"id":"reading","kind":"extension","name":"Reading tab","version":"0.0.5","updated_at":300.0,"shared":{"repo":"me/kelo-reading","commit":"${"a".repeat(40)}","at":250}},
        {"id":"writer","kind":"skill","name":"","version":"0.0.1","updated_at":200.0,"shared":null}]}"""
    private val skills = """{"items":[
        {"id":"mine","name":"My review skill","description":"d","source":"custom","state":"ready","created_at":5.0},
        {"id":"writer","name":"Writer","description":"d","source":"custom","state":"ready","created_at":4.0},
        {"id":"builtin","name":"Built in","description":"d","source":"builtin","state":"ready"}]}"""

    private fun vm(answer: (String, String, String) -> MockResponse?): MadeHereViewModel {
        val s = MyFakeServer(answer).also { server = it }
        return vms.make { MadeHereViewModel(s.sessions()) }
    }

    private fun list(m: String, p: String): MockResponse? = when {
        m == "GET" && p == "/api/nest/drafts" -> ok(drafts)
        m == "GET" && p == "/api/skills" -> ok(skills)
        else -> null
    }

    private fun posts(path: String) = server!!.requests.count { it == "POST $path" }

    @Test fun theListHasDraftsNewestFirstThenCustomSkillsThatAreNotDraftsYet() {
        val vm = vm { m, p, _ -> list(m, p) }
        vm.load()
        until("loaded") { vm.state.value.loaded }
        val items = vm.state.value.items
        assertEquals(listOf("reading", "writer", "old-one", "mine"), items.map { it.id })
        val reading = items[0]
        assertEquals(MadeKind.EXTENSION, reading.kind)
        assertEquals("0.0.5", reading.version)
        assertTrue(reading.draft)
        assertTrue(reading.shared)
        assertEquals("https://github.com/me/kelo-reading", reading.repoUrl)
        assertEquals("writer", items[1].title)   // no name: the id
        assertFalse(items[2].shared)
        assertNull(items[2].repoUrl)
        val mine = items[3]
        assertEquals(MadeKind.CUSTOM_SKILL, mine.kind)
        assertFalse(mine.draft)
        assertFalse(vm.state.value.failed)
    }

    @Test fun anOlderServerWithoutDraftsStillShowsItsCustomSkills() {
        val vm = vm { m, p, _ -> if (p == "/api/nest/drafts") fail(404, "none") else list(m, p) }
        vm.load()
        until("loaded") { vm.state.value.loaded }
        assertEquals(listOf("mine", "writer"), vm.state.value.items.map { it.id })
        assertFalse(vm.state.value.failed)
    }

    @Test fun nothingReadableMarksTheListFailed() {
        val vm = vm { _, _, _ -> fail(500, "boom") }
        vm.load()
        until("loaded") { vm.state.value.loaded }
        assertTrue(vm.state.value.failed)
        assertTrue(vm.state.value.items.isEmpty())
    }

    @Test fun aRepositoryNameThatIsNotOwnerSlashNameGivesNoLink() {
        assertNull(MadeHereRules.repoUrl("javascript:alert(1)"))
        assertNull(MadeHereRules.repoUrl("https://evil.example/x/y"))
        assertNull(MadeHereRules.repoUrl("a b/c"))
        assertEquals("https://github.com/o/n.x", MadeHereRules.repoUrl("o/n.x"))
    }

    @Test fun share202OpensTheChatOnThatTurn() {
        val vm = vm { m, p, _ -> if (m == "POST" && p == "/api/nest/drafts/reading/share") MockResponse().setResponseCode(202).setBody("""{"turn_id":"turn-7"}""") else list(m, p) }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.share(vm.state.value.items.first { it.id == "reading" })
        until("shared") { posts("/api/nest/drafts/reading/share") == 1 && vm.state.value.busy.isEmpty() }
        assertEquals(MadeEffect.OpenChat("turn-7"), vm.nextEffect())
        assertNull(vm.state.value.connectFor)
    }

    @Test fun share409WithoutGithubWriteAccessOffersToConnectAndThenSharesAgain() {
        var connected = false
        val vm = vm { m, p, _ ->
            if (m == "POST" && p == "/api/nest/drafts/reading/share") {
                if (connected) MockResponse().setResponseCode(202).setBody("""{"turn_id":"turn-8"}""")
                else MockResponse().setResponseCode(409).setBody("""{"code":"github_write_needed","detail":"github write access needed"}""")
            } else list(m, p)
        }
        vm.load(); until("loaded") { vm.state.value.loaded }
        val item = vm.state.value.items.first { it.id == "reading" }
        vm.share(item)
        until("sheet") { vm.state.value.connectFor == item }
        assertNull(vm.nextEffect())
        assertNull(vm.state.value.message)
        // GitHub gets connected (the existing connector flow), the sheet's button shares again
        connected = true
        vm.retryShare()
        until("shared again") { posts("/api/nest/drafts/reading/share") == 2 && vm.state.value.busy.isEmpty() }
        assertEquals(MadeEffect.OpenChat("turn-8"), vm.nextEffect())
        assertNull(vm.state.value.connectFor)
    }

    @Test fun aSheetCanBeDismissedWithoutSharing() {
        val vm = vm { m, p, _ -> if (m == "POST") MockResponse().setResponseCode(409).setBody("""{"code":"github_write_needed","detail":"x"}""") else list(m, p) }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.share(vm.state.value.items.first())
        until("sheet") { vm.state.value.connectFor != null }
        vm.dismissConnect()
        assertNull(vm.state.value.connectFor)
        vm.retryShare()
        assertEquals(1, posts("/api/nest/drafts/reading/share"))
    }

    @Test fun anOtherFailureOfShareSaysWhy() {
        val vm = vm { m, p, _ -> if (m == "POST") fail(404, "draft 'reading' not found") else list(m, p) }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.share(vm.state.value.items.first())
        until("message") { vm.state.value.message == "draft 'reading' not found" }
        assertNull(vm.state.value.connectFor)
        assertNull(vm.nextEffect())
    }

    @Test fun sharingACustomSkillFirstMakesItADraftThenSharesIt() {
        val vm = vm { m, p, _ ->
            when {
                m == "POST" && p == "/api/nest/drafts/from-skill/mine" -> ok("""{"ok":true,"id":"mine","kind":"skill","version":"0.0.1"}""")
                m == "POST" && p == "/api/nest/drafts/mine/share" -> MockResponse().setResponseCode(202).setBody("""{"turn_id":"turn-3"}""")
                else -> list(m, p)
            }
        }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.share(vm.state.value.items.first { it.id == "mine" })
        until("shared") { posts("/api/nest/drafts/mine/share") == 1 && vm.state.value.busy.isEmpty() }
        assertEquals(1, posts("/api/nest/drafts/from-skill/mine"))
        val order = server!!.requests.filter { it.startsWith("POST") }
        assertEquals(listOf("POST /api/nest/drafts/from-skill/mine", "POST /api/nest/drafts/mine/share"), order)
        assertEquals(MadeEffect.OpenChat("turn-3"), vm.nextEffect())
    }

    @Test fun aSkillAlreadyCopiedToADraftIsStillShared() {
        val vm = vm { m, p, _ ->
            when {
                m == "POST" && p == "/api/nest/drafts/from-skill/mine" -> fail(409, "Draft 'mine' already exists")
                m == "POST" && p == "/api/nest/drafts/mine/share" -> MockResponse().setResponseCode(202).setBody("""{"turn_id":"turn-4"}""")
                else -> list(m, p)
            }
        }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.share(vm.state.value.items.first { it.id == "mine" })
        until("shared") { vm.state.value.busy.isEmpty() && vm.nextEffect() == MadeEffect.OpenChat("turn-4") }
    }

    @Test fun deletingADraftCallsItsEndpointAndTheListLosesIt() {
        var gone = false
        val vm = vm { m, p, _ ->
            when {
                m == "DELETE" && p == "/api/nest/drafts/old-one" -> { gone = true; ok("""{"ok":true}""") }
                m == "GET" && p == "/api/nest/drafts" -> ok(if (gone) drafts.replace(Regex("""\{"id":"old-one".*?"shared":null\},"""), "") else drafts)
                else -> list(m, p)
            }
        }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.delete(vm.state.value.items.first { it.id == "old-one" })
        until("deleted") { vm.state.value.items.none { it.id == "old-one" } && vm.state.value.busy.isEmpty() }
        assertTrue(server!!.saw("DELETE /api/nest/drafts/old-one"))
        assertEquals(listOf("reading", "writer", "mine"), vm.state.value.items.map { it.id })
    }

    @Test fun deletingACustomSkillCallsTheSkillsEndpoint() {
        val vm = vm { m, p, _ -> if (m == "DELETE" && p == "/api/skills/custom/mine") ok("""{"ok":true}""") else list(m, p) }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.delete(vm.state.value.items.first { it.id == "mine" })
        until("deleted") { server!!.saw("DELETE /api/skills/custom/mine") && vm.state.value.busy.isEmpty() }
    }

    @Test fun aRefusedDeleteSaysWhyAndKeepsTheItem() {
        val vm = vm { m, p, _ -> if (m == "DELETE") fail(403, "Only the owner") else list(m, p) }
        vm.load(); until("loaded") { vm.state.value.loaded }
        vm.delete(vm.state.value.items.first { it.id == "old-one" })
        until("message") { vm.state.value.message == "Only the owner" }
        until("idle") { vm.state.value.busy.isEmpty() }
        assertTrue(vm.state.value.items.any { it.id == "old-one" })
    }

    @Test fun nestChangedReloadsTheList() {
        var n = 0
        val vm = vm { m, p, _ -> if (m == "GET" && p == "/api/nest/drafts") { n++; ok(drafts) } else list(m, p) }
        vm.load(); until("loaded") { vm.state.value.loaded && n == 1 }
        NestLive.changed("reading")
        until("reloaded") { n == 2 }
    }
}
