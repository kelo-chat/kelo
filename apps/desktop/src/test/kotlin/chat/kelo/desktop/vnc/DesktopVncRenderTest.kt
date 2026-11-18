package chat.kelo.desktop.vnc

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toPixelMap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.unit.dp
import chat.kelo.desktop.UiSteps
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.parityShots
import chat.kelo.desktop.ui.KeloFrame
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.test.assertTrue

/**
 * The VNC screen draws what the server sent: a 64 x 64 screen, a Raw rectangle of red and a Tight fill of blue for the left half.
 * The shot is build/parity/live-vnc-412-light.png.
 */
class DesktopVncRenderTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()

    private fun cat(vararg parts: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        parts.forEach { out.write(it) }
        return out.toByteArray()
    }

    private fun ints(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }

    private fun header(x: Int, y: Int, w: Int, h: Int, encoding: Int): ByteArray = ints(
        x shr 8, x and 0xff, y shr 8, y and 0xff, w shr 8, w and 0xff, h shr 8, h and 0xff,
        encoding ushr 24, (encoding shr 16) and 0xff, (encoding shr 8) and 0xff, encoding and 0xff,
    )

    @Test fun theScreenDrawsTheServersPicture() {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        app.settings.setLanguage("ko")
        app.applyLanguage()
        app.settings.setTheme("light")

        val session = DesktopVncSession(
            scope = CoroutineScope(Dispatchers.Unconfined), send = {}, offerClipboard = {}, onFailed = {},
            macOs = false, dispatcher = Dispatchers.Unconfined,
        )
        // the greeting (a 64 x 64 screen), an update with two rectangles: Raw red everywhere, then a Tight fill of blue on the left half
        val red = ByteArray(64 * 64 * 4) { i -> if (i % 4 == 2) 255.toByte() else 0 }
        val update = cat(
            ints(0, 0, 0, 2),
            header(0, 0, 64, 64, RfbEncoding.RAW), red,
            header(0, 0, 32, 64, RfbEncoding.TIGHT), ints(0x80, 0x00, 0x00, 0xff),
        )
        session.toPage(cat(serverStart(64, 64), update))

        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Box(Modifier.size(412.dp, 560.dp)) {
                    VncSurface(session, holding = false, onPaste = {}, modifier = Modifier.fillMaxSize())
                }
            }
        }
        ui.settle(12)
        ui.shot("live-vnc-412-light")

        val image = compose.onNodeWithTag("vnc-surface").captureToImage()
        val map = image.toPixelMap()
        val left = map[image.width / 4, image.height / 2]
        val right = map[image.width * 3 / 4, image.height / 2]
        assertTrue(left.blue > 0.9f && left.red < 0.1f, "left half should be blue, was $left")
        assertTrue(right.red > 0.9f && right.blue < 0.1f, "right half should be red, was $right")
    }
}
