package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import chat.kelo.app.agent.FrameHeader
import chat.kelo.app.agent.LiveCanvas
import chat.kelo.app.agent.LiveClient
import chat.kelo.app.agent.LiveFrame
import chat.kelo.app.agent.LivePointer
import chat.kelo.app.agent.LiveSource
import chat.kelo.app.agent.LiveState
import chat.kelo.app.agent.LiveStatus
import chat.kelo.app.agent.LiveViewModel
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.ui.KeloFrame
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonObject
import org.jetbrains.compose.resources.StringResource
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * Screenshots of the live view with a pointer, and with the busy banner, at 320 and 412 dp, light and dark.
 * Saved as build/parity/live-*.png or under $KELO_SHOTS.
 */
class LiveShotsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()

    private class FakeSource : LiveSource {
        val _frame = MutableStateFlow<LiveFrame?>(null)
        override val frame: StateFlow<LiveFrame?> = _frame
        val _state = MutableStateFlow(LiveState())
        override val state: StateFlow<LiveState> = _state
        val _pointer = MutableStateFlow<LivePointer?>(null)
        override val pointer: StateFlow<LivePointer?> = _pointer
        override val notices: SharedFlow<StringResource> = MutableSharedFlow()
        override fun start() {}
        override fun stop(giveBack: Boolean) {}
        override fun close() {}
        override suspend fun control(take: Boolean, handover: Boolean): LiveClient.ControlResult = LiveClient.ControlResult.Ok
        override fun send(event: JsonObject) {}
        override fun type(text: String) {}
    }

    private fun testPointer(widthDp: Int) {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("ko")
        app.applyLanguage()
        app.settings.setTheme("light")

        val source = FakeSource()
        val bitmap = ImageBitmap(800, 600)
        val header = FrameHeader(seq = 1, w = 800, h = 600, cssW = 800, cssH = 600, title = "Pointer Test")
        val frame = LiveFrame(bitmap, header)
        source._frame.value = frame
        source._pointer.value = LivePointer(x = 400f, y = 300f, down = false)
        val vm = LiveViewModel { source }

        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Box(Modifier.size(widthDp.dp, 560.dp)) {
                    LiveCanvas(client = vm, frame = frame, controlling = false)
                }
            }
        }
        ui.settle(12)
        ui.shot("live-pointer-$widthDp-light")
        app.settings.setTheme("dark")
        ui.settle(12)
        ui.shot("live-pointer-$widthDp-dark")
    }

    private fun testBusyBanner(widthDp: Int) {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("ko")
        app.applyLanguage()
        app.settings.setTheme("light")

        val source = FakeSource()
        val bitmap = ImageBitmap(800, 600)
        val header = FrameHeader(seq = 1, w = 800, h = 600, cssW = 800, cssH = 600, title = "Busy Test")
        val frame = LiveFrame(bitmap, header)
        source._frame.value = frame
        source._state.value = LiveState(status = LiveStatus(pageBusy = true))
        val vm = LiveViewModel { source }

        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Box(Modifier.size(widthDp.dp, 560.dp)) {
                    LiveCanvas(client = vm, frame = frame, controlling = false)
                }
            }
        }
        ui.waitText("페이지가 응답하지 않아요")
        ui.settle(12)
        ui.shot("live-busy-$widthDp-light")
        app.settings.setTheme("dark")
        ui.settle(12)
        ui.shot("live-busy-$widthDp-dark")
    }

    @Test fun pointer320() = testPointer(320)
    @Test fun pointer412() = testPointer(412)
    @Test fun busy320() = testBusyBanner(320)
    @Test fun busy412() = testBusyBanner(412)
}
