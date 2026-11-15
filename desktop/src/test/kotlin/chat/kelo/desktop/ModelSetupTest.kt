package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toAwtImage
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.hasScrollToKeyAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.performScrollToKey
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onLast
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.isRoot
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.EndpointRules
import chat.kelo.app.chat.KeyRules
import chat.kelo.app.chat.SetupRules
import chat.kelo.app.chat.SignInRules
import chat.kelo.app.core.Profile
import chat.kelo.app.core.Session
import chat.kelo.app.my.CliStatus
import chat.kelo.app.my.ProviderDetail
import chat.kelo.desktop.chat.ChatModel
import chat.kelo.desktop.chat.ChatPane
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.i18n.Strings
import chat.kelo.desktop.ui.KeloFrame
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.net.HttpURLConnection
import java.net.ServerSocket
import java.net.URI
import javax.imageio.ImageIO

/** Which services the model sheet offers to set up, and the small checks of the settings (no UI). */
class SetupRulesTest {
    private fun p(name: String, configured: Boolean? = false, key: Boolean = false, url: Boolean = false, signin: Boolean = false,
                  signedIn: Boolean? = null, unofficial: Boolean = false, cli: CliStatus? = null, display: String = name) =
        ProviderDetail(name, display, configured = configured, takesKey = key, takesUrl = url, signin = signin, signedIn = signedIn,
            unofficial = unofficial, cli = cli)

    @Test fun aFreshServerOffersEveryServiceThatTheAppCanSetUp() {
        val rows = SetupRules.rows(listOf(
            p("openrouter", key = true, display = "OpenRouter"),
            p("bedrock", configured = null),                                       // nothing to enter in the app: left out
            p("claude-subscription", signin = true, signedIn = false, unofficial = true, display = "Claude subscription"),
            p("custom", key = true, url = true, display = "Custom endpoint"),
            p("openai", key = true, display = "OpenAI (API key)"),
            p("groq", configured = true, key = true, display = "Groq"),             // ready: its models are listed instead
        ))
        assertEquals(listOf("custom", "openai", "openrouter", "claude-subscription"), rows.map { it.name })
    }

    @Test fun anAgyCliThatCanBeInstalledIsOffered() {
        val agy = p("antigravity", cli = CliStatus(installed = false, installable = true), unofficial = true)
        assertTrue(SetupRules.canSetUp(agy))
        assertFalse(SetupRules.canSetUp(p("x", cli = CliStatus(installed = false, installable = false))))
    }

    @Test fun theThinkingLevelWaitsForAModel() {
        assertFalse(SetupRules.showsEffort(Profile(provider = "", model = "")))
        assertTrue(SetupRules.showsEffort(Profile(provider = "openai", model = "gpt-5.5")))
    }

    @Test fun savingNeedsAModelOrTheServersDefault() {
        assertFalse(SetupRules.canSave("", "", serverHasDefault = false))
        assertTrue(SetupRules.canSave("", "", serverHasDefault = true))
        assertFalse(SetupRules.canSave("openai", " ", serverHasDefault = true))
        assertTrue(SetupRules.canSave("openai", "gpt-5.5", serverHasDefault = false))
    }

    @Test fun addressesKeysAndSignIns() {
        assertNull(EndpointRules.check("http://192.168.0.10:1234/v1"))
        assertEquals(EndpointRules.Problem.SCHEME, EndpointRules.check("localhost:11434"))
        assertEquals(EndpointRules.Problem.SPACES, EndpointRules.check("http://a b"))
        assertEquals(EndpointRules.Problem.EMPTY, EndpointRules.check("  "))
        assertEquals(KeyRules.State.IN_APP, KeyRules.state("app"))
        assertEquals(KeyRules.State.NONE, KeyRules.state(null))
        assertFalse(KeyRules.ready("short"))
        assertTrue(SignInRules.offered(signin = true, signedIn = false))
        assertFalse(SignInRules.offered(signin = true, signedIn = true))
        assertEquals("1:05", SignInRules.mmss(65))
    }
}

/**
 * The whole first start against tools/mock_server.py --fresh --signin (a brand-new server: no model, no service ready): the chat's
 * "choose a model" card → the model sheet, which lists the services to set up → OpenAI: paste a key → pick a model → saved, the card
 * goes away; then an OpenAI-compatible server's address (custom) and a sign-in (Claude, consent first, code pasted back). Skipped
 * where python3 cannot run. Screenshots of each step go to build/setup-flow (and to $KELO_SHOTS when set).
 */
@OptIn(ExperimentalTestApi::class)
class ModelSetupFlowTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: Process? = null
    private val en = Strings.of("en")
    private val shots = (System.getenv("KELO_SHOTS")?.let(::File) ?: File("build/setup-flow")).also { it.mkdirs() }

    private fun freePort(): Int {
        val taken = setOf(8080, 8765, 8786, 8787, 6080) + (8100..8103) + (6100..6102)
        while (true) {
            val port = ServerSocket(0).use { it.localPort }
            if (port !in taken) return port
        }
    }

    private fun startMock(): String {
        val port = freePort()
        val script = File("tools/mock_server.py").absoluteFile
        val proc = runCatching {
            ProcessBuilder(mockPython ?: "python3", script.path, "--port", "$port", "--lang", "en", "--fresh", "--signin").redirectErrorStream(true).start()
        }.getOrNull()
        assumeTrue("Python 3 is needed for the mock server", mockPython != null && proc != null)
        mock = proc
        Runtime.getRuntime().addShutdownHook(Thread { proc?.destroyForcibly() })   // also when the test run is stopped
        val base = "http://127.0.0.1:$port"
        val end = System.currentTimeMillis() + 10_000
        while (System.currentTimeMillis() < end) {
            val up = runCatching { (URI("$base/healthz").toURL().openConnection() as HttpURLConnection).responseCode == 200 }.getOrDefault(false)
            if (up) return base
            Thread.sleep(100)
        }
        assumeTrue("the mock server did not start", false)
        return base
    }

    @After fun stop() { mock?.destroyForcibly() }

    // The clock is driven by hand: Kelo's character and the loading spinners never stop moving, so the page is never "idle".
    private fun waitUntil(what: String, timeout: Long = 10_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeout
        while (true) {
            compose.mainClock.advanceTimeBy(64)
            if (ok()) return
            if (System.currentTimeMillis() > end) throw AssertionError("timed out waiting for $what")
            Thread.sleep(25)
        }
    }

    private fun waitText(text: String, timeout: Long = 10_000) =
        waitUntil("\"$text\"", timeout) { compose.onAllNodesWithText(text, substring = true).fetchSemanticsNodes().isNotEmpty() }

    /** The model sheet's list scrolled to an item (a lazy list composes only what is on screen). */
    private fun sheetTo(key: String) {
        compose.onNode(hasScrollToKeyAction()).performScrollToKey(key)
        compose.mainClock.advanceTimeBy(200)
    }

    private fun shot(name: String) {
        repeat(8) { compose.mainClock.advanceTimeBy(64); Thread.sleep(10) }
        // the window and any open list (a popup is a root of its own), drawn one over the other
        val layers = compose.onAllNodes(isRoot()).fetchSemanticsNodes().indices.map { compose.onAllNodes(isRoot())[it].captureToImage().toAwtImage() }
        val out = java.awt.image.BufferedImage(layers[0].getWidth(null), layers[0].getHeight(null), java.awt.image.BufferedImage.TYPE_INT_ARGB)
        out.createGraphics().apply { layers.forEach { drawImage(it, 0, 0, null) }; dispose() }
        ImageIO.write(out, "png", File(shots, "$name.png"))
    }

    @Test fun aBrandNewServerIsSetUpFromTheDesktopApp() {
        try { flow() } catch (e: Throwable) { runCatching { shot("zz-failed") }; throw e }
    }

    private fun flow() {
        val base = startMock()
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("en")
        app.settings.setTheme("light")
        app.sessions.signIn(Session(base, "mock-desktop-token"))
        runBlocking { app.setProfile(app.sessions.requireApi().get<Profile>("/api/profile")) }
        assertEquals(false, app.profile.value?.modelReady)
        val chat = ChatModel(app)
        compose.mainClock.autoAdvance = false

        compose.setContent {
            KeloFrame(app) {
                val profile by app.profile.collectAsState()
                ChatPane(chat, app.sessions, profile ?: Profile(), onProfile = app::setProfile, onOpenConnector = {}, topInset = 0.dp)
            }
        }

        // 1. the first-start card, 2. its button opens the sheet, which lists the services that can be set up
        waitText(en.get(R.string.model_first_title))
        shot("01-first-start")
        compose.onNode(hasText(en.get(R.string.model_first_button)) and SemanticsMatcher.expectValue(SemanticsProperties.Role, Role.Button)).performClick()
        val openai = en.get(R.string.model_picker_needs_setup, "OpenAI (API key)")
        waitText(en.get(R.string.desktop_chooser_setup_title))
        compose.onNodeWithText(en.get(R.string.desktop_chooser_setup_hint)).assertExists()
        shot("02-sheet-setup-rows")
        sheetTo("s:openai")
        waitText(openai)
        compose.onAllNodesWithText(en.get(R.string.chat_effort)).fetchSemanticsNodes().let { assertTrue("no thinking level without a model", it.isEmpty()) }
        shot("02b-sheet-setup-rows-scrolled")

        // 3. OpenAI: paste a key and save; its models load
        compose.onNodeWithText(openai).performClick()
        waitText(en.get(R.string.model_key_none))
        compose.onNode(hasSetTextAction() and hasText(en.get(R.string.model_key_paste))).performTextInput("sk-mock-0000-1234")
        shot("03-openai-key")
        compose.onAllNodesWithText(en.get(R.string.save)).onFirst().performClick()
        waitText(en.get(R.string.model_key_in_app, "1234"))
        waitText(en.get(R.string.model_picker_choose_model))

        // 4. pick a model and save: the profile has it, the card goes away
        compose.onNodeWithText(en.get(R.string.model_picker_choose_model)).performClick()
        waitText("GPT-5.5 Mini")
        shot("04-openai-models")
        compose.onNodeWithText("GPT-5.5 Mini").performClick()
        shot("05-openai-picked")
        compose.onAllNodesWithText(en.get(R.string.save)).onLast().performClick()
        waitUntil("the model saved") { app.profile.value?.modelReady == true }
        assertEquals("openai" to "gpt-5.5-mini", app.profile.value!!.provider to app.profile.value!!.model)
        waitUntil("the card gone") { compose.onAllNodesWithText(en.get(R.string.model_first_title)).fetchSemanticsNodes().isEmpty() }
        shot("06-ready")

        // 5. the sheet now lists OpenAI's models; an OpenAI-compatible server (LM Studio, Ollama…) by its address
        compose.onNodeWithText("GPT-5.5 Mini", substring = true).performClick()
        val custom = en.get(R.string.model_picker_needs_setup, "Custom endpoint")
        waitText(en.get(R.string.desktop_chooser_setup_title))
        sheetTo("s:custom")
        waitText(custom)
        shot("07-sheet-with-models")
        compose.onNodeWithText(custom).performClick()
        waitText(en.get(R.string.model_url_label))
        compose.onNode(hasSetTextAction() and hasText(en.get(R.string.model_url_placeholder))).performTextInput("http://192.168.0.20:11434/v1")
        shot("08-custom-address")
        compose.onAllNodesWithText(en.get(R.string.save)).onFirst().performClick()
        waitText(en.get(R.string.model_url_set, "http://192.168.0.20:11434/v1"))
        compose.onNodeWithText(en.get(R.string.model_picker_choose_model)).performClick()
        waitText("Qwen3 8B")
        compose.onNodeWithText("Qwen3 8B").performClick()
        compose.onAllNodesWithText(en.get(R.string.save)).onLast().performClick()
        waitUntil("custom saved") { app.profile.value?.provider == "custom" }
        assertEquals("qwen3:8b", app.profile.value!!.model)

        // 6. a sign-in service: the consent notice, then the browser page and the code pasted back
        compose.onNodeWithText("Qwen3 8B", substring = true).performClick()
        val claude = en.get(R.string.model_picker_needs_setup, "Claude subscription")
        waitText(en.get(R.string.desktop_chooser_setup_title))
        sheetTo("s:claude-subscription-directsdk-experimental")
        waitText(claude)
        compose.onNodeWithText(claude).performClick()
        waitText(en.get(R.string.consent_check))
        shot("09-claude-consent")
        compose.onNodeWithText(en.get(R.string.consent_check)).performClick()
        compose.onNodeWithText(en.get(R.string.consent_use)).performClick()
        waitText(en.get(R.string.signin_button))
        compose.onNodeWithText(en.get(R.string.signin_button)).performClick()
        waitText(en.get(R.string.signin_paste_placeholder))
        compose.onNode(hasSetTextAction() and hasText(en.get(R.string.signin_paste_placeholder))).performTextInput("MOCK-CODE")
        shot("10-claude-paste")
        compose.onNodeWithText(en.get(R.string.signin_finish)).performClick()
        waitText(en.get(R.string.model_signin_account, "me@example.com"))
        // the account line now comes with 로그아웃 (a signed-in service can be signed out again)
        waitText(en.get(R.string.model_signout))
        // the models load again after the sign-in: wait for the field, then open it
        waitUntil("the model field") { compose.onAllNodesWithText(en.get(R.string.model_picker_choose_model)).fetchSemanticsNodes().size == 1 }
        compose.onNodeWithText(en.get(R.string.model_picker_choose_model)).performClick()
        waitText("Claude Opus 5.5")
        compose.onNodeWithText("Claude Opus 5.5").performClick()
        shot("11-claude-signed-in")
        compose.onAllNodesWithText(en.get(R.string.save)).onLast().performClick()
        waitUntil("claude saved") { app.profile.value?.provider == "claude-subscription-directsdk-experimental" }
        assertEquals("claude-opus-5-5", app.profile.value!!.model)
        shot("12-claude-ready")
        chat.close()
    }
}

/** The Gmail connector's app-password form offers only Gmail; the other mailbox connector offers every service. */
class MailRulesTest {
    private val all = listOf("gmail", "naver", "daum", "icloud").map { chat.kelo.app.connectors.MailProvider(it, it) }

    @Test fun gmailOffersOnlyGmail() {
        assertEquals(listOf("gmail"), chat.kelo.desktop.pages.MailRules.choices("gmail", all).map { it.id })
        assertEquals("gmail", chat.kelo.desktop.pages.MailRules.first("gmail", all))
    }

    @Test fun theOtherMailboxConnectorOffersThemAll() {
        assertEquals(4, chat.kelo.desktop.pages.MailRules.choices("imap_email", all).size)
        assertEquals("gmail", chat.kelo.desktop.pages.MailRules.first("imap_email", all))
    }
}
