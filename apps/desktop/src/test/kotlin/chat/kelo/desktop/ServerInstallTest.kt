package chat.kelo.desktop

import chat.kelo.app.update.ReleaseKeys
import chat.kelo.app.update.TestReleaseKey
import chat.kelo.desktop.core.DesktopPlatform
import chat.kelo.desktop.core.ServerInstallRules
import chat.kelo.desktop.core.ServerInstallState
import chat.kelo.desktop.core.ServerInstaller
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.security.MessageDigest
import java.util.Base64

/**
 * "Use this PC as the Kelo server" (core/ServerInstall.kt): the signed row only, the sha256 and size check (a mismatch deletes the
 * file), the msiexec exit codes, the command line (the path is one quoted argument, never injected), the platform gate, the installed
 * check and the wait for the server. The HTTP side is a local MockWebServer; the process and the health check are fakes.
 */
class ServerInstallTest {
    @get:Rule val tmp = TemporaryFolder()
    private var web: MockWebServer? = null

    @After fun stop() {
        web?.shutdown()
    }

    private val msi = ByteArray(120_000) { (it % 233).toByte() }
    private fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }

    /**
     * latest.json: the desktop app's own row ("windows-msi", never used for the server) and the server's row ("windows-server-msi").
     * [signed] = the test key signs the server row; [serverRow] = false leaves the server row out.
     */
    private fun list(sha: String, signed: Boolean = true, serverRow: Boolean = true): String {
        val app = """"windows-msi": {"version": "1.0.3", "size": 5, "sha256": "${"c".repeat(64)}", "date": "", "file": "kelo-1.0.3.msi", "url": "/download/windows-msi"}"""
        val file = "kelo-server-1.0.3.msi"
        val sig = if (signed) ", " + TestReleaseKey.members("windows-server-msi", file, "1.0.3", 0, msi.size.toLong(), sha) else ""
        val server = if (serverRow) """, "windows-server-msi": {"version": "1.0.3", "size": ${msi.size}, "sha256": "$sha", "date": "", "file": "$file", "url": "/download/windows-server-msi"$sig}""" else ""
        return "{$app$server}"
    }

    /** A kelo.chat stand-in: latest.json = [list], the MSI = [file]. Nothing leaves this computer. */
    private fun serve(list: String, file: ByteArray = msi): String {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                "/download/latest.json" -> MockResponse().setBody(list)
                "/download/windows-server-msi" -> MockResponse().setBody(okio.Buffer().write(file))
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        web = server
        return server.url("/").toString().trimEnd('/')
    }

    private fun decodedScript(cmd: List<String>): String {
        assertEquals("powershell.exe", cmd[0])
        val encoded = cmd.last()
        return String(Base64.getDecoder().decode(encoded), Charsets.UTF_16LE)
    }

    @Test fun onlyTheSignedRowIsOffered() = runBlocking {
        val base = serve(list(sha(msi)))
        val signed = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = base, keys = TestReleaseKey.KEYS, run = { 0 }, healthy = { true })
        assertNotNull("the signed MSI row is picked", signed.latest())
        val unsigned = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = serve(list(sha(msi), signed = false)), keys = TestReleaseKey.KEYS, run = { 0 })
        assertNull("an unsigned row is refused", unsigned.latest())
        val production = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = base, keys = ReleaseKeys.ALL, run = { 0 })
        assertNull("a row signed with a key this app does not carry is refused", production.latest())
    }

    @Test fun installDownloadsChecksRunsAndWaitsForTheServer() = runBlocking {
        val base = serve(list(sha(msi)))
        var command: List<String>? = null
        var polls = 0
        val installer = ServerInstaller(
            os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = base, keys = TestReleaseKey.KEYS,
            run = { cmd -> command = cmd; 0 },
            healthy = { polls++ >= 2 },            // the server answers on the third look
            waitMs = 10_000, everyMs = 1,
        )
        installer.install()
        val st = installer.state.value
        assertEquals(ServerInstallState.Phase.DONE, st.phase)
        assertEquals(3, polls)
        val msiFile = File(tmp.root, "kelo-server-1.0.3.msi")
        assertTrue("the checked MSI stays in the server-install folder", msiFile.isFile)
        assertEquals(msi.size.toLong(), msiFile.length())
        assertFalse("no partial file is left", File(tmp.root, "kelo-server-1.0.3.msi.part").exists())
        assertTrue(decodedScript(command!!).contains("msiexec.exe"))
    }

    @Test fun shaMismatchDeletesTheFileAndNeverRuns() = runBlocking {
        val other = ByteArray(msi.size) { 7 }              // same size, other bytes: the row's sha256 does not match
        val base = serve(list(sha(msi)), file = other)
        var ran = false
        val installer = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = base, keys = TestReleaseKey.KEYS, run = { ran = true; 0 }, healthy = { true })
        installer.install()
        assertEquals(ServerInstallState.Phase.FAILED, installer.state.value.phase)
        assertEquals(ServerInstallState.Failure.MISMATCH, installer.state.value.failure)
        assertFalse(File(tmp.root, "kelo-server-1.0.3.msi").exists())
        assertFalse(File(tmp.root, "kelo-server-1.0.3.msi.part").exists())
        assertFalse("nothing runs from a file that does not check out", ran)
    }

    @Test fun nothingRunsOffWindows() = runBlocking {
        var ran = false
        val mac = ServerInstaller(os = DesktopPlatform.Os.MAC, dir = tmp.root, base = serve(list(sha(msi))), keys = TestReleaseKey.KEYS, run = { ran = true; 0 })
        mac.install()
        assertEquals(ServerInstallState.Phase.IDLE, mac.state.value.phase)
        assertFalse("the installer never starts on another system", ran)
        assertFalse(File(tmp.root, "kelo-server-1.0.3.msi").exists())
    }

    @Test fun theDesktopAppRowIsNeverTheServer() = runBlocking {
        // only the desktop app's own MSI row is published (signed): the server is not there, so nothing is offered or run
        val appOnly = """{"windows-msi": {"version": "1.0.3", "size": ${msi.size}, "sha256": "${sha(msi)}", "date": "", "file": "kelo-1.0.3.msi", "url": "/download/windows-msi"${TestReleaseKey.members("windows-msi", "kelo-1.0.3.msi", "1.0.3", 0, msi.size.toLong(), sha(msi)).let { ", " + it }}}}"""
        val base = serve(appOnly)
        assertNull("the app's windows-msi row is not the server", ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = base, keys = TestReleaseKey.KEYS, run = { 0 }).latest())
        var ran = false
        val installer = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = base, keys = TestReleaseKey.KEYS, run = { ran = true; 0 })
        installer.install()
        assertEquals(ServerInstallState.Phase.UNAVAILABLE, installer.state.value.phase)
        assertFalse("no server MSI is run", ran)
        assertFalse(File(tmp.root, "kelo-server-1.0.3.msi").exists())
    }

    @Test fun aMissingServerRowIsNotAvailableAndOffersNothing() = runBlocking {
        val installer = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = serve(list(sha(msi), serverRow = false)), keys = TestReleaseKey.KEYS, run = { error("must not run") })
        installer.prepare()
        assertEquals(ServerInstallState.Phase.UNAVAILABLE, installer.state.value.phase)
        assertFalse("no install button is offered", installer.state.value.ready)
        installer.install()
        assertEquals(ServerInstallState.Phase.UNAVAILABLE, installer.state.value.phase)
    }

    @Test fun aPublishedServerRowIsOfferedAfterPrepare() = runBlocking {
        val installer = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = serve(list(sha(msi))), keys = TestReleaseKey.KEYS, run = { 0 })
        installer.prepare()
        assertEquals(ServerInstallState.Phase.IDLE, installer.state.value.phase)
        assertTrue(installer.state.value.ready)
    }

    @Test fun exitCodesMapToStates() = runBlocking {
        assertEquals(ServerInstallRules.Outcome.DONE, ServerInstallRules.outcome(0))
        assertEquals(ServerInstallRules.Outcome.RESTART, ServerInstallRules.outcome(3010))
        assertEquals(ServerInstallRules.Outcome.CANCELLED, ServerInstallRules.outcome(1223))
        assertEquals(ServerInstallRules.Outcome.CANCELLED, ServerInstallRules.outcome(1602))
        assertEquals(ServerInstallRules.Outcome.FAILED, ServerInstallRules.outcome(1603))

        for ((code, phase) in listOf(3010 to ServerInstallState.Phase.RESTART, 1223 to ServerInstallState.Phase.CANCELLED)) {
            val installer = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = serve(list(sha(msi))), keys = TestReleaseKey.KEYS, run = { code })
            installer.install()
            assertEquals(phase, installer.state.value.phase)
        }
        val failing = ServerInstaller(os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = serve(list(sha(msi))), keys = TestReleaseKey.KEYS, run = { 1603 })
        failing.install()
        assertEquals(ServerInstallState.Phase.FAILED, failing.state.value.phase)
        assertEquals(ServerInstallState.Failure.INSTALLER, failing.state.value.failure)
        assertNotNull("the install log is named for the failure", failing.state.value.log)
    }

    @Test fun theCommandCarriesThePathAsOneQuotedArgument() {
        val folder = File(tmp.root, "O'Brien Pc\\Kelo\\server-install")
        val msiPath = File(folder, "kelo-server-1.0.3.msi")
        val script = decodedScript(ServerInstallRules.command(msiPath, File(folder, "logs\\install-1.0.3.log")))
        // the one quote in the name is doubled inside a PowerShell literal, and the path sits inside msiexec's own quotes
        assertTrue(script.contains("'\"" + msiPath.absolutePath.replace("'", "''") + "\"'"))
        assertTrue(script.contains("Start-Process -FilePath msiexec.exe -Verb RunAs"))
        assertTrue(script.contains("catch { exit 1223 }"))
    }

    @Test fun aVersionThatIsNotPlainIsNeverAFileName() {
        assertEquals("kelo-server-1.0.3.msi", ServerInstallRules.msiName("1.0.3"))
        assertNull(ServerInstallRules.msiName("1.0.3; del C:\\x"))
        assertNull(ServerInstallRules.msiName("..\\..\\x"))
    }

    @Test fun onlyWindowsOffersTheFeature() {
        assertTrue(ServerInstallRules.available(DesktopPlatform.Os.WINDOWS))
        assertFalse(ServerInstallRules.available(DesktopPlatform.Os.MAC))
        assertFalse(ServerInstallRules.available(DesktopPlatform.Os.LINUX))
    }

    @Test fun installedMeansTheServerFolderIsThere() {
        assertTrue(ServerInstallRules.installed(tmp.root))
        assertFalse(ServerInstallRules.installed(File(tmp.root, "Kelo Server")))
    }

    @Test fun theWaitEndsAfterItsTimeAndTheInstallStillCounts() = runBlocking {
        var looks = 0
        val installer = ServerInstaller(
            os = DesktopPlatform.Os.WINDOWS, dir = tmp.root, base = serve(list(sha(msi))), keys = TestReleaseKey.KEYS,
            run = { 0 }, healthy = { looks++; false }, waitMs = 5, everyMs = 1,
        )
        installer.install()
        assertTrue("the server was looked at until the wait ran out", looks >= 1)
        assertEquals(ServerInstallState.Phase.DONE, installer.state.value.phase)
    }
}
