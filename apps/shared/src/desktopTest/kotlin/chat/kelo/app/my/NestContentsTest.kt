package chat.kelo.app.my

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.width
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import chat.kelo.app.TestViewModels
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.until
import chat.kelo.app.ui.theme.KeloTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import kotlin.test.AfterTest
import kotlin.test.BeforeTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/** What a package installs (My → Nest): the card's contents, an update's changed files, the size labels, and the server's refusal words. */
@OptIn(ExperimentalTestApi::class)
class NestContentsTest {
    private val json = Json { ignoreUnknownKeys = true }
    private val vms = TestViewModels()
    private var server: MyFakeServer? = null

    @BeforeTest fun main() { Dispatchers.setMain(Dispatchers.Default) }
    @AfterTest fun reset() { server?.shutdown(); vms.clear(); Dispatchers.resetMain() }

    private val card = """{"token":"tok-1","id":"reader","name":"Reader","version":"1.0.0","repo":"me/reader",
        "permissions":{"hash":"h-1","words":[]},
        "contents":{"files":[{"path":"skill.md","bytes":2048,"sha256":"aa"},{"path":"img/big.png","bytes":1025,"sha256":"bb"}],
        "total_bytes":3073,"texts":{"skill.md":"# Reader\n…","manifest.json":"{}"},"cut":["skill.md"],
        "endpoints":["api.example.com","https://example.com/very/long/path/segment/with/no/spaces/anywhere/at/all/index.html"],
        "warnings":["Uses an old manifest version"]}}"""

    private val update = """{"version":"1.1.0","widened":[],"permissions_hash":"h-2","words":[],"pinned":false,
        "changed":{"added":["a.md"],"removed":["b.md","c.md"],"modified":["d.md","e.md","f.md"]}}"""

    @Test fun aCardCarriesItsContents() {
        val c = json.decodeFromString<NestLinkCard>(card).contents
        assertEquals(listOf("skill.md", "img/big.png"), c.files.map { it.path })
        assertEquals(3073L, c.totalBytes)
        assertEquals(setOf("skill.md", "manifest.json"), c.texts.keys)
        assertEquals(listOf("skill.md"), c.cut)
        assertEquals(2, c.endpoints.size)
        assertEquals(listOf("Uses an old manifest version"), c.warnings)
        assertTrue(NestContentsRules.hasContents(c))
        assertTrue(NestContentsRules.isCut(c, "skill.md"))
        assertFalse(NestContentsRules.isCut(c, "manifest.json"))
    }

    @Test fun anOlderServersCardHasNoContentsRow() {
        val old = json.decodeFromString<NestLinkCard>("""{"token":"t","id":"reader","permissions":{"hash":"h"}}""")
        assertFalse(NestContentsRules.hasContents(old.contents))
        assertEquals(0, old.contents.files.size)
    }

    @Test fun anUpdateCountsAddedRemovedAndModifiedFiles() {
        val u = json.decodeFromString<NestUpdate>(update)
        assertEquals(6, NestContentsRules.changedCount(u.changed))
        assertEquals(listOf("c.md"), u.changed.removed.drop(1))
        assertEquals(0, NestContentsRules.changedCount(json.decodeFromString<NestUpdate>("""{"version":"1.0.1"}""").changed))
    }

    @Test fun sizesAreBytesThenWholeKilobytesRoundedUp() {
        assertEquals(NestSize.Bytes(0), NestContentsRules.size(0))
        assertEquals(NestSize.Bytes(1023), NestContentsRules.size(1023))
        assertEquals(NestSize.Kilobytes(1), NestContentsRules.size(1024))
        assertEquals(NestSize.Kilobytes(2), NestContentsRules.size(1025))
        assertEquals(NestSize.Kilobytes(2), NestContentsRules.size(2048))
    }

    @Test fun aRefusalShowsTheServersWords() {
        server = MyFakeServer { m, _, _ -> if (m == "POST") fail(422, "This package has fields Kelo doesn't know: color") else null }
        val vm = vms.make { NestLinksViewModel(server!!.sessions()) }
        vm.setLink("https://github.com/me/reader")
        vm.check()
        until("refusal") { vm.state.value.problem != null && !vm.state.value.checking }
        assertEquals(NestLinkProblem.NOT_A_PACKAGE, vm.state.value.problem)
        assertEquals("This package has fields Kelo doesn't know: color", vm.state.value.reason)
    }

    /** A long spaceless path and endpoint wrap inside a 320 dp or a 412 dp column (no sheet, no page scroll). */
    private fun wrapsIn(width: Int) = runComposeUiTest {
        val long = "https://example.com/very/long/path/segment/with/no/spaces/anywhere/at/all/index.html"
        val contents = json.decodeFromString<NestLinkCard>(card).contents
        setContent {
            KeloTheme(dark = false) {
                Column(Modifier.width(width.dp)) { NestContentsBody(contents) }
            }
        }
        onNodeWithText(long).assertIsDisplayed()
    }

    @Test fun aLongPathWrapsIn320dp() = wrapsIn(320)
    @Test fun aLongPathWrapsIn412dp() = wrapsIn(412)
}
