package chat.kelo.desktop

import chat.kelo.app.update.TestReleaseKey
import chat.kelo.desktop.core.DesktopPlatform
import chat.kelo.desktop.core.DesktopVersion
import chat.kelo.desktop.core.StoreUpdates
import chat.kelo.desktop.core.Updater
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/**
 * The Microsoft Store copy of the desktop app (MSIX): its channel, that its own updater stays off, that its update notices go to the
 * Store, and the Store's answer ([StoreUpdates]: parsing, timeout, the 6-hour cache). Nothing here calls the network or the Store.
 */
class StoreUpdatesTest {
    @get:Rule val tmp = TemporaryFolder()
    private val hour = 60L * 60 * 1000

    @Test fun theStoreCopyIsTheMsstoreChannelAndEveryOtherBuildIsTheDownloadOne() {
        assertEquals("msstore", DesktopVersion.channelFor(store = true))
        assertEquals("download", DesktopVersion.channelFor(store = false))
    }

    @Test fun aPackagedInstallIsRecognisedOnlyOnWindowsUnderWindowsApps() {
        val store = "C:\\Program Files\\WindowsApps\\ChatKelo_1.0.2.0_x64__abc\\app\\runtime"
        assertTrue(DesktopPlatform.inStoreFolder(DesktopPlatform.Os.WINDOWS, store, null))
        assertTrue(DesktopPlatform.inStoreFolder(DesktopPlatform.Os.WINDOWS, null, store))
        assertFalse(DesktopPlatform.inStoreFolder(DesktopPlatform.Os.WINDOWS, "C:\\Users\\me\\AppData\\Local\\Kelo\\app", "C:\\Users\\me\\AppData\\Local\\Kelo\\app\\runtime"))
        assertFalse(DesktopPlatform.inStoreFolder(DesktopPlatform.Os.MAC, "/Applications/WindowsApps/Kelo.app", null))
        assertFalse(DesktopPlatform.inStoreFolder(DesktopPlatform.Os.WINDOWS, null, null))
    }

    @Test fun theHelpersAnswerIsReadOnlyWhenItIsTheCountJson() {
        assertEquals(0, StoreUpdates.parse("{\"updates\":0}"))
        assertEquals(1, StoreUpdates.parse("{\"updates\": 1}\r\n"))
        assertEquals(3, StoreUpdates.parse("  {\"updates\":3}  "))
        assertNull(StoreUpdates.parse("garbage"))
        assertNull(StoreUpdates.parse("{\"updates\":"))
        assertNull(StoreUpdates.parse(""))
        assertNull(StoreUpdates.parse(null))
        assertNull(StoreUpdates.parse("{\"updates\":\"1\"}"))            // a string is not a count
        assertNull(StoreUpdates.parse("{\"updates\":-1}"))
        assertNull(StoreUpdates.parse("{\"other\":1}"))
        assertNull(StoreUpdates.parse("[1]"))
    }

    @Test fun aHelperThatPrintsNothingOrRunsTooLongGivesNoAnswer() {
        assumeTrue(File("/bin/sleep").exists() && File("/bin/echo").exists())
        assertEquals("{\"updates\":2}", StoreUpdates.run(listOf("/bin/echo", "{\"updates\":2}"))?.trim())
        assertEquals(2, StoreUpdates.parse(StoreUpdates.run(listOf("/bin/echo", "{\"updates\":2}"))))
        val started = System.currentTimeMillis()
        assertNull(StoreUpdates.run(listOf("/bin/sleep", "5"), timeoutMs = 300))
        assertTrue("the wait is cut at the timeout", System.currentTimeMillis() - started < 4000)
        assertNull(StoreUpdates.run(listOf("/no/such/helper-for-kelo")))
    }

    @Test fun theCommandPassesTheScriptEncodedSoNoQuotingIsNeeded() {
        val cmd = StoreUpdates.command()
        assertEquals("-EncodedCommand", cmd[cmd.size - 2])
        assertTrue(cmd.last().isNotBlank())
        assertTrue(StoreUpdates.SCRIPT.contains("GetAppAndOptionalStorePackageUpdatesAsync"))
    }

    @Test fun theStoreIsAskedAtMostEverySixHours() {
        val t0 = 1_000_000_000L
        assertTrue(StoreUpdates.due(null, t0))
        assertFalse(StoreUpdates.due(StoreUpdates.Last(t0, 0), t0 + 5 * hour))
        assertTrue(StoreUpdates.due(StoreUpdates.Last(t0, 0), t0 + 6 * hour))
        assertTrue("a clock set back counts as due", StoreUpdates.due(StoreUpdates.Last(t0, 0), t0 - hour))
        assertEquals(StoreUpdates.Last(t0, -1), StoreUpdates.decode(StoreUpdates.encode(StoreUpdates.Last(t0, -1))))
        assertNull(StoreUpdates.decode("12 x"))
        assertNull(StoreUpdates.decode(null))
    }

    @Test fun theStoreCopyNeverChecksTheInstallerAndTellsTheStoreWhenTheStoreSaysNewer() = runBlocking {
        var asked = 0
        val answers = ArrayDeque(listOf("{\"updates\":1}"))
        val u = storeUpdater(tmp.newFolder()) { asked++; answers.removeFirstOrNull() }
        assertFalse(u.enabled)                                             // the installer check is off
        assertEquals(Updater.Way.STORE, u.way)
        u.checkIfDue()
        assertEquals(1, asked)
        assertTrue(u.storeNewer.value)
        assertEquals("ms-windows-store://pdp/?productid=9P54HX2TX361", Updater.STORE_UPDATES)
    }

    @Test fun theStoreCopyAsksTheStoreAgainOnlyAfterSixHoursAndKeepsTheAnswerAcrossRuns() = runBlocking {
        val dir = tmp.newFolder()
        var now = 1_000_000_000L
        var asked = 0
        val u = storeUpdater(dir, clock = { now }) { asked++; "{\"updates\":1}" }
        u.checkIfDue(); u.check()
        now += 5 * hour
        u.checkIfDue()
        assertEquals(1, asked)
        // a new run of the app (a new Updater on the same folder) reads the cached answer
        val again = storeUpdater(dir, clock = { now }) { asked++; "{\"updates\":0}" }
        again.checkStore()
        assertEquals(1, asked)
        assertTrue(again.storeNewer.value)
        now += 2 * hour                                                    // six hours in all
        again.checkIfDue()
        assertEquals(2, asked)
        assertFalse(again.storeNewer.value)
    }

    @Test fun aFailedStoreCheckGivesNoNoticeAndIsNotRepeatedWithinSixHours() = runBlocking {
        val dir = tmp.newFolder()
        var now = 1_000_000_000L
        var asked = 0
        val u = storeUpdater(dir, clock = { now }) { asked++; null }
        u.checkIfDue()
        assertFalse(u.storeNewer.value)
        now += hour
        u.checkIfDue()
        assertEquals(1, asked)
        now += 6 * hour
        u.checkIfDue()
        assertEquals(2, asked)
        assertFalse(u.storeNewer.value)
    }

    @Test fun theStoreCopyStillOpensTheStoresUpdatesPage() = runBlocking {
        val links = mutableListOf<String>()
        val u = storeUpdater(tmp.newFolder()) { "{\"updates\":1}" }
        u.checkIfDue()
        u.updateNow { links += it; true }
        assertEquals(listOf(Updater.STORE_UPDATES), links)
    }

    private fun storeUpdater(dir: File, clock: () -> Long = System::currentTimeMillis, helper: () -> String?): Updater =
        Updater(
            "1.0.2", DesktopPlatform.Os.WINDOWS, "https://kelo.example", dir,
            store = true, clock = clock, keys = TestReleaseKey.KEYS, storeRun = helper,
        )
}
