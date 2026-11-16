package chat.kelo.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The APK handed out as a file never carries Google Play's update library (Play Core): its classes are not on this build's class
 * path, and build.gradle.kts adds it to the Play build only. The file builds get [AppUpdater] from src/fileBuilds.
 */
class UpdateDependencyTest {
    @Test fun playCoreIsNotInTheSideloadBuild() {
        for (name in listOf("com.google.android.play.core.appupdate.AppUpdateManager", "com.google.android.play.core.ktx.AppUpdateManagerKtxKt")) {
            assertFalse(name, runCatching { Class.forName(name) }.isSuccess)
        }
        assertFalse(runCatching { Class.forName("chat.kelo.app.update.PlayAppUpdates") }.isSuccess)
    }

    @Test fun theGradleFileAddsPlayCoreToThePlayBuildOnly() {
        val gradle = listOf(File("build.gradle.kts"), File("app/build.gradle.kts")).first { it.exists() }.readText()
        val lines = gradle.lines().filter { it.contains("play.app.update") }
        assertEquals(2, lines.size)
        assertTrue(lines.all { it.trim().startsWith("\"playImplementation\"(") })
    }

    @Test fun theFileBuildsUseTheirOwnUpdater() {
        val src = listOf(File("src/fileBuilds"), File("app/src/fileBuilds")).first { it.exists() }
        assertTrue(File(src, "java/chat/kelo/app/update/AppUpdatesFactory.kt").readText().contains("AppUpdater("))
    }
}
