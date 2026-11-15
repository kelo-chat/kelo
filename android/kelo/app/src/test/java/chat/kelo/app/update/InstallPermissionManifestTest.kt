package chat.kelo.app.update

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * REQUEST_INSTALL_PACKAGES (handing a verified update APK to Android's installer) belongs only to the builds handed out as a file:
 * never in the shared manifest, and the Play bundle removes it in case a library brings it. The sideload APK still asks for none of
 * the permissions Play Protect blocks in apps installed from a file (text messages, call log, notification reading).
 */
class InstallPermissionManifestTest {
    private val perm = "android.permission.REQUEST_INSTALL_PACKAGES"
    private fun manifest(set: String): String {
        val f = listOf(File("src/$set/AndroidManifest.xml"), File("app/src/$set/AndroidManifest.xml")).first { it.exists() }
        return f.readText()
    }
    private fun declares(xml: String, name: String): Boolean =
        Regex("""<uses-permission\s+android:name="${Regex.escape(name)}"\s*/>""").containsMatchIn(xml)

    @Test fun onlySideloadAndFullAskToInstallPackages() {
        assertFalse(manifest("main").contains(perm))
        assertTrue(declares(manifest("sideload"), perm))
        assertTrue(declares(manifest("full"), perm))
        val play = manifest("play")
        assertFalse(declares(play, perm))
        assertTrue(Regex("""<uses-permission\s+android:name="$perm"\s+tools:node="remove"\s*/>""").containsMatchIn(play))
    }

    @Test fun theSideloadApkStaysClearOfWhatPlayProtectBlocks() {
        val sideload = manifest("main") + manifest("sideload")
        for (p in listOf("READ_SMS", "SEND_SMS", "RECEIVE_SMS", "READ_CALL_LOG", "BIND_NOTIFICATION_LISTENER_SERVICE")) {
            assertFalse(p, sideload.contains("android.permission.$p"))
        }
    }
}
