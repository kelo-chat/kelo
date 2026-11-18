package chat.kelo.desktop.vnc

import androidx.compose.ui.test.junit4.createComposeRule
import chat.kelo.app.agent.LocalVncScreenHost
import chat.kelo.app.agent.VncScreenHost
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.FileKeyValueStore
import chat.kelo.desktop.core.StoreKey
import chat.kelo.desktop.ui.KeloFrame
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import kotlin.test.assertSame

/** The desktop window provides a VNC screen host, so the live view shows the tab's X screen instead of the JPEG picture. */
class DesktopVncHostTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()

    @Test fun theWindowProvidesAVncScreenHost() {
        val dir = tmp.newFolder()
        val app = AppContainer(dir, FileKeyValueStore(File(dir, "kelo.properties")), StoreKey.cipher(dir), requireE2e = false)
        var seen: VncScreenHost? = null
        compose.setContent {
            KeloFrame(app) { seen = LocalVncScreenHost.current }
        }
        compose.waitForIdle()
        assertSame(DesktopVncScreenHost, seen)
    }
}
