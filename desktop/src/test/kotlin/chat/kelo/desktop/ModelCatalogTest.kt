package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.ModelFacts
import chat.kelo.app.chat.ModelList
import chat.kelo.app.chat.ModelNames
import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.Profile
import chat.kelo.app.core.ScreenCache
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Session
import chat.kelo.app.core.Sessions
import chat.kelo.app.my.CliStatus
import chat.kelo.app.my.ProviderDetail
import chat.kelo.desktop.chat.ModelCatalog
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.DesktopCaches
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.i18n.Strings
import chat.kelo.desktop.pages.my.ModelPage
import chat.kelo.desktop.ui.KeloFrame
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executor
import javax.crypto.KeyGenerator

/**
 * The kept provider and model lists on this computer (chat.kelo.desktop.chat.ModelCatalog, the phone's ModelCatalog with
 * DesktopCaches) and the one way of writing a model's name the server also uses (the shared ModelNames.humanize).
 */
class ModelCatalogTest {
    @get:Rule val tmp = TemporaryFolder()

    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    private val now = Executor { it.run() }

    private class MapStore : KeyValueStore {
        val map = HashMap<String, String>()
        override fun getString(key: String) = map[key]
        override fun putString(key: String, value: String) { map[key] = value }
        override fun remove(key: String) { map.remove(key) }
    }

    /** A new launch: a fresh cache instance on the same folder, so everything is read back from the disk. */
    private fun sessions(server: String, dir: File = tmp.root): Sessions =
        Sessions(SecureStore(MapStore(), AesGcmCipher { key }), OkHttpClient(), ScreenCache(dir, AesGcmCipher { key }, now), requireE2e = false)
            .apply { signIn(Session(server, "tok-0123456789abcdef")) }

    private val agy = ModelList(
        "antigravity-oauth", listOf("gemini-3.8-flash-medium", "claude-sonnet-4-6"),
        labels = mapOf("gemini-3.8-flash-medium" to "Gemini 3.8 Flash (Medium)", "claude-sonnet-4-6" to "Claude Sonnet 4.6 (Thinking)"),
        info = mapOf("claude-sonnet-4-6" to ModelFacts(images = true)), fetchedAt = 1_700_000_000,
    )

    // ---- the shared label rule

    /** tests/fixtures/model_labels.json at the repo root: the same cases the server's and the phone's tests check. */
    private fun fixture(): File {
        var dir: File? = File("").absoluteFile
        while (dir != null) {
            val f = File(dir, "tests/fixtures/model_labels.json")
            if (f.isFile) return f
            dir = dir.parentFile
        }
        fail("tests/fixtures/model_labels.json not found above ${File("").absolutePath}")
        throw IllegalStateException()
    }

    @Test fun namesAreWrittenExactlyAsTheServerWritesThem() {
        val cases = Json.parseToJsonElement(fixture().readText()).jsonObject.getValue("cases").jsonArray
        assertTrue(cases.size >= 20)
        for (c in cases) {
            val (id, want) = c.jsonArray.map { it.jsonPrimitive.content }
            assertEquals("for $id", want, ModelNames.humanize(id))
        }
    }

    @Test fun aDerivedNameAndTheServersLabelNeverDifferInPunctuation() {
        // the CLIs' own labels (agy models, the claude CLI's names) for ids whose words say it all
        mapOf(
            "gemini-3.8-flash-medium" to "Gemini 3.8 Flash (Medium)", "gemini-3.8-flash-high" to "Gemini 3.8 Flash (High)",
            "claude-opus-4-6-thinking" to "Claude Opus 4.6 (Thinking)", "gpt-oss-120b-medium" to "GPT-OSS 120B (Medium)",
            "sonnet" to "Sonnet (latest)", "claude-opus-5-5" to "Claude Opus 5.5",
            "gpt-4o" to "GPT-4o", "gpt-4o-2024-08-06" to "GPT-4o", "gpt-oss-120b" to "GPT-OSS 120B", "gpt-5.1-mini" to "GPT-5.1 Mini",
        ).forEach { (id, label) ->
            assertEquals(label, ModelNames.display(id, null))
            assertEquals(ModelNames.short(label), ModelNames.short(ModelNames.display(id, null)))   // the composer chip's form too
        }
        // a dated snapshot next to the plain id in one list keeps its date, so the two still read differently
        assertEquals("GPT-4o (2024-08-06)", ModelNames.displayAll(listOf("gpt-4o", "gpt-4o-2024-08-06"), emptyMap())["gpt-4o-2024-08-06"])
    }

    // ---- what a screen shows

    @Test fun neverBlank() {
        val failed = ModelList(agy.provider, emptyList(), error = "could not")
        assertEquals(agy, ModelCatalog.shown(null, agy))                      // not answered yet: the kept list
        assertEquals(agy.copy(stale = true), ModelCatalog.shown(failed, agy))  // failed: the kept list, marked stale
        assertEquals(failed, ModelCatalog.shown(failed, null))                // nothing kept: the reason
        val fresh = agy.copy(models = agy.models + "gemini-3.8-flash-high")
        assertEquals(fresh, ModelCatalog.shown(fresh, agy))                   // a good answer replaces it in place
        assertNull(ModelCatalog.shown(null, null))
        // nothing known at all: the chosen model by name (a select row), never the raw id in a text field
        assertEquals(listOf("sonnet"), ModelCatalog.placeholder("claude-cli", "sonnet")!!.models)
        assertEquals("Sonnet (latest)", ModelCatalog.name("sonnet", null, null))
        assertNull(ModelCatalog.placeholder("claude-cli", ""))
        assertNull(ModelCatalog.placeholder("", "sonnet"))
        assertEquals(1000L, ModelCatalog.SLOW_MS)
    }

    @Test fun theLabelComesFromTheFreshListThenTheKeptOneThenTheRule() {
        assertEquals("Claude Sonnet 4.6 (Thinking)", ModelCatalog.name("claude-sonnet-4-6", null, agy))
        assertEquals("Claude Sonnet 4.6 (Thinking)", ModelCatalog.name("claude-sonnet-4-6", agy.copy(labels = emptyMap()), agy))
        assertEquals("Claude Sonnet 4.6", ModelCatalog.name("claude-sonnet-4-6", null, null))
    }

    @Test fun onlyGoodListsAreKeptNewestFirstAndCapped() {
        var kept = emptyMap<String, ModelList>()
        kept = ModelCatalog.merge(kept, agy)
        kept = ModelCatalog.merge(kept, ModelList(agy.provider, emptyList(), error = "down"))   // a failure changes nothing
        assertEquals(agy, kept[agy.provider])
        kept = ModelCatalog.merge(kept, ModelList("openai", emptyList()))                        // an empty list is not kept
        assertFalse("openai" in kept)
        repeat(ModelCatalog.MAX_PROVIDERS + 4) { i -> kept = ModelCatalog.merge(kept, ModelList("p$i", listOf("m"))) }
        assertEquals(ModelCatalog.MAX_PROVIDERS, kept.size)
        assertEquals("p${ModelCatalog.MAX_PROVIDERS + 3}", kept.keys.first())
    }

    // ---- kept on this computer

    @Test fun listsAndProvidersSurviveARestartPerServer() {
        val a = "https://a.example.com"
        val first = sessions(a)
        ModelCatalog.keepList(first, first.requireApi(), agy.copy(stale = true))
        ModelCatalog.keepList(first, first.requireApi(), ModelList("openai", emptyList(), error = "no key"))
        ModelCatalog.keepList(first, first.requireApi(), ModelList(agy.provider, emptyList(), error = "agy models timed out"))   // never replaces it
        val rows = listOf(ProviderDetail("antigravity-oauth", "Antigravity", configured = true, cli = CliStatus(installed = true, loggedIn = true)))
        first.keep(DesktopCaches.PROVIDER_DETAILS, rows)

        val again = sessions(a)   // the next launch
        assertEquals(agy, ModelCatalog.keptList(again, agy.provider))   // with its labels and facts, without the stale mark
        assertNull(ModelCatalog.keptList(again, "openai"))
        assertEquals(rows, ModelCatalog.keptProviders(again))
        assertEquals(listOf("antigravity-oauth"), ModelCatalog.keptProviderInfos(again).map { it.name })

        val other = sessions("https://b.example.com")   // another server never sees them
        assertNull(ModelCatalog.keptList(other, agy.provider))
        assertTrue(ModelCatalog.keptProviders(other).isEmpty())
        assertTrue(ModelCatalog.keptLists(other).isEmpty())

        again.signOut()   // signing out forgets them, on disk too
        assertNull(ModelCatalog.keptList(sessions(a), agy.provider))
        assertTrue(ModelCatalog.keptProviders(sessions(a)).isEmpty())
    }

    /** The app's own storage (AppContainer: the cache folder in its data folder, sealed with its key) reads them back on a new run. */
    @Test fun theAppKeepsThemInItsDataFolder() {
        val dir = tmp.newFolder()
        fun app() = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        val first = app()
        first.sessions.signIn(Session("https://a.example.com", "tok-0123456789abcdef"))
        ModelCatalog.keepList(first.sessions, first.sessions.requireApi(), agy)
        first.sessions.keep(DesktopCaches.PROVIDER_DETAILS, listOf(ProviderDetail("antigravity-oauth", "Antigravity", configured = true)))
        // the disk write runs on the cache's own thread: a later run reads it once it is there
        val end = System.currentTimeMillis() + 10_000
        var next = app()
        while ((ModelCatalog.keptList(next.sessions, agy.provider) == null || ModelCatalog.keptProviders(next.sessions).isEmpty()) &&
            System.currentTimeMillis() < end) {
            Thread.sleep(50)
            next = app()
        }
        assertEquals(agy, ModelCatalog.keptList(next.sessions, agy.provider))
        assertEquals("Antigravity", ModelCatalog.keptProviders(next.sessions).single().displayName)
    }

    @Test fun fetchKeepsAGoodAnswerAndAFailureKeepsTheLastGoodOne() = runBlocking {
        val server = MockWebServer()
        server.start()
        try {
            val base = server.url("/").toString().trimEnd('/')
            val s = sessions(base)
            server.enqueue(MockResponse().setBody(
                """{"provider":"antigravity-oauth","models":["gemini-3.8-flash-medium"],"labels":{"gemini-3.8-flash-medium":"Gemini 3.8 Flash (Medium)"},"info":{},"error":null,"stale":false,"fetched_at":1}""",
            ))
            val got = ModelCatalog.fetch(s, "antigravity-oauth")
            assertEquals("Gemini 3.8 Flash (Medium)", got.labels["gemini-3.8-flash-medium"])
            assertTrue(server.takeRequest().path!!.startsWith("/api/models?provider=antigravity-oauth"))
            server.enqueue(MockResponse().setBody("""{"provider":"antigravity-oauth","models":[],"labels":{},"info":{},"error":"agy models timed out"}"""))
            val failed = ModelCatalog.fetch(s, "antigravity-oauth")
            assertNotNull(failed.error)
            val kept = ModelCatalog.keptList(sessions(base), "antigravity-oauth")
            assertEquals(listOf("gemini-3.8-flash-medium"), kept!!.models)
            assertEquals("Gemini 3.8 Flash (Medium)", ModelCatalog.shown(failed, kept)!!.labels["gemini-3.8-flash-medium"])

            server.enqueue(MockResponse().setBody("""[{"name":"antigravity-oauth","display_name":"Antigravity","configured":true,"cli":{"installed":true,"logged_in":true,"checked_at":1700000000.5}}]"""))
            val rows = ModelCatalog.fetchProviders(s)
            assertEquals("Antigravity", rows.single().displayName)
            val next = sessions(base)
            assertEquals(rows, ModelCatalog.keptProviders(next))
            assertEquals("Antigravity", ModelCatalog.keptProviderInfos(next).single().displayName)
        } finally {
            server.shutdown()
        }
    }
}

/**
 * The model settings (마이 → 모델) against a server that never answers: the provider list and the model's name kept from the last
 * time show at once, no text field holds the raw id, and the refresh link says "loading" only once a second has passed.
 */
class ModelSettingsKeptTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val en = Strings.of("en")
    private val silent = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    private val held = CopyOnWriteArrayList<Socket>()

    init {
        // accepts every request and never answers (closed in stop())
        Thread {
            while (!silent.isClosed) runCatching { held += silent.accept() }
        }.apply { isDaemon = true }.start()
    }

    @After fun stop() {
        runCatching { silent.close() }
        held.forEach { runCatching { it.close() } }
    }

    @Test fun theKeptProvidersAndTheModelsNameShowBeforeTheServerAnswers() {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("en")
        app.applyLanguage()
        app.settings.setTheme("light")
        app.sessions.signIn(Session("http://127.0.0.1:${silent.localPort}", "tok-0123456789abcdef"))
        // what an earlier run kept for this server
        app.sessions.keep(DesktopCaches.PROVIDER_DETAILS, listOf(
            ProviderDetail("claude-cli", "Claude Code", configured = true),
            ProviderDetail("openrouter", "OpenRouter", configured = false, takesKey = true),
        ))
        ModelCatalog.keepList(app.sessions, app.sessions.requireApi(),
            ModelList("claude-cli", listOf("sonnet", "opus"), labels = mapOf("sonnet" to "Sonnet (latest)", "opus" to "Opus (latest)")))
        app.setProfile(Profile(provider = "claude-cli", model = "sonnet"))

        val ui = UiSteps(compose, File("build/model-kept"))
        compose.mainClock.autoAdvance = false
        compose.setContent { KeloFrame(app) { Box(Modifier.size(900.dp, 1400.dp)) { ModelPage(app, onBack = {}) } } }
        compose.mainClock.advanceTimeBy(200)

        ui.waitText(en.get(R.string.model_picker_ready, "Claude Code"))   // the provider field, from the kept list
        ui.waitText("Sonnet (latest)")                                    // the model by its name
        assertTrue("no text field while the list loads", compose.onAllNodes(hasSetTextAction()).fetchSemanticsNodes().isEmpty())
        assertFalse("no raw id", ui.has("sonnet"))
        assertFalse("no loading hint in the first second", compose.onAllNodesWithText(en.get(R.string.loading)).fetchSemanticsNodes().isNotEmpty())
        assertTrue(ui.has(en.get(R.string.refresh)))

        compose.mainClock.advanceTimeBy(ModelCatalog.SLOW_MS + 200)
        ui.waitText(en.get(R.string.loading))                             // still nothing from the server: the link says so
        assertTrue("the name stays", ui.has("Sonnet (latest)"))

        // the kept provider list is what the provider field offers
        compose.onAllNodesWithText(en.get(R.string.model_picker_ready, "Claude Code")).onFirst().performClick()
        ui.waitText(en.get(R.string.model_picker_needs_setup, "OpenRouter"))
    }
}
