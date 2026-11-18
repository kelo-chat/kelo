package chat.kelo.desktop.core

import chat.kelo.desktop.core.ServerInstallRules.Outcome
import java.io.File

/**
 * The pure rules of "Use this Mac as the Kelo server" (Apple silicon, macOS 14 or later): the bundle's row and names, the ports, the
 * VM's size, the version and user rules, the administrator command and its two fixed scripts, and the setup link's shape. No file,
 * process or prompt is touched here (MacBundlePlan does that). The scripts are written with "§" for "$" so Kotlin does not read
 * the shell's variables; [ROOT_SCRIPT] and [REMOVE_SCRIPT] are the only code that runs as the administrator.
 */
object MacServerRules {
    /** The latest.json key of the Kelo server's Mac bundle. The app's own Mac row ("mac", a .dmg) is never this. */
    const val ROW = "mac-server-bundle"
    const val EXTENSION = ".zip"
    const val INSTALL_ROOT = "/Library/Application Support/Kelo Server"
    const val CURRENT = "$INSTALL_ROOT/current"
    const val DAEMON_PLIST = "/Library/LaunchDaemons/chat.kelo.server.plist"
    const val AGENT_PLIST_NAME = "chat.kelo.server.plist"
    const val LABEL = "chat.kelo.server"
    const val VM_NAME = "kelo"
    /** The guided setup page, as the VM serves it on this Mac (the guest and the Mac use the same number unless a test moves it). */
    const val SETUP_PORT = 8081
    const val TEAM_ID = "P6GD4DM9C5"

    enum class ServiceMode { DAEMON, AGENT }

    /** A LaunchDaemon (starts at boot) until measurement M1 says the VM does not start from one (then AGENT). */
    val SERVICE_MODE: ServiceMode = ServiceMode.DAEMON

    private const val GIB = 1024L * 1024 * 1024
    private val VERSION = Regex("^[0-9][0-9A-Za-z.-]{0,39}$")
    private val USER = Regex("^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
    private val DOTTED = Regex("^[0-9]+(\\.[0-9]+)*$")

    /** The server's port on this Mac (8080), and the setup page's (8081). The tests may move them with system properties. */
    fun serverPort(): Int =
        System.getProperty("kelo.server.port")?.toIntOrNull()?.takeIf { it in 1..65535 } ?: ServerInstallRules.SERVER_PORT

    fun setupPort(): Int =
        System.getProperty("kelo.server.setupPort")?.toIntOrNull()?.takeIf { it in 1..65535 } ?: SETUP_PORT

    /** The VM's folder: ~/.kelo-server/vm (short, no spaces: Unix socket paths are limited to 104 bytes). */
    fun limaHome(home: File): File = File(File(home, ".kelo-server"), "vm")

    /** The bundle's file name for [version], or null for a version that is not a plain version string (never a path). */
    fun bundleName(version: String): String? = if (VERSION.matches(version)) "kelo-server-$version$EXTENSION" else null

    /** "26.3.1" to 26; "10.15.7" to 10; anything else to 0. */
    fun macMajor(osVersion: String): Int = Regex("^(\\d+)").find(osVersion.trim())?.groupValues?.get(1)?.toIntOrNull() ?: 0

    data class VmSize(val memoryGiB: Int, val cpus: Int)

    /** Half the host's memory, 4 to 8 GiB; half its cores, 2 to 4. */
    fun vmSize(memoryBytes: Long, cores: Int): VmSize =
        VmSize(memoryGiB = (memoryBytes / 2 / GIB).coerceIn(4L, 8L).toInt(), cpus = (cores / 2).coerceIn(2, 4))

    /** True when [offered] is an older version than [installed]; both are dotted numbers (a missing part counts as 0). */
    fun isDowngrade(installed: String, offered: String): Boolean = compareDotted(offered, installed) < 0

    private fun compareDotted(a: String, b: String): Int {
        if (!DOTTED.matches(a) || !DOTTED.matches(b)) return 0
        val x = a.split('.').map { it.toLong() }
        val y = b.split('.').map { it.toLong() }
        for (i in 0 until maxOf(x.size, y.size)) {
            val d = (x.getOrElse(i) { 0L }).compareTo(y.getOrElse(i) { 0L })
            if (d != 0) return d
        }
        return 0
    }

    /** A short name that is not root and that the admin scripts accept. */
    fun validUserName(name: String): Boolean = USER.matches(name) && name != "root"

    /**
     * The command that runs [script] as the administrator, with [args] as separate arguments: osascript gets the script and each
     * argument as its own argv item and quotes them with `quoted form of`, so nothing is pasted into a string. The arguments reach the
     * script as $1, $2, ... (after the name "kelo-root").
     */
    fun adminCommand(script: String, args: List<String>): List<String> {
        val parts = mutableListOf("\"/bin/sh -c \" & quoted form of (item 1 of argv) & \" kelo-root\"")
        args.indices.forEach { parts += "\" \" & quoted form of (item ${it + 2} of argv)" }
        val shell = "do shell script " + parts.joinToString(" & ") + " with administrator privileges"
        return listOf("/usr/bin/osascript", "-e", "on run argv", "-e", shell, "-e", "end run", "--", script) + args
    }

    /** 0 is done; the user's "Cancel" at the password dialog is error -128; anything else failed. */
    fun adminOutcome(exit: Int, output: String): Outcome = when {
        exit == 0 -> Outcome.DONE
        output.contains("(-128)") -> Outcome.CANCELLED
        else -> Outcome.FAILED
    }

    /** The user stage's commands: the app runs them as the user, with [limaEnvironment]. */
    fun setupCommand(size: VmSize): List<String> =
        listOf("$CURRENT/bin/kelo-server-setup", "prepare", "--memory", "${size.memoryGiB}", "--cpus", "${size.cpus}")

    fun setupPageCommand(): List<String> = listOf("$CURRENT/bin/kelo-server-setup", "setup-page")

    fun limaEnvironment(home: File, serverPort: Int, setupPort: Int): Map<String, String> = mapOf(
        "HOME" to home.absolutePath,
        "LIMA_HOME" to limaHome(home).absolutePath,
        "KELO_SERVER_PORT" to "$serverPort",
        "KELO_SETUP_PORT" to "$setupPort",
        "PATH" to "/usr/bin:/bin:/usr/sbin:/sbin",
    )

    /** The one link the setup page prints: loopback only, the setup port, a token in the fragment. Anything else is refused. */
    fun setupUrl(line: String, port: Int = setupPort()): String? =
        line.takeIf { Regex("^http://127\\.0\\.0\\.1:$port/#t=[A-Za-z0-9_-]{16,}$").matches(it) }

    /**
     * The root stage (runs as root after the one password prompt; arguments: $1 zip, $2 sha256, $3 version, $4 user, $5 daemon|agent).
     * Order: validate every input (exit 64), copy the zip into a stage folder and check the copy's sha256 (65), unpack it and check
     * limactl's signature against the Kelo team (66), check version.txt (67), then switch `current` and run kelo-server-admin, which
     * must be the last command.
     */
    val ROOT_SCRIPT: String = """
set -eu
fail() { echo "kelo-root: §1" >&2; exit "§2"; }
printf '%s' "§3" | grep -Eq '^[0-9][0-9A-Za-z.-]{0,39}${'$'}' || fail "bad version" 64
printf '%s' "§2" | grep -Eq '^[0-9a-f]{64}${'$'}' || fail "bad checksum" 64
printf '%s' "§4" | grep -Eq '^[A-Za-z_][A-Za-z0-9_.-]{0,31}${'$'}' || fail "bad user" 64
[ "§4" != root ] || fail "bad user" 64
uid="§(id -u "§4" 2>/dev/null)" || fail "bad user" 64
[ "§uid" -ge 501 ] || fail "bad user" 64
case "§5" in daemon|agent) ;; *) fail "bad mode" 64 ;; esac
[ -f "§1" ] && [ ! -L "§1" ] || fail "the bundle is not a regular file" 64
root="/Library/Application Support/Kelo Server"
install -d -o root -g wheel -m 755 "§root" "§root/versions"
stage="§(mktemp -d "§root/versions/.stage.XXXXXX")"
trap 'rm -rf "§stage"' EXIT
cp "§1" "§stage/kelo-server.zip"
actual="§(shasum -a 256 "§stage/kelo-server.zip" | awk '{print §1}')"
[ "§actual" = "§2" ] || fail "checksum mismatch" 65
ditto -x -k "§stage/kelo-server.zip" "§stage/unpacked"
tree="§stage/unpacked/kelo-server"
codesign --verify --strict -R='anchor apple generic and certificate leaf[subject.OU] = "P6GD4DM9C5"' "§tree/bin/limactl" 2>/dev/null || fail "limactl is not signed by Kelo" 66
[ "§(cat "§tree/version.txt")" = "§3" ] || fail "version mismatch" 67
chown -R root:wheel "§tree"
chmod -R go-w "§tree"
rm -rf "§root/versions/§3"
mv "§tree" "§root/versions/§3"
ln -sfhn "versions/§3" "§root/current"
rm -rf "§stage"
exec "§root/current/bin/kelo-server-admin" install --user "§4" --mode "§5"
""".replace('§', '$')

    /**
     * The remove step (arguments: $1 user, $2 1 to delete the chats, keys and files too, else 0). It runs kelo-server-admin when the
     * program folder is there; otherwise it does the same steps inline.
     */
    val REMOVE_SCRIPT: String = """
set -eu
root="/Library/Application Support/Kelo Server"
printf '%s' "§1" | grep -Eq '^[A-Za-z_][A-Za-z0-9_.-]{0,31}${'$'}' || exit 64
[ "§1" != root ] || exit 64
case "§2" in 0|1) ;; *) exit 64 ;; esac
if [ -x "§root/current/bin/kelo-server-admin" ]; then
    if [ "§2" = 1 ]; then
        exec "§root/current/bin/kelo-server-admin" remove --delete-data --user "§1"
    fi
    exec "§root/current/bin/kelo-server-admin" remove --user "§1"
fi
uid="§(id -u "§1")"
launchctl bootout system/chat.kelo.server 2>/dev/null || true
launchctl bootout "gui/§uid/chat.kelo.server" 2>/dev/null || true
rm -f /Library/LaunchDaemons/chat.kelo.server.plist
home="§(dscl . -read "/Users/§1" NFSHomeDirectory | awk '{print §2}')"
rm -f "§home/Library/LaunchAgents/chat.kelo.server.plist"
if [ "§2" = 1 ] && [ -d "§home/.kelo-server" ] && [ ! -L "§home/.kelo-server" ]; then rm -rf -- "§home/.kelo-server"; fi
rm -rf -- "§root"
""".replace('§', '$')
}
