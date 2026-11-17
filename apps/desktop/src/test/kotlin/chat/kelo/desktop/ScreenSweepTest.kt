package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.semantics.SemanticsNode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.unit.dp
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.platform.LocalPageFrame
import chat.kelo.app.ui.platform.PageFrame
import chat.kelo.desktop.chat.ChatPane
import chat.kelo.desktop.chat.DesktopChat
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.pages.ConnectorsPane
import chat.kelo.desktop.pages.GoalsPane
import chat.kelo.desktop.pages.WorkPane
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import javax.imageio.ImageIO

private const val HEIGHT = 720

/**
 * Screen sweep (owner 2026-11-16): every My page, the work, goals and connectors pages, the chat in each scene the mock can show,
 * at 320 and 412 dp wide, in Korean and English, light, drawn the way the phone lays them out (FormFactor.Phone) with the desktop's
 * text engine. A page is HEIGHT dp tall and scrolled down in steps, one picture per step: sweep-<lang>-<width>-<screen>-<n>.png in
 * $KELO_SHOTS (else build/parity). Look at them for clipped or overlapping text, a word cut in the middle, a field that wraps.
 * Not an assertion test: it only fails when the sweep cannot run (a screen that does not come up is printed as "sweep: ... failed").
 * KELO_SWEEP_ONLY=my-actions,work runs only those screens.
 * Run: cd apps/desktop && KELO_MOCK_E2E=0 KELO_SHOTS=<folder> ./gradlew --no-daemon test --tests 'chat.kelo.desktop.ScreenSweepTest'
 */
class ScreenSweepTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val servers = mutableListOf<MockServer>()
    private val current = mutableStateOf<AppContainer?>(null)
    private val width = mutableStateOf(320)
    private val screen = mutableStateOf("none")
    private val openId = mutableStateOf<String?>(null)
    private val chatScene = mutableStateOf<Pair<AppContainer, DesktopChat>?>(null)
    private var lang = "en"

    @After fun stop() { servers.forEach { it.stop() } }

    @Test fun korean() = sweep("ko")
    @Test fun english() = sweep("en")

    /** My pages by name; the second list needs a scene of the mock (its env) to have anything to show. */
    private val myViews = listOf(
        MyView.PROFILE, MyView.AGENT, "permissions", MyView.RECORDS, MyView.NOTIFY, MyView.MODEL, MyView.USAGE, MyView.DEVICES,
        MyView.RESOURCES, "cleanup", MyView.DOCUMENTS, "files", MyView.PRIVACY, "forget", "actions", MyView.NEST,
        MyView.LANGUAGE, "wallet", "vault", "operator", "desktop",
    )
    private val myScenes = listOf(
        Triple("su", mapOf("KELO_MOCK_SERVER_UPDATE" to "available"), listOf("home", chat.kelo.app.my.MyPage.SERVER_UPDATE)),
        Triple("notices", mapOf("KELO_MOCK_NOTICES" to "1"), listOf("home")),
        Triple("wallet", mapOf("KELO_MOCK_WALLET" to "ask"), listOf("wallet")),
        Triple("res-fail", mapOf("KELO_MOCK_RESOURCES" to "fail"), listOf(MyView.RESOURCES)),
    )
    private val connectorRows = listOf("GitHub", "Slack", "Gmail", "IMAP")
    private val chatScenes = listOf(
        "default" to emptyMap<String, String>(),
        "briefing" to mapOf("KELO_MOCK_NO_ASKS" to "1", "KELO_MOCK_CATCHUP" to "1", "KELO_MOCK_BRIEFING" to "1"),
        "briefing-long" to mapOf("KELO_MOCK_NO_ASKS" to "1", "KELO_MOCK_CATCHUP" to "1", "KELO_MOCK_BRIEFING" to "long"),
        "secret" to mapOf("KELO_MOCK_NO_ASKS" to "1", "KELO_MOCK_SECRET_ENTRIES" to "1"),
        "location" to mapOf("KELO_MOCK_NO_ASKS" to "1", "KELO_MOCK_LOCATION_CARD" to "1"),
        "wallet" to mapOf("KELO_MOCK_NO_ASKS" to "1", "KELO_MOCK_WALLET" to "ask", "KELO_MOCK_CATCHUP" to "pad"),
        "batch" to mapOf("KELO_MOCK_NO_ASKS" to "1", "KELO_MOCK_BATCH" to "1"),
    )

    private fun scene(env: Map<String, String>): AppContainer {
        val m = MockServer("--lang", lang, env = env).also { servers += it }
        return m.app(tmp.newFolder(), lang = lang, theme = "light")
    }

    private fun sweep(lang: String) {
        this.lang = lang
        val first = scene(emptyMap())
        val ui = UiSteps(compose, parityShots())
        current.value = first
        compose.mainClock.autoAdvance = false
        compose.setContent {
            val app = current.value
            if (app != null) key(app) {
                KeloFrame(app) {
                    CompositionLocalProvider(LocalFormFactor provides FormFactor.Phone, LocalPageFrame provides PageFrame.None) {
                        Box(Modifier.size(width.value.dp, HEIGHT.dp)) {
                            when (screen.value) {
                                "work" -> WorkPane(app, topInset = 16.dp)
                                "goals" -> GoalsPane(app, topInset = 16.dp)
                                "connectors" -> ConnectorsPane(app, topInset = 16.dp, openId = openId.value, onOpened = { openId.value = null })
                                "my" -> MyPaneAt(app, myStart)
                                "chat" -> chatScene.value?.let { (a, c) ->
                                    key(c) {
                                        val profile by a.profile.collectAsState()
                                        CompositionLocalProvider(androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner provides c) {
                                            ChatPane(c.vm, a.sessions, profile ?: Profile(), onProfile = a::setProfile, onOpenConnector = {}, topInset = 56.dp)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        for (w in listOf(320, 412)) {
            width.value = w
            fun shots(name: String, down: Boolean = true) {
                for (part in 1..12) {
                    ui.settle(30)
                    val file = "sweep-$lang-$w-$name-$part"
                    ui.shot(file)
                    crop(File(parityShots(), "$file.png"), w)
                    if (part == 4 && !down) return
                    if (!scroll(ui, if (down) 1 else -1, either = !down)) return
                }
            }
            fun reset() { screen.value = "none"; ui.settle(); }
            fun show(s: String) { reset(); screen.value = s; ui.settle(30); Thread.sleep(1500); ui.settle(20) }
            fun guarded(name: String, body: () -> Unit) { if (!wanted(name)) return; runCatching(body).onFailure { println("sweep: $name failed: $it"); reset() } }

            // My: the home page, then each page by name
            current.value = first
            show("my"); guarded("my-home") { shots("my-home") }
            for (v in myViews) guarded("my-$v") { openMy(ui, first, v); shots("my-$v") }
            for ((tag, env, views) in myScenes) {
                val app = scene(env); current.value = app; ui.settle(20)
                for (v in views) guarded("my-$tag-$v") {
                    if (v == "home") { show("my"); shots("my-$tag-home") } else { openMy(ui, app, v); shots("my-$tag-$v") }
                }
            }
            current.value = first
            for (s in listOf("work", "goals", "connectors")) guarded(s) { show(s); shots(s) }
            // a connector's own page: its row clicked
            for (row in connectorRows) guarded("connector-$row") {
                show("connectors")
                compose.onAllNodesWithText(row, substring = true).onFirst().performClick()
                ui.settle(30); Thread.sleep(800)
                shots("connector-${row.lowercase()}")
            }
            // the chat in each scene (cards of every kind that need no typing)
            for ((name, env) in chatScenes) guarded("chat-$name") {
                val app = scene(env)
                chatScene.value = app to DesktopChat(app)
                show("chat")
                ui.waitUntil("the chat loaded", timeout = 20_000) { chatScene.value!!.second.vm.state.value.loaded }
                ui.settle(40); Thread.sleep(1500)
                shots("chat-$name", down = false)
                reset(); chatScene.value!!.second.close()
            }
        }
    }

    private val myStart = mutableStateOf<String?>(null)

    /** KELO_SWEEP_ONLY=my-actions,work: only the screens whose name starts with one of these (to look again after a fix). */
    private fun wanted(name: String): Boolean {
        val only = System.getenv("KELO_SWEEP_ONLY")?.split(',')?.map { it.trim() }?.filter { it.isNotEmpty() } ?: return true
        return only.any { name.startsWith(it) }
    }

    /** The My page showing [view] (a fresh My page each time, so the one before does not stay on the stack). */
    private fun openMy(ui: UiSteps, app: AppContainer, view: String) {
        current.value = app
        screen.value = "none"; ui.settle(); screen.value = "my"; ui.settle(30)
        Thread.sleep(800)
        app.myPageRequests.value = view
        ui.settle(30); Thread.sleep(1200)
    }

    private fun crop(f: File, w: Int) {
        val img = ImageIO.read(f)
        ImageIO.write(img.getSubimage(0, 0, minOf(w, img.width), minOf(HEIGHT, img.height)), "png", f)
    }

    private fun scrollNode(): SemanticsNode? =
        compose.onAllNodes(SemanticsMatcher.keyIsDefined(SemanticsProperties.VerticalScrollAxisRange)).fetchSemanticsNodes()
            .maxByOrNull { it.config[SemanticsProperties.VerticalScrollAxisRange].maxValue() }

    /**
     * One step in the page's scroll area ([dir] 1 = down the page, -1 = back up; semantics, not the mouse); false when it did not move
     * (at the end, or no scroll area). The clock stays hand-driven: a spinner never lets the page go idle.
     */
    private fun scroll(ui: UiSteps, dir: Int, either: Boolean): Boolean {
        val before = scrollNode()?.config?.get(SemanticsProperties.VerticalScrollAxisRange)?.value?.invoke() ?: return false
        val step = (HEIGHT - 120).toFloat() * dir
        fun go(dy: Float) {
            val target = scrollNode() ?: return
            val n = compose.onAllNodes(SemanticsMatcher.keyIsDefined(SemanticsProperties.VerticalScrollAxisRange))
            val idx = n.fetchSemanticsNodes().indexOfFirst { it.id == target.id }
            n[idx].performSemanticsAction(SemanticsActions.ScrollBy) { it(0f, dy) }
            ui.settle(20)
        }
        go(step)
        var after = scrollNode()?.config?.get(SemanticsProperties.VerticalScrollAxisRange)?.value?.invoke() ?: return false
        if (after == before && either) { go(-step); after = scrollNode()?.config?.get(SemanticsProperties.VerticalScrollAxisRange)?.value?.invoke() ?: return false }
        return after != before
    }
}
