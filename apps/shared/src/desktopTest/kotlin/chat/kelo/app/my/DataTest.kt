package chat.kelo.app.my

import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import chat.kelo.app.platform.FilePicker
import chat.kelo.app.platform.PickedFile
import chat.kelo.app.platform.PickedImage
import chat.kelo.app.platform.SaveTarget
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
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream

/** My > Your data: the export (make, wait, save), the import (pasted answer, ZIP, progress) and start over (the typed word, the saved copy). */
@OptIn(ExperimentalCoroutinesApi::class)
class DataTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun tearDown() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private class Picker(val zip: ByteArray? = null, val saved: ByteArrayOutputStream = ByteArrayOutputStream()) : FilePicker {
        var savedName: String? = null
        override suspend fun pickImage(maxBytes: Long): PickedImage? = null
        override suspend fun pickFile(types: List<String>): PickedFile? =
            zip?.let { z -> PickedFile("export.zip", z.size.toLong(), "application/zip") { ByteArrayInputStream(z) } }
        override suspend fun saveFile(name: String, type: String): SaveTarget? { savedName = name; return SaveTarget { saved } }
    }

    private fun overview(export: String = """{"state":"idle"}""", imp: String = """{"state":"idle"}""") =
        """{"confirm_word":"reset","export":$export,"import":$imp}"""

    private fun serve(answer: (String, String, String) -> okhttp3.mockwebserver.MockResponse?) = MyFakeServer(answer).also { server = it }

    @Test fun theRulesDecideWhatTheScreenOffers() {
        assertTrue(DataRules.exportReady(DataExport(state = "done", download = "/api/files/library/a.zip/file")))
        assertFalse(DataRules.exportReady(DataExport(state = "running")))
        assertFalse(DataRules.exportReady(DataExport(state = "done")))
        assertTrue(DataRules.typedWord("  RESET ", "reset"))
        assertFalse(DataRules.typedWord("rese", "reset"))
        assertFalse(DataRules.typedWord("", ""))
        assertEquals(0, DataRules.percent(DataImport(total = 0)))
        assertEquals(50, DataRules.percent(DataImport(total = 4, done = 2)))
        assertTrue(DataRules.importFinished(DataImport(state = "done")))
        assertFalse(DataRules.importFinished(DataImport(state = "running")))
        assertEquals("kelo-data.zip", DataRules.saveName(DataExport()))
    }

    @Test fun theOverviewLoadsWithThePromptAndTheWord() {
        val s = serve { m, p, _ ->
            when ("$m $p") {
                "GET /api/data" -> ok(overview())
                "GET /api/data/import/prompt" -> ok("""{"prompt":"List everything you remember about me"}""")
                else -> null
            }
        }
        val vm = vms.make { DataViewModel(s.sessions(), Picker(), poll = 10) }
        vm.load()
        until("the overview") { vm.state.value.overview != null }
        assertEquals("reset", vm.state.value.word)
        assertEquals("List everything you remember about me", vm.state.value.prompt)
    }

    @Test fun aFailedLoadSaysSo() {
        val s = serve { _, _, _ -> fail(500, "boom") }
        val vm = vms.make { DataViewModel(s.sessions(), Picker(), poll = 10) }
        vm.load()
        until("failed") { vm.state.value.failed }
        assertNull(vm.state.value.overview)
    }

    @Test fun theExportRunsThenItsFileIsSavedWithTheToken() {
        var polls = 0
        val file = "/api/files/library/kelo-data-export-1.zip/file"
        val s = serve { m, p, _ ->
            when ("$m $p") {
                "GET /api/data" -> ok(overview())
                "GET /api/data/import/prompt" -> ok("""{"prompt":"x"}""")
                "POST /api/data/export" -> ok("""{"state":"running"}""")
                "GET /api/data/export" -> ok(if (++polls < 2) """{"state":"running"}""" else """{"state":"done","file":"kelo-data-export-1.zip","download":"$file","bytes":2048}""")
                "GET /api/data/import" -> ok("""{"state":"idle"}""")
                "GET $file" -> ok("ZIPBYTES")
                else -> null
            }
        }
        val picker = Picker()
        val vm = vms.make { DataViewModel(s.sessions(), picker, poll = 10) }
        vm.load()
        until("loaded") { vm.state.value.overview != null }
        vm.startExport()
        until("running") { DataRules.running(vm.state.value.export.state) || DataRules.exportReady(vm.state.value.export) }
        until("done") { DataRules.exportReady(vm.state.value.export) }
        assertEquals(2048L, vm.state.value.export.bytes)
        vm.saveExport()
        until("saved") { picker.saved.size() > 0 }
        assertEquals("ZIPBYTES", picker.saved.toString())
        assertEquals("kelo-data-export-1.zip", picker.savedName)
        assertTrue(s.saw("GET $file"))
    }

    @Test fun aPastedAnswerIsSentAndTheProgressAndTheAddedFactsAreShown() {
        var polls = 0
        var sent = ""
        val s = serve { m, p, body ->
            when ("$m $p") {
                "GET /api/data" -> ok(overview())
                "GET /api/data/import/prompt" -> ok("""{"prompt":"x"}""")
                "POST /api/data/import/text" -> { sent = body; ok("""{"state":"running","total":2,"done":0}""") }
                "GET /api/data/export" -> ok("""{"state":"idle"}""")
                "GET /api/data/import" -> ok(if (++polls < 2) """{"state":"running","total":2,"done":1}"""
                    else """{"state":"done","assistant":"ChatGPT","total":2,"done":2,"added":[{"id":"c1","text":"Lives in Busan","kind":"fact"}]}""")
                else -> null
            }
        }
        val vm = vms.make { DataViewModel(s.sessions(), Picker(), poll = 10) }
        vm.load()
        until("loaded") { vm.state.value.overview != null }
        vm.paste("- Lives in Busan")
        vm.addPasted()
        until("sent") { sent.isNotEmpty() }
        assertTrue(sent.contains("Lives in Busan"))
        until("progress") { vm.state.value.import.state == "running" || DataRules.importFinished(vm.state.value.import) }
        until("done") { DataRules.importFinished(vm.state.value.import) }
        assertEquals(listOf("Lives in Busan"), vm.state.value.import.added.map { it.text })
        assertEquals("", vm.state.value.pasted)
    }

    @Test fun aZipGoesUpAsTheRawBodyAndAServerRefusalIsShown() {
        var body = ""
        val s = serve { m, p, b ->
            when ("$m $p") {
                "GET /api/data" -> ok(overview())
                "GET /api/data/import/prompt" -> ok("""{"prompt":"x"}""")
                "PUT /api/data/import/file" -> { body = b; fail(422, "This ZIP holds no ChatGPT, Claude or Gemini chats that Kelo can read.") }
                else -> null
            }
        }
        val vm = vms.make { DataViewModel(s.sessions(), Picker(zip = "PK-fake-zip".toByteArray()), poll = 10) }
        vm.load()
        until("loaded") { vm.state.value.overview != null }
        vm.pickZip()
        until("refused") { vm.state.value.import.state == "failed" }
        assertEquals("PK-fake-zip", body)
        assertTrue(vm.state.value.import.error.contains("ChatGPT, Claude or Gemini"))
    }

    @Test fun startOverNeedsTheWordSendsItAndReportsTheCopy() {
        var sent = ""
        val s = serve { m, p, b ->
            when ("$m $p") {
                "GET /api/data" -> ok(overview())
                "GET /api/data/import/prompt" -> ok("""{"prompt":"x"}""")
                "POST /api/data/reset" -> { sent = b; ok("""{"ok":true,"backup":{"name":"kelo-backup-before-reset-1.zip","path":"files/kelo-backup-before-reset-1.zip","bytes":99},"signed_out":1}""") }
                else -> null
            }
        }
        val vm = vms.make { DataViewModel(s.sessions(), Picker(), poll = 10) }
        vm.load()
        until("loaded") { vm.state.value.overview != null }
        vm.typed("rese")
        vm.reset()
        assertEquals("", sent)                                   // the word is not right: nothing is sent
        vm.typed("Reset")
        vm.signOut(true)
        vm.reset()
        until("done") { vm.state.value.resetDone != null }
        assertTrue(sent.contains("\"confirm\":\"Reset\"") && sent.contains("\"sign_out_connectors\":true"))
        assertEquals("kelo-backup-before-reset-1.zip", vm.state.value.resetDone!!.backup.name)
        assertEquals("", vm.state.value.typed)
        assertFalse(vm.state.value.signOut)
    }

    @Test fun aWrongWordAnswerKeepsThePageAndSaysSo() {
        val s = serve { m, p, _ ->
            when ("$m $p") {
                "GET /api/data" -> ok(overview())
                "GET /api/data/import/prompt" -> ok("""{"prompt":"x"}""")
                "POST /api/data/reset" -> fail(400, "type the confirmation word to start over")
                else -> null
            }
        }
        val vm = vms.make { DataViewModel(s.sessions(), Picker(), poll = 10) }
        vm.load()
        until("loaded") { vm.state.value.overview != null }
        vm.typed("reset")
        vm.reset()
        until("said") { vm.state.value.wrongWord }
        assertNull(vm.state.value.resetDone)
        assertEquals("reset", vm.state.value.typed)
    }
}
