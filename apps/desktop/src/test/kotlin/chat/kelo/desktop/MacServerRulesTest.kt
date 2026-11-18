package chat.kelo.desktop

import chat.kelo.desktop.core.DesktopPlatform
import chat.kelo.desktop.core.MacServerRules
import chat.kelo.desktop.core.ServerInstallRules
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The pure rules of "Use this Mac as the Kelo server" (core/MacServerRules.kt): which systems are offered, the version and size
 * rules, the administrator command (every value its own argument, the AppleScript constant), its outcome, the root stage's order of
 * checks, the setup link's shape and the user name rule. No file, process or prompt is touched.
 */
class MacServerRulesTest {
    /** Mac-only rules (POSIX paths, /bin/sh): the Windows CI runner skips them; the Mac build and this Mac run them. */
    @org.junit.Before fun notOnWindows() = org.junit.Assume.assumeFalse(System.getProperty("os.name").startsWith("Windows"))

    @After fun clearOverrides() {
        System.clearProperty("kelo.server.port")
        System.clearProperty("kelo.server.setupPort")
    }

    @Test fun theServerIsOfferedOnWindowsAndOnAppleSiliconMacsFromMacOs14() {
        assertTrue(ServerInstallRules.available(DesktopPlatform.Os.WINDOWS, 0, false))
        for (major in listOf(13, 14, 26)) {
            for (arm in listOf(true, false)) {
                val expected = major >= 14 && arm
                assertEquals("macOS $major, arm64=$arm", expected, ServerInstallRules.available(DesktopPlatform.Os.MAC, major, arm))
            }
        }
        assertFalse(ServerInstallRules.available(DesktopPlatform.Os.LINUX, 26, true))
    }

    @Test fun theMacMajorIsTheFirstNumber() {
        assertEquals(26, MacServerRules.macMajor("26.3.1"))
        assertEquals(10, MacServerRules.macMajor("10.15.7"))
        assertEquals(14, MacServerRules.macMajor("14"))
        assertEquals(0, MacServerRules.macMajor("garbage"))
        assertEquals(0, MacServerRules.macMajor(""))
    }

    @Test fun theVmSizeIsHalfTheHostWithClamps() {
        val gib = 1024L * 1024 * 1024
        assertEquals(MacServerRules.VmSize(memoryGiB = 4, cpus = 2), MacServerRules.vmSize(2 * gib, 1))
        assertEquals(MacServerRules.VmSize(memoryGiB = 4, cpus = 4), MacServerRules.vmSize(8 * gib, 8))
        assertEquals(MacServerRules.VmSize(memoryGiB = 8, cpus = 4), MacServerRules.vmSize(32 * gib, 16))
        assertEquals(MacServerRules.VmSize(memoryGiB = 8, cpus = 2), MacServerRules.vmSize(64 * gib, 4))
    }

    @Test fun theBundleNameIsAPlainVersionOnly() {
        assertEquals("kelo-server-0.10.4.zip", MacServerRules.bundleName("0.10.4"))
        assertNull(MacServerRules.bundleName("../x"))
        assertNull(MacServerRules.bundleName("0.10.4; rm -rf /"))
        assertNull(MacServerRules.bundleName(""))
    }

    @Test fun aDowngradeIsComparedDotByDot() {
        assertTrue(MacServerRules.isDowngrade(installed = "0.10.4", offered = "0.10.3"))
        assertFalse(MacServerRules.isDowngrade(installed = "0.10.4", offered = "0.10.10"))
        assertFalse(MacServerRules.isDowngrade(installed = "0.10.4", offered = "0.10.4"))
        assertFalse(MacServerRules.isDowngrade(installed = "0.9.9", offered = "0.10"))
        assertTrue(MacServerRules.isDowngrade(installed = "1.0.1", offered = "1.0"))
    }

    @Test fun theUserNameRule() {
        assertTrue(MacServerRules.validUserName("alice"))
        assertTrue(MacServerRules.validUserName("_svc.user-1"))
        assertFalse(MacServerRules.validUserName("root"))
        assertFalse(MacServerRules.validUserName("../x"))
        assertFalse(MacServerRules.validUserName(""))
        assertFalse(MacServerRules.validUserName("a b"))
        assertFalse(MacServerRules.validUserName("a".repeat(33)))
    }

    @Test fun theAdministratorCommandKeepsEveryValueAsOneArgument() {
        val nasty = "/Users/o'brien \"x\"/Downloads/\$(rm -rf ~)/kelo\nserver.zip"
        val cmd = MacServerRules.adminCommand(MacServerRules.ROOT_SCRIPT, listOf(nasty, "a".repeat(64), "0.10.4", "alice", "daemon"))
        assertEquals("/usr/bin/osascript", cmd[0])
        val at = cmd.indexOf("--")
        assertTrue("the arguments come after --", at > 0)
        assertEquals(MacServerRules.ROOT_SCRIPT, cmd[at + 1])
        assertEquals(nasty, cmd[at + 2])
        assertEquals(listOf("a".repeat(64), "0.10.4", "alice", "daemon"), cmd.subList(at + 3, cmd.size))
    }

    @Test fun theAppleScriptTextIsTheSameWhateverTheArguments() {
        val a = MacServerRules.adminCommand(MacServerRules.REMOVE_SCRIPT, listOf("one"))
        val b = MacServerRules.adminCommand(MacServerRules.REMOVE_SCRIPT, listOf("two\"; do shell script \"x\""))
        assertEquals(a.subList(0, a.indexOf("--")), b.subList(0, b.indexOf("--")))
        val text = a.subList(0, a.indexOf("--")).joinToString("\n")
        assertTrue(text.contains("with administrator privileges"))
        assertTrue(text.contains("quoted form of"))
    }

    @Test fun theAdministratorOutcome() {
        assertEquals(ServerInstallRules.Outcome.DONE, MacServerRules.adminOutcome(0, ""))
        assertEquals(ServerInstallRules.Outcome.CANCELLED, MacServerRules.adminOutcome(1, "execution error: User canceled. (-128)"))
        assertEquals(ServerInstallRules.Outcome.FAILED, MacServerRules.adminOutcome(1, "kelo-root: checksum mismatch"))
        assertEquals(ServerInstallRules.Outcome.FAILED, MacServerRules.adminOutcome(66, ""))
    }

    @Test fun theRootStageChecksTheCopyInTheRightOrder() {
        val s = MacServerRules.ROOT_SCRIPT
        fun at(needle: String): Int = s.indexOf(needle).also { assertTrue("missing: $needle", it >= 0) }
        val copy = at("cp \"\$1\"")
        val sum = at("shasum -a 256")
        val unpack = at("ditto -x -k")
        val signature = at("-R='anchor apple generic and certificate leaf[subject.OU] = \"P6GD4DM9C5\"'")
        val link = at("ln -sfhn")
        val last = at("exec \"\$root/current/bin/kelo-server-admin\" install")
        assertTrue("copy before the checksum", copy < sum)
        assertTrue("checksum before unpacking", sum < unpack)
        assertTrue("signature before the switch", signature < link)
        assertTrue("the admin tool runs last", link < last)
        assertEquals("the admin command is the last line", last, s.trimEnd().lastIndexOf("exec \"\$root/current/bin/kelo-server-admin\""))
        assertTrue("the checksum is of the copy, not of the user's file", s.contains("shasum -a 256 \"\$stage/"))
        assertTrue("the checks exit 64, 65, 66, 67", listOf(64, 65, 66, 67).all { s.contains("$it)") || s.contains(" $it\n") || s.contains("$it ") })
        assertTrue("the version pattern", s.contains("^[0-9][0-9A-Za-z.-]{0,39}\$"))
        assertTrue("the sha pattern", s.contains("^[0-9a-f]{64}\$"))
        assertTrue("the user is not root and is at least uid 501", s.contains("-ge 501") && s.contains("root"))
        assertTrue("a symlink is refused", s.contains("! -L"))
        assertFalse("nothing in the root stage calls sudo or the user's home", s.contains("sudo"))
    }

    @Test fun theRemoveScriptCallsTheAdminToolOrDoesTheSameSteps() {
        val s = MacServerRules.REMOVE_SCRIPT
        assertTrue(s.contains("kelo-server-admin\" remove"))
        assertTrue(s.contains("--delete-data"))
        assertTrue(s.contains("chat.kelo.server"))
    }

    @Test fun theSetupLinkIsOnlyTheLoopbackSetupPageWithAToken() {
        val token = "a1_B2-c3_D4-e5_F6"
        assertEquals("http://127.0.0.1:8081/#t=$token", MacServerRules.setupUrl("http://127.0.0.1:8081/#t=$token"))
        assertNull(MacServerRules.setupUrl("http://localhost:8081/#t=$token"))
        assertNull(MacServerRules.setupUrl("http://127.0.0.1:8080/#t=$token"))
        assertNull(MacServerRules.setupUrl("https://127.0.0.1:8081/#t=$token"))
        assertNull(MacServerRules.setupUrl("http://127.0.0.1:8081/?t=$token"))
        assertNull(MacServerRules.setupUrl("http://127.0.0.1:8081/#t=$token and more"))
        assertNull(MacServerRules.setupUrl("http://127.0.0.1:8081/#t=short"))
        System.setProperty("kelo.server.setupPort", "18081")
        assertEquals("http://127.0.0.1:18081/#t=$token", MacServerRules.setupUrl("http://127.0.0.1:18081/#t=$token"))
    }

    @Test fun thePortsAreTheDefaultsUnlessTheTestsOverrideThem() {
        assertEquals(8080, MacServerRules.serverPort())
        assertEquals(8081, MacServerRules.setupPort())
        System.setProperty("kelo.server.port", "18080")
        System.setProperty("kelo.server.setupPort", "18081")
        assertEquals(18080, MacServerRules.serverPort())
        assertEquals(18081, MacServerRules.setupPort())
    }

    @Test fun theLimaEnvironmentAndTheCommandsUseTheBundleAndTheVmFolder() {
        val home = java.io.File("/Users/someone")
        val env = MacServerRules.limaEnvironment(home, 8080, 8081)
        assertEquals("/Users/someone/.kelo-server/vm", env["LIMA_HOME"])
        assertEquals("8080", env["KELO_SERVER_PORT"])
        assertEquals("8081", env["KELO_SETUP_PORT"])
        val setup = MacServerRules.setupCommand(MacServerRules.VmSize(4, 2))
        assertEquals(listOf("prepare", "--memory", "4", "--cpus", "2"), setup.drop(1))
        assertTrue(setup[0].endsWith("/current/bin/kelo-server-setup"))
        assertTrue(MacServerRules.setupPageCommand()[0].endsWith("/current/bin/kelo-server-setup"))
        assertEquals("setup-page", MacServerRules.setupPageCommand()[1])
    }

    @Test fun theConstantsAreTheOnesTheBriefNames() {
        assertEquals("mac-server-bundle", MacServerRules.ROW)
        assertEquals(".zip", MacServerRules.EXTENSION)
        assertEquals("chat.kelo.server", MacServerRules.LABEL)
        assertEquals("kelo", MacServerRules.VM_NAME)
        assertEquals("P6GD4DM9C5", MacServerRules.TEAM_ID)
        assertEquals("/Library/LaunchDaemons/chat.kelo.server.plist", MacServerRules.DAEMON_PLIST)
        assertEquals(MacServerRules.ServiceMode.DAEMON, MacServerRules.SERVICE_MODE)
    }

    @Test fun theScriptsParseAsShell() {
        for (script in listOf(MacServerRules.ROOT_SCRIPT, MacServerRules.REMOVE_SCRIPT)) {
            val file = java.io.File.createTempFile("kelo-script", ".sh")
            try {
                file.writeText(script)
                val p = ProcessBuilder("/bin/sh", "-n", file.absolutePath).redirectErrorStream(true).start()
                val out = p.inputStream.bufferedReader().readText()
                assertEquals(out, 0, p.waitFor())
            } finally {
                file.delete()
            }
        }
    }
}
