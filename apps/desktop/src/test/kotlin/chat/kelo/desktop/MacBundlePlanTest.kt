package chat.kelo.desktop

import chat.kelo.app.update.ReleaseKeys
import chat.kelo.app.update.TestReleaseKey
import chat.kelo.desktop.core.DesktopPlatform
import chat.kelo.desktop.core.MacBundlePlan
import chat.kelo.desktop.core.MacServerRules
import chat.kelo.desktop.core.ProcessResult
import chat.kelo.desktop.core.ServerInstallRules
import chat.kelo.desktop.core.ServerInstallState
import chat.kelo.desktop.core.ServerInstaller
import chat.kelo.desktop.core.SetupPageSession
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

/**
 * "Use this Mac as the Kelo server" end to end with fakes: only the signed mac-server-bundle .zip row is offered (never the app's
 * .dmg row), a checksum mismatch runs nothing, PORT_BUSY and DOWNGRADE stop before the administrator prompt, a cancelled prompt runs
 * nothing more, a done install runs the user stage with the VM folder and ports, the setup link opens from the page's own process and
 * is in no log, and Remove passes the data choice. Nothing is started: the runner, the ports, the browser are injected.
 */
class MacBundlePlanTest {
    @get:Rule val tmp = TemporaryFolder()
    private var web: MockWebServer? = null

    @After fun stop() {
        web?.shutdown()
    }

    private val zip = ByteArray(90_000) { (it % 211).toByte() }
    private fun sha(b: ByteArray) = MessageDigest.getInstance("SHA-256").digest(b).joinToString("") { "%02x".format(it) }
    private val token = "Ab3_dEf-GhI4jKl5MnOp6"

    /** latest.json with the Mac bundle row ([signed]) and, optionally, the app's own Mac row. */
    private fun list(sha: String, signed: Boolean = true, file: String = "kelo-server-1.0.4.zip", extension: String = "zip"): String {
        val name = file.substringBeforeLast('.') + ".$extension"
        val sig = if (signed) ", " + TestReleaseKey.members("mac-server-bundle", name, "1.0.4", 0, zip.size.toLong(), sha) else ""
        return """{"mac-server-bundle": {"version": "1.0.4", "size": ${zip.size}, "sha256": "$sha", "date": "", "file": "$name", "url": "/download/mac-server-bundle"$sig}}"""
    }

    private fun serve(list: String, file: ByteArray = zip): String {
        val server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse = when {
                request.path == "/download/latest.json" -> MockResponse().setBody(list)
                request.path?.startsWith("/download/mac-server-bundle") == true -> MockResponse().setBody(okio.Buffer().write(file))
                else -> MockResponse().setResponseCode(404)
            }
        }
        server.start()
        web = server
        return server.url("/").toString().trimEnd('/')
    }

    /** The fake runner: records each command; the administrator's answer and the user stage's answer come from the test. */
    private inner class Runner(var admin: ProcessResult = ProcessResult(0, ""), var prepare: Int = 0) {
        val calls = mutableListOf<Pair<List<String>, Map<String, String>>>()
        suspend fun run(cmd: List<String>, env: Map<String, String>, log: File): ProcessResult {
            calls += cmd to env
            log.appendText("fake: ${cmd.first()}\n")
            return when {
                cmd.first() == "/usr/bin/osascript" -> admin
                cmd.contains("prepare") -> ProcessResult(prepare, "")
                else -> ProcessResult(0, "")
            }
        }
        fun adminCalls() = calls.filter { it.first.first() == "/usr/bin/osascript" }
    }

    private fun home(): File = File(tmp.root, "home").apply { mkdirs() }
    private fun installRoot(version: String? = null): File = File(tmp.root, "Kelo Server").apply {
        if (version != null) {
            File(this, "current").mkdirs()
            File(this, "current/version.txt").writeText(version)
        }
    }

    private fun plan(
        runner: Runner,
        free: (Int) -> Boolean = { true },
        opened: MutableList<String> = mutableListOf(),
        root: File = installRoot(),
        setupCommand: List<String> = listOf("sh", "-c", "echo 'http://127.0.0.1:8081/#t=$token'; sleep 5"),
        timeoutMs: Long = 5_000,
    ) = MacBundlePlan(
        run = runner::run,
        portFree = free,
        home = home(),
        userName = "tester",
        uid = 501,
        openUrl = { opened += it },
        mode = MacServerRules.ServiceMode.DAEMON,
        installRoot = root,
        setupPageCommand = setupCommand,
        setupPageTimeoutMs = timeoutMs,
        size = { MacServerRules.VmSize(4, 2) },
    )

    private fun installer(
        plan: chat.kelo.desktop.core.ServerInstallPlan,
        base: String,
        keys: Map<String, String> = TestReleaseKey.KEYS,
    ) = ServerInstaller(
        dir = File(tmp.root, "server-install"), base = base, keys = keys, os = DesktopPlatform.Os.MAC,
        plan = plan, available = true,
    )

    @Test fun onlyTheSignedMacBundleZipIsOffered() = runBlocking {
        val base = serve(list(sha(zip)))
        val runner = Runner()
        assertNotNull("the signed row is picked", installer(plan(runner), base).latest())
        assertNull("an unsigned row is refused", installer(plan(runner), serve(list(sha(zip), signed = false)))
            .latest())
        assertNull("a row signed with a key this app does not carry is refused",
            installer(plan(runner), base, keys = ReleaseKeys.ALL).latest())
        assertNull("a .dmg in the server row is refused", installer(plan(runner), serve(list(sha(zip), extension = "dmg"))).latest())
    }

    @Test fun theAppsOwnMacRowIsNeverTheServer() = runBlocking {
        val dmg = ByteArray(1000) { 3 }
        val app = """{"mac": {"version": "1.0.4", "size": ${dmg.size}, "sha256": "${sha(dmg)}", "date": "", "file": "Kelo-1.0.4.dmg", "url": "/download/mac"${TestReleaseKey.members("mac", "Kelo-1.0.4.dmg", "1.0.4", 0, dmg.size.toLong(), sha(dmg))}}}"""
        val runner = Runner()
        assertNull(installer(plan(runner), serve(app)).latest())
        val installer = installer(plan(runner), serve(app))
        installer.install()
        assertEquals(ServerInstallState.Phase.UNAVAILABLE, installer.state.value.phase)
        assertTrue("nothing runs for the app's row", runner.calls.isEmpty())
    }

    @Test fun aChecksumMismatchDeletesTheFileAndRunsNothing() = runBlocking {
        val other = ByteArray(zip.size) { 7 }
        val runner = Runner()
        val installer = installer(plan(runner), serve(list(sha(zip)), file = other))
        installer.install()
        assertEquals(ServerInstallState.Phase.FAILED, installer.state.value.phase)
        assertEquals(ServerInstallState.Failure.MISMATCH, installer.state.value.failure)
        assertFalse(File(tmp.root, "server-install/kelo-server-1.0.4.zip").exists())
        assertTrue("no command runs from a file that does not check out", runner.calls.isEmpty())
    }

    @Test fun aBusyPortStopsBeforeAnyDownloadOrPrompt() = runBlocking {
        val runner = Runner()
        val installer = installer(plan(runner, free = { it != 8080 }), serve(list(sha(zip))))
        installer.install()
        assertEquals(ServerInstallState.Failure.PORT_BUSY, installer.state.value.failure)
        assertEquals(8080, installer.state.value.port)
        assertTrue(runner.calls.isEmpty())
        assertFalse("nothing is downloaded either", File(tmp.root, "server-install/kelo-server-1.0.4.zip").exists())
    }

    @Test fun aSecondBusyPortIsAlsoRefused() = runBlocking {
        val installer = installer(plan(Runner(), free = { it != 8081 }), serve(list(sha(zip))))
        installer.install()
        assertEquals(ServerInstallState.Failure.PORT_BUSY, installer.state.value.failure)
        assertEquals(8081, installer.state.value.port)
    }

    @Test fun aDowngradeIsRefusedBeforeAnyPrompt() = runBlocking {
        val runner = Runner()
        val installer = installer(plan(runner, root = installRoot("1.0.5")), serve(list(sha(zip))))
        installer.install()
        assertEquals(ServerInstallState.Failure.DOWNGRADE, installer.state.value.failure)
        assertTrue(runner.calls.isEmpty())
    }

    @Test fun aCancelledPromptRunsNothingMore() = runBlocking {
        val runner = Runner(admin = ProcessResult(1, "execution error: User canceled. (-128)"))
        val installer = installer(plan(runner), serve(list(sha(zip))))
        installer.install()
        assertEquals(ServerInstallState.Phase.CANCELLED, installer.state.value.phase)
        assertEquals("only the prompt ran", 1, runner.calls.size)
        assertTrue(runner.calls.none { it.first.contains("prepare") })
    }

    @Test fun aDoneInstallRunsTheRootStageThenTheUserStageAndOpensTheSetupPage() = runBlocking {
        val runner = Runner()
        val opened = mutableListOf<String>()
        val installer = installer(plan(runner, opened = opened), serve(list(sha(zip))))
        installer.install()
        assertEquals(ServerInstallState.Phase.DONE, installer.state.value.phase)

        val admin = runner.adminCalls().single().first
        assertEquals(MacServerRules.ROOT_SCRIPT, admin[admin.indexOf("--") + 1])
        val args = admin.subList(admin.indexOf("--") + 2, admin.size)
        assertEquals(listOf("tester", "daemon"), args.drop(3))
        assertEquals(sha(zip), args[1])
        assertEquals("1.0.4", args[2])

        val prepare = runner.calls.single { it.first.contains("prepare") }
        assertEquals(listOf("prepare", "--memory", "4", "--cpus", "2"), prepare.first.drop(1))
        assertTrue(prepare.second["LIMA_HOME"]!!.endsWith("/.kelo-server/vm"))
        assertEquals("8080", prepare.second["KELO_SERVER_PORT"])
        assertEquals("8081", prepare.second["KELO_SETUP_PORT"])
        assertEquals(listOf("http://127.0.0.1:8081/#t=$token"), opened)
    }

    @Test fun noLinkInTimeOpensTheServerAddress() = runBlocking {
        val opened = mutableListOf<String>()
        val installer = installer(
            plan(Runner(), opened = opened, setupCommand = listOf("sh", "-c", "sleep 3"), timeoutMs = 200),
            serve(list(sha(zip))),
        )
        installer.install()
        assertEquals(listOf("http://localhost:8080/"), opened)
        assertEquals(ServerInstallState.Phase.DONE, installer.state.value.phase)
    }

    @Test fun theSetupTokenIsInNoLogFileAndNoCommandLine() = runBlocking {
        val runner = Runner()
        val opened = mutableListOf<String>()
        val installer = installer(plan(runner, opened = opened), serve(list(sha(zip))))
        installer.install()
        assertEquals(listOf("http://127.0.0.1:8081/#t=$token"), opened)
        tmp.root.walkTopDown().filter { it.isFile }.forEach { file ->
            assertFalse("the token is in ${file.name}", file.readText(Charsets.ISO_8859_1).contains(token))
        }
        assertTrue(runner.calls.none { cmd -> cmd.first.any { it.contains(token) } })
    }

    @Test fun removeWithoutAndWithTheData() = runBlocking {
        val runner = Runner()
        val installer = installer(plan(runner), serve(list(sha(zip))))
        installer.remove(deleteData = false)
        assertEquals(ServerInstallState.Phase.REMOVED, installer.state.value.phase)
        installer.remove(deleteData = true)
        val calls = runner.adminCalls().map { it.first }
        assertEquals(listOf("tester", "0"), calls[0].subList(calls[0].indexOf("--") + 2, calls[0].size))
        assertEquals(listOf("tester", "1"), calls[1].subList(calls[1].indexOf("--") + 2, calls[1].size))
        assertEquals(MacServerRules.REMOVE_SCRIPT, calls[0][calls[0].indexOf("--") + 1])
    }

    @Test fun theMacPlanIsInstalledOnlyWhenTheProgramAndAPlistAreThere() {
        val root = installRoot("1.0.4")
        val p = plan(Runner(), root = root)
        assertFalse("the plist is not there yet", p.installed())
        File(home(), "Library/LaunchAgents").mkdirs()
        File(home(), "Library/LaunchAgents/chat.kelo.server.plist").writeText("<plist/>")
        assertTrue(p.installed())
    }

    @Test fun nothingRunsOnLinuxOrWindowsForTheMacPlan() = runBlocking {
        val runner = Runner()
        val linux = ServerInstaller(dir = File(tmp.root, "x"), base = serve(list(sha(zip))), keys = TestReleaseKey.KEYS, os = DesktopPlatform.Os.LINUX)
        linux.install()
        assertEquals(ServerInstallState.Phase.IDLE, linux.state.value.phase)
        assertTrue(runner.calls.isEmpty())
        assertFalse(ServerInstallRules.available(DesktopPlatform.Os.LINUX, 26, true))
    }

    @Test fun theSetupLinkComesFromThePageProcessOnly() = runBlocking {
        val opened = mutableListOf<String>()
        val session = SetupPageSession(
            command = listOf("sh", "-c", "echo 'not a link'; echo 'http://127.0.0.1:8081/#t=$token'; sleep 5"),
            env = emptyMap(), openUrl = { opened += it }, timeoutMs = 5_000,
        )
        assertEquals("http://127.0.0.1:8081/#t=$token", session.open())
        assertEquals(listOf("http://127.0.0.1:8081/#t=$token"), opened)
        session.close()
    }
}
