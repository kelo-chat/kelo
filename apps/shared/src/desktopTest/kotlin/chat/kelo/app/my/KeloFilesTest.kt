package chat.kelo.app.my

import chat.kelo.app.core.KeloApi
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import chat.kelo.app.res.Res
import chat.kelo.app.res.files_core_reset_hint
import chat.kelo.app.res.files_saved
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
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

class KeloFileRulesTest {
    private fun f(p: String) = HomeEntry(p)
    private fun d(p: String) = HomeEntry(p, "dir")

    @Test fun theTopShowsFoldersThenCoreFilesInOrderThenTheRest() {
        val all = listOf(f("user.md"), f("zeta.md"), d("memory"), f("assistant.md"), f("memory/a.md"), d("notes"), f("Alpha.md"), f("MEMORY.md"))
        assertEquals(listOf("memory", "notes", "assistant.md", "user.md", "MEMORY.md", "Alpha.md", "zeta.md"), KeloFileRules.children(all, "").map { it.path })
    }

    @Test fun aFolderShowsOnlyItsDirectChildren() {
        val all = listOf(d("memory"), f("memory/b.md"), d("memory/people"), f("memory/people/x.md"), f("memory/a.md"), f("other/c.md"))
        assertEquals(listOf("memory/people", "memory/a.md", "memory/b.md"), KeloFileRules.children(all, "memory").map { it.path })
    }

    @Test fun deleteAndNewFolderRules() {
        assertTrue(KeloFileRules.canDelete("memory/a.md"))
        assertFalse(KeloFileRules.canDelete("memory"))
        assertFalse(KeloFileRules.canDelete("user.md"))
        assertFalse(KeloFileRules.canMakeFolder(""))
        assertTrue(KeloFileRules.canMakeFolder("notes"))
        assertTrue(KeloFileRules.canMakeFolder("notes/x"))
        assertFalse(KeloFileRules.canMakeFolder("uploads"))
    }

    @Test fun folderNames() {
        assertEquals("a", KeloFileRules.folderName(" a "))
        assertNull(KeloFileRules.folderName("a/b"))
        assertNull(KeloFileRules.folderName(".x"))
        assertNull(KeloFileRules.folderName(""))
    }

    @Test fun eachPartIsSegmentedAndTheSlashesStay() {
        assertEquals("memory/people/%EA%B9%80%20%EC%B2%A0%EC%88%98.md", KeloFileRules.apiPath("memory/people/김 철수.md") { KeloApi.segment(it) })
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class KeloFilesViewModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun main() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun stop() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    @Test fun savePutsTheDraftToTheFilesPath() {
        var put = ""
        val s = MyFakeServer { m, p, body ->
            when {
                m == "GET" && p == "/api/files/memory/a%20b.md" -> ok("""{"path":"memory/a b.md","content":"old"}""")
                m == "PUT" && p == "/api/files/memory/a%20b.md" -> { put = body; ok("""{"ok":true}""") }
                m == "GET" && p == "/api/files?folders=1" -> ok("[]")
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { KeloFilesViewModel(s.sessions()) }
        vm.openFile("memory/a b.md")
        until("opened") { vm.state.value.open != null }
        vm.edit(); vm.setDraft("new")
        vm.save()
        until("saved") { vm.state.value.note == Res.string.files_saved }
        assertTrue(put.contains("\"content\":\"new\""))
        assertEquals("new", vm.state.value.open?.content)
        assertNull(vm.state.value.draft)
    }

    @Test fun deletingACoreFileTellsToGoBackToTheStart() {
        val s = MyFakeServer { m, p, _ -> if (m == "DELETE" && p == "/api/files/user.md") fail(409, "core file") else null }.also { server = it }
        val vm = vms.make { KeloFilesViewModel(s.sessions()) }
        var went = false
        vm.delete("user.md") { went = true }
        until("note") { vm.state.value.note == Res.string.files_core_reset_hint }
        assertFalse(went)
    }

    private val skillsJson = """{"items":[
        {"id":"research","name":"Research","description":"Research a question.","source":"builtin","state":"ready"},
        {"id":"nest-one","name":"Nest one","description":"","source":"installed","state":"ready"},
        {"id":"tidy-inbox","name":"Tidy inbox","description":"Tidy the inbox.","source":"custom","state":"ready"},
        {"id":"a-held","name":"A held one","description":"Waiting.","source":"custom","state":"held"}]}"""

    @Test fun theSkillsRowNeedsACustomSkill() {
        var items = skillsJson
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p == "/api/files?folders=1" -> ok("[]")
                m == "GET" && p == "/api/skills" -> ok(items)
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { KeloFilesViewModel(s.sessions()) }
        vm.load()
        until("skills") { vm.state.value.skills.isNotEmpty() }
        assertEquals(listOf("A held one", "Tidy inbox"), vm.state.value.skills.map { it.title })   // by name; built-in and Nest-installed never
        assertTrue(vm.state.value.skills[0].held)
        items = """{"items":[{"id":"research","name":"Research","source":"builtin"}]}"""
        vm.load()
        until("hidden") { vm.state.value.skills.isEmpty() && vm.state.value.entries != null }
    }

    @Test fun aFailingSkillsCallLeavesTheFilesWorking() {
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p == "/api/files?folders=1" -> ok("""[{"path":"user.md","type":"file","bytes":3,"writable":true}]""")
                m == "GET" && p == "/api/skills" -> fail(500, "boom")
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { KeloFilesViewModel(s.sessions()) }
        vm.load()
        until("files") { vm.state.value.entries?.size == 1 }
        assertTrue(vm.state.value.skills.isEmpty())
        assertFalse(vm.state.value.failed)
    }

    @Test fun openingASkillReadsItsTextAndDeletingReloadsTheList() {
        var deleted = false
        var deletedHeld = false
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p == "/api/files?folders=1" -> ok("[]")
                m == "GET" && p == "/api/skills" -> ok(
                    if (deletedHeld) """{"items":[]}""" else if (deleted) """{"items":[{"id":"a-held","name":"A held one","source":"custom","state":"held"}]}""" else skillsJson,
                )
                m == "GET" && p == "/api/skills/custom/tidy-inbox" ->
                    ok("""{"id":"tidy-inbox","name":"Tidy inbox","description":"Tidy the inbox.","text":"## Steps\n1. Sort.","created_at":5,"state":"ready"}""")
                m == "DELETE" && p == "/api/skills/custom/tidy-inbox" -> { deleted = true; ok("""{"ok":true}""") }
                m == "DELETE" && p == "/api/skills/custom/a-held" -> { deletedHeld = true; ok("""{"ok":true}""") }
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { KeloFilesViewModel(s.sessions()) }
        vm.load()
        until("skills") { vm.state.value.skills.size == 2 }
        vm.openSkill("tidy-inbox")
        until("opened") { vm.state.value.skill != null }
        assertEquals("## Steps\n1. Sort.", vm.state.value.skill?.text)
        var last: Boolean? = null
        vm.deleteSkill("tidy-inbox") { last = it }
        until("deleted") { last != null }
        assertEquals(false, last)
        assertEquals(listOf("a-held"), vm.state.value.skills.map { it.id })
        assertNull(vm.state.value.skill)
        // the last one: the caller is told, and the row has nothing left to show
        vm.deleteSkill("a-held") { last = it }
        until("gone") { last == true }
        assertTrue(vm.state.value.skills.isEmpty())
    }
}
