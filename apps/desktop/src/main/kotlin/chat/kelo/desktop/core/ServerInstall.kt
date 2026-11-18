package chat.kelo.desktop.core

import chat.kelo.app.update.ReleaseKeys
import chat.kelo.app.update.ReleaseSignature
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.Base64
import java.util.concurrent.TimeUnit

/**
 * The pure rules of "Use this PC as the Kelo server" (Windows only): which latest.json row is the server's MSI, the msiexec exit
 * codes, the command that runs it, where the server answers. No file or process is touched here.
 */
object ServerInstallRules {
    /**
     * The latest.json key of the Kelo SERVER's Windows MSI. "windows-msi" is the desktop app's own installer and is never used here.
     * Until the operator publishes this row, the feature says the server is not available yet.
     */
    const val ROW = "windows-server-msi"
    private val SHA = Regex("^[0-9a-f]{64}$")
    private val FILE = Regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")

    /** This row only: a complete, signed (with [keys]) row whose file is an .msi. Null otherwise (no fallback to any other row). */
    fun pick(all: Map<String, LatestBuild>, keys: Map<String, String> = ReleaseKeys.ALL): LatestBuild? =
        pickSigned(all, ROW, ".msi", keys)

    /** The same for any server row: [row] must be signed for its own platform name and its file must end with [extension]. */
    fun pickSigned(all: Map<String, LatestBuild>, row: String, extension: String, keys: Map<String, String> = ReleaseKeys.ALL): LatestBuild? {
        val b = all[row] ?: return null
        val ok = b.version.isNotBlank() && b.size > 0 && SHA.matches(b.sha256) && FILE.matches(b.file) &&
            b.file.lowercase().endsWith(extension) &&
            ReleaseSignature.verify(row, b.file, b.version, b.code, b.size, b.sha256, b.commit, b.key, b.sig, keys)
        return b.takeIf { ok }
    }
    /** The Kelo server inside the WSL 2 distro is reachable on this PC at 127.0.0.1:8080 (docs/native-server.md; the setup script's fallback). */
    const val SERVER_PORT = 8080
    const val HEALTH_WAIT_MS = 5 * 60 * 1000L
    const val HEALTH_EVERY_MS = 3_000L
    private val VERSION = Regex("^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$")

    /** What an install step ended with (the MSI's exit code on Windows, the administrator step's result on Mac). */
    enum class Outcome { DONE, RESTART, CANCELLED, FAILED }

    /** msiexec: 0 installed, 3010 restart needed (setup resumes after sign-in), 1602 / 1223 cancelled (UAC or the MSI), else failed. */
    fun outcome(exit: Int): Outcome = when (exit) {
        0 -> Outcome.DONE
        3010 -> Outcome.RESTART
        1602, 1223 -> Outcome.CANCELLED
        else -> Outcome.FAILED
    }

    /** The file name the download is saved under; null for a version that is not a plain version string (never a path). */
    fun msiName(version: String): String? = if (VERSION.matches(version)) "kelo-server-$version.msi" else null

    /** A PowerShell single-quoted literal: nothing inside is expanded, and the one quote character is doubled. */
    fun psLiteral(s: String): String = "'" + s.replace("'", "''") + "'"

    /**
     * The PowerShell script: msiexec started elevated (Windows asks once), `/qn` (no msiexec window), a log next to the installer.
     * Every msiexec argument is its own list element; the two paths are quoted for msiexec and then as PowerShell literals. A refused
     * UAC prompt makes Start-Process throw, which is exit 1223 (cancelled).
     */
    fun script(msi: File, log: File): String {
        val args = listOf("/i", "\"${msi.absolutePath}\"", "/qn", "/norestart", "/l*v", "\"${log.absolutePath}\"")
        val list = args.joinToString(",") { psLiteral(it) }
        return "try { \$p = Start-Process -FilePath msiexec.exe -Verb RunAs -Wait -PassThru -ArgumentList @($list); exit \$p.ExitCode } " +
            "catch { exit 1223 }"
    }

    /** powershell.exe with -EncodedCommand: no command-line quoting to get wrong, whatever the user's folder name is. */
    fun command(msi: File, log: File): List<String> {
        val encoded = Base64.getEncoder().encodeToString(script(msi, log).toByteArray(Charsets.UTF_16LE))
        return listOf("powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded)
    }

    /** Where the MSI puts the server: "Program Files\Kelo Server". */
    fun programFilesDir(): File = File(System.getenv("ProgramFiles")?.takeIf { it.isNotBlank() } ?: "C:\\Program Files", "Kelo Server")

    fun installed(folder: File = programFilesDir()): Boolean = folder.isDirectory

    /** %LOCALAPPDATA%\Kelo\server-install (Windows) or ~/Library/Application Support/Kelo/server-install (Mac): the download and its log. */
    fun defaultDir(os: DesktopPlatform.Os = DesktopPlatform.os): File {
        if (os == DesktopPlatform.Os.MAC) {
            return File(File(File(System.getProperty("user.home"), "Library/Application Support"), "Kelo"), "server-install")
        }
        val local = System.getenv("LOCALAPPDATA")?.takeIf { it.isNotBlank() } ?: (System.getProperty("user.home") + "\\AppData\\Local")
        return File(File(local, "Kelo"), "server-install")
    }

    fun healthUrl(port: Int = SERVER_PORT): String = "http://127.0.0.1:$port/healthz"

    /** The starting server's answer, a short call; false on any failure. */
    suspend fun serverHealthy(client: OkHttpClient): Boolean = withContext(Dispatchers.IO) {
        runCatching {
            client.newBuilder().callTimeout(5, TimeUnit.SECONDS).build()
                .newCall(Request.Builder().url(healthUrl()).build()).execute().use { it.isSuccessful }
        }.getOrDefault(false)
    }

    /** Windows always; a Mac from macOS 14 on, Apple silicon only (the Mac bundle, MacServerRules). */
    fun available(
        os: DesktopPlatform.Os = DesktopPlatform.os,
        macMajor: Int = MacServerRules.macMajor(MacHardware.osVersion()),
        isArm64: Boolean = MacHardware.isArm64(),
    ): Boolean = os == DesktopPlatform.Os.WINDOWS || (os == DesktopPlatform.Os.MAC && macMajor >= 14 && isArm64)

    /** Runs the command and returns its exit code. */
    suspend fun runProcess(cmd: List<String>): Int = withContext(Dispatchers.IO) {
        ProcessBuilder(cmd).redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.DISCARD).start().waitFor()
    }
}

/** A reason to stop before anything is downloaded or changed: [failure] (PORT_BUSY with the [port], or DOWNGRADE). */
data class ServerInstallRefusal(val failure: ServerInstallState.Failure, val port: Int? = null)

/**
 * What differs between the systems: which latest.json row is the server, the file's extension, how it is installed, whether it is
 * there, and how it is removed. The installer (ServerInstaller) does everything else the same way on every system.
 */
interface ServerInstallPlan {
    /** The latest.json key of the server's installer. */
    val row: String
    /** The file extension the row must carry (".msi", ".zip"). */
    val extension: String
    /** The download's file name for [version], or null for a version that is not a plain version string. */
    fun artifactName(version: String): String?
    fun installed(): Boolean
    /** Why the install must not start (checked before the download), or null. */
    suspend fun preflight(build: LatestBuild): ServerInstallRefusal? = null
    /** Installs the checked [artifact]; output goes to [log]. */
    suspend fun install(artifact: File, build: LatestBuild, log: File): ServerInstallRules.Outcome
    /** After a DONE install: the wait for the server, or the setup page. */
    suspend fun afterInstall()
    /** Only the Mac offers Remove. */
    val canRemove: Boolean get() = false
    suspend fun remove(deleteData: Boolean, log: File): ServerInstallRules.Outcome = ServerInstallRules.Outcome.FAILED
}

/** Windows: the signed MSI, run elevated with msiexec; the MSI's own user step opens the setup page. */
class WindowsMsiPlan(
    private val run: suspend (List<String>) -> Int = ServerInstallRules::runProcess,
    private val healthy: suspend () -> Boolean,
    private val waitMs: Long = ServerInstallRules.HEALTH_WAIT_MS,
    private val everyMs: Long = ServerInstallRules.HEALTH_EVERY_MS,
) : ServerInstallPlan {
    override val row: String = ServerInstallRules.ROW
    override val extension: String = ".msi"
    override fun artifactName(version: String): String? = ServerInstallRules.msiName(version)
    override fun installed(): Boolean = ServerInstallRules.installed()

    override suspend fun install(artifact: File, build: LatestBuild, log: File): ServerInstallRules.Outcome =
        ServerInstallRules.outcome(run(ServerInstallRules.command(artifact, log)))

    /** The server answers, or the wait runs out (then the setup page still opens; the install itself succeeded). */
    override suspend fun afterInstall() {
        var waited = 0L
        while (waited < waitMs) {
            if (healthy()) return
            delay(everyMs)
            waited += everyMs
        }
    }
}

/** The plan for a system ([os]), or null where the feature is not offered. The Mac plan runs the real commands (MacBundlePlan). */
object ServerInstallPlans {
    fun forOs(
        os: DesktopPlatform.Os,
        run: suspend (List<String>) -> Int,
        healthy: suspend () -> Boolean,
        waitMs: Long,
        everyMs: Long,
    ): ServerInstallPlan? = when (os) {
        DesktopPlatform.Os.WINDOWS -> WindowsMsiPlan(run, healthy, waitMs, everyMs)
        DesktopPlatform.Os.MAC -> MacBundlePlan()
        DesktopPlatform.Os.LINUX -> null
    }
}

/** Where the install is: what the page shows. */
data class ServerInstallState(
    val phase: Phase = Phase.IDLE,
    val failure: Failure? = null,
    /** 0..1 while downloading */
    val progress: Float = 0f,
    /** The install log (msiexec's /l*v file), when msiexec ran and failed */
    val log: String? = null,
    val version: String? = null,
    /** A server row is published and checked: the install button may be offered. */
    val ready: Boolean = false,
    /** The port that is in use (PORT_BUSY), for the message. */
    val port: Int? = null,
) {
    enum class Phase { IDLE, CHECKING, DOWNLOADING, VERIFYING, INSTALLING, STARTING, DONE, RESTART, CANCELLED, FAILED, UNAVAILABLE, REMOVING, REMOVED }
    enum class Failure { DOWNLOAD, MISMATCH, INSTALLER, PORT_BUSY, DOWNGRADE }

    val busy: Boolean get() = phase in BUSY

    private companion object {
        val BUSY = setOf(Phase.CHECKING, Phase.DOWNLOADING, Phase.VERIFYING, Phase.INSTALLING, Phase.STARTING, Phase.REMOVING)
    }
}

/**
 * "Use this PC / this Mac as the Kelo server": the app fetches the server's installer itself (a file an app downloads carries no
 * Mark of the Web, so no SmartScreen or Gatekeeper warning from the download), checks its signature row and sha256, installs it
 * through the system's plan and waits until the server answers. Nothing starts without the user's button. The plan decides what
 * differs (the MSI on Windows; the Mac bundle later).
 */
class ServerInstaller(
    private val dir: File,
    private val base: String = UpdateRules.base(System.getProperty("kelo.update.base"), System.getenv("KELO_UPDATE_BASE")),
    private val client: OkHttpClient = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS).readTimeout(120, TimeUnit.SECONDS).build(),
    /** The release keys the app accepts (tests pass their own). */
    private val keys: Map<String, String> = ReleaseKeys.ALL,
    private val run: suspend (List<String>) -> Int = ServerInstallRules::runProcess,
    private val healthy: suspend () -> Boolean = { ServerInstallRules.serverHealthy(client) },
    private val waitMs: Long = ServerInstallRules.HEALTH_WAIT_MS,
    private val everyMs: Long = ServerInstallRules.HEALTH_EVERY_MS,
    private val os: DesktopPlatform.Os = DesktopPlatform.os,
    /** The system's plan; null = the default for [os] (no plan = the feature is not offered). */
    plan: ServerInstallPlan? = null,
    private val available: Boolean = ServerInstallRules.available(os),
) {
    private val plan: ServerInstallPlan? =
        if (!available) null else plan ?: ServerInstallPlans.forOs(os, run, healthy, waitMs, everyMs)
    private val _state = MutableStateFlow(ServerInstallState())
    val state: StateFlow<ServerInstallState> = _state.asStateFlow()

    /** The signed server row of latest.json, or null when none is published or none carries a valid signature for this app's keys. */
    suspend fun latest(): LatestBuild? = withContext(Dispatchers.IO) {
        val current = plan ?: return@withContext null
        val body = client.newCall(
            Request.Builder().url("$base/download/latest.json").header("User-Agent", UpdateRules.userAgent(ReleaseInfo.VERSION, os)).build(),
        ).execute().use { r -> if (!r.isSuccessful) error("HTTP ${r.code}") else r.body?.string().orEmpty() }
        ServerInstallRules.pickSigned(UpdateRules.parse(body), current.row, current.extension, keys)
    }

    /** Checks the server row when the page opens: no row means UNAVAILABLE (no download is offered). */
    suspend fun prepare() {
        if (_state.value.busy || plan == null) return
        set(ServerInstallState.Phase.CHECKING)
        val build = runCatching { latest() }.getOrElse { return fail(ServerInstallState.Failure.DOWNLOAD, null) }
        if (build == null) {
            _state.value = ServerInstallState(ServerInstallState.Phase.UNAVAILABLE)
        } else {
            _state.value = ServerInstallState(ServerInstallState.Phase.IDLE, version = build.version, ready = true)
        }
    }

    /** The server is installed on this computer (the plan's check). */
    fun installed(): Boolean = plan?.installed() ?: false

    /** Whether the plan offers Remove (the Mac only). */
    val canRemove: Boolean get() = plan?.canRemove ?: false

    /** The whole flow: check, refuse early, download, verify, install, wait for the server. A second call while one runs does nothing. */
    suspend fun install() {
        val current = plan ?: return
        if (_state.value.busy) return
        set(ServerInstallState.Phase.CHECKING)
        val build = runCatching { latest() }.getOrElse { return fail(ServerInstallState.Failure.DOWNLOAD, null) }
            ?: return unavailable()
        val version = build.version
        val name = current.artifactName(version) ?: return unavailable()
        current.preflight(build)?.let { return fail(it.failure, version, port = it.port) }
        val url = UpdateRules.downloadUrl(base, build) ?: return fail(ServerInstallState.Failure.DOWNLOAD, version)

        set(ServerInstallState.Phase.DOWNLOADING, version = version)
        dir.mkdirs()
        val target = File(dir, name)
        val part = File(dir, "$name.part")
        val got = withContext(Dispatchers.IO) { runCatching { download(url, part, build.size, version) } }
        if (got.isFailure) {
            part.delete()
            return fail(ServerInstallState.Failure.DOWNLOAD, version)
        }
        set(ServerInstallState.Phase.VERIFYING, progress = 1f, version = version)
        val ok = withContext(Dispatchers.IO) { part.length() == build.size && UpdateRules.verify(part, build.sha256) }
        if (!ok) {
            part.delete()
            return fail(ServerInstallState.Failure.MISMATCH, version)
        }
        target.delete()
        if (!part.renameTo(target)) part.copyTo(target, overwrite = true).also { part.delete() }

        set(ServerInstallState.Phase.INSTALLING, version = version)
        val logDir = File(dir, "logs").apply { mkdirs() }
        val log = File(logDir, "install-$version.log")
        when (current.install(target, build, log)) {
            ServerInstallRules.Outcome.DONE -> {
                set(ServerInstallState.Phase.STARTING, version = version)
                current.afterInstall()
                set(ServerInstallState.Phase.DONE, version = version)
            }
            ServerInstallRules.Outcome.RESTART -> set(ServerInstallState.Phase.RESTART, version = version)
            ServerInstallRules.Outcome.CANCELLED -> set(ServerInstallState.Phase.CANCELLED, version = version)
            ServerInstallRules.Outcome.FAILED -> fail(ServerInstallState.Failure.INSTALLER, version, log)
        }
    }

    /** Removes the server (only where the plan offers it). Chats, keys and files stay unless [deleteData]. */
    suspend fun remove(deleteData: Boolean) {
        val current = plan ?: return
        if (_state.value.busy || !current.canRemove) return
        set(ServerInstallState.Phase.REMOVING)
        val log = File(File(dir, "logs").apply { mkdirs() }, "remove.log")
        when (current.remove(deleteData, log)) {
            ServerInstallRules.Outcome.DONE -> _state.value = ServerInstallState(ServerInstallState.Phase.REMOVED, ready = _state.value.ready)
            ServerInstallRules.Outcome.CANCELLED -> _state.value = ServerInstallState(ServerInstallState.Phase.CANCELLED, ready = _state.value.ready)
            else -> fail(ServerInstallState.Failure.INSTALLER, _state.value.version, log)
        }
    }

    private fun download(url: String, part: File, size: Long, version: String) {
        val req = Request.Builder().url(url).header("User-Agent", UpdateRules.userAgent(ReleaseInfo.VERSION, os)).build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) error("HTTP ${r.code}")
            val body = r.body ?: error("empty")
            val total = body.contentLength().takeIf { it > 0 } ?: size
            var done = 0L
            var shown = -1
            part.outputStream().use { out ->
                body.byteStream().use { input ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        done += n
                        val pct = ((done * 100) / total).toInt().coerceIn(0, 100)
                        if (pct != shown) {
                            shown = pct
                            set(ServerInstallState.Phase.DOWNLOADING, progress = pct / 100f, version = version)
                        }
                    }
                }
            }
        }
    }

    private fun set(phase: ServerInstallState.Phase, progress: Float = 0f, version: String? = _state.value.version) {
        _state.value = ServerInstallState(phase = phase, progress = progress, version = version, ready = _state.value.ready)
    }

    private fun unavailable() {
        _state.value = ServerInstallState(ServerInstallState.Phase.UNAVAILABLE)
    }

    private fun fail(f: ServerInstallState.Failure, version: String?, log: File? = null, port: Int? = null) {
        _state.value = ServerInstallState(
            ServerInstallState.Phase.FAILED, f, 0f, log?.absolutePath, version, ready = _state.value.ready, port = port,
        )
    }
}
