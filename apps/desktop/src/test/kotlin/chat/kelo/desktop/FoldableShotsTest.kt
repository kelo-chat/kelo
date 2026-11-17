package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.components.TwoPane
import chat.kelo.app.ui.components.rememberSplitState
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.platform.LocalPageFrame
import chat.kelo.app.ui.platform.LocalWindowShape
import chat.kelo.app.ui.platform.PageFrame
import chat.kelo.app.ui.platform.WindowRules
import chat.kelo.desktop.chat.ChatPane
import chat.kelo.desktop.chat.DesktopChat
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.pages.WorkPane
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import javax.imageio.ImageIO

/**
 * Wide-window pictures (foldable-shared): the chat tab as the Android shell lays it out (the same WindowRules decide), at a phone
 * (360x780), an unfolded foldable in portrait (673x841) and landscape (841x673) and a roomy window (1280x800), in Korean and English.
 * Files: foldable-<lang>-<w>x<h>.png in $KELO_SHOTS. Not an assertion test; the rules are pinned in WindowRulesTest (shared).
 */
class FoldableShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val servers = mutableListOf<MockServer>()
    private val size = mutableStateOf(360 to 780)
    private val scene = mutableStateOf<Triple<AppContainer, DesktopChat, Int>?>(null)

    @After fun stop() { servers.forEach { it.stop() } }

    @Test fun korean() = run("ko")
    @Test fun english() = run("en")

    private fun run(lang: String) {
        val m = MockServer("--lang", lang, env = emptyMap()).also { servers += it }
        val app = m.app(tmp.newFolder(), lang = lang, theme = "light")
        val chat = DesktopChat(app)
        scene.value = Triple(app, chat, 0)
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            val (a, c, _) = scene.value!!
            key(a) {
                KeloFrame(a) {
                    val (w, h) = size.value
                    val shape = WindowRules.shapeFor(w.toFloat(), h.toFloat())
                    val column = WindowRules.pageColumn(shape)
                    CompositionLocalProvider(
                        LocalFormFactor provides FormFactor.Phone,
                        LocalWindowShape provides shape,
                        LocalPageFrame provides (column?.let { PageFrame(0.dp, it) } ?: PageFrame.None),
                    ) {
                        Box(Modifier.size(w.dp, h.dp)) {
                            val profile by a.profile.collectAsState()
                            CompositionLocalProvider(androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner provides c) {
                                val side = WindowRules.side(shape, onChatTab = true, liveOpen = false)
                                TwoPane(
                                    first = { ChatPane(c.vm, a.sessions, profile ?: Profile(), onProfile = a::setProfile, onOpenConnector = {}, topInset = 56.dp) },
                                    second = { WorkPane(a, topInset = 56.dp) },
                                    split = rememberSplitState(),
                                    showSecond = side != WindowRules.Side.None,
                                )
                            }
                        }
                    }
                }
            }
        }
        ui.waitUntil("the chat loaded", timeout = 20_000) { chat.vm.state.value.loaded }
        for ((w, h) in listOf(360 to 780, 673 to 841, 841 to 673, 1280 to 800)) {
            size.value = w to h
            ui.settle(40); Thread.sleep(1500); ui.settle(20)
            val name = "foldable-$lang-${w}x$h"
            ui.shot(name)
            val f = java.io.File(parityShots(), "$name.png")
            val img = ImageIO.read(f)
            ImageIO.write(img.getSubimage(0, 0, minOf(w, img.width), minOf(h, img.height)), "png", f)
        }
        chat.close()
    }
}
