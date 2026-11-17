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
    fun pick(all: Map<String, LatestBuild>, keys: Map<String, String> = ReleaseKeys.ALL): LatestBuild? {
        val b = all[ROW] ?: return null
        val ok = b.version.isNotBlank() && b.size > 0 && SHA.matches(b.sha256) && FILE.matches(b.file) &&
            b.file.lowercase().endsWith(".msi") &&
            ReleaseSignature.verify(ROW, b.file, b.version, b.code, b.size, b.sha256, b.commit, b.key, b.sig, keys)
        return b.takeIf { ok }
    }
    /** The Kelo server inside the WSL 2 distro is reachable on this PC at 127.0.0.1:8080 (docs/native-server.md; the setup script's fallback). */
    const val SERVER_PORT = 8080
    const val HEALTH_WAIT_MS = 5 * 60 * 1000L
    const val HEALTH_EVERY_MS = 3_000L
    private val VERSION = Regex("^[0-9A-Za-z][0-9A-Za-z.+-]{0,39}$")

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

    fun available(os: DesktopPlatform.Os = DesktopPlatform.os): Boolean = os == DesktopPlatform.Os.WINDOWS

    /** Where the MSI puts the server: "Program Files\Kelo Server". */
    fun programFilesDir(): File = File(System.getenv("ProgramFiles")?.takeIf { it.isNotBlank() } ?: "C:\\Program Files", "Kelo Server")

    fun installed(folder: File = programFilesDir()): Boolean = folder.isDirectory

    /** %LOCALAPPDATA%\Kelo\server-install: the downloaded MSI and its install log. */
    fun defaultDir(): File {
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

    /** Runs the command and returns its exit code. */
    suspend fun runProcess(cmd: List<String>): Int = withContext(Dispatchers.IO) {
        ProcessBuilder(cmd).redirectErrorStream(true).redirectOutput(ProcessBuilder.Redirect.DISCARD).start().waitFor()
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
) {
    enum class Phase { IDLE, CHECKING, DOWNLOADING, VERIFYING, INSTALLING, STARTING, DONE, RESTART, CANCELLED, FAILED, UNAVAILABLE }
    enum class Failure { DOWNLOAD, MISMATCH, INSTALLER }

    val busy: Boolean get() = phase in BUSY

    private companion object {
        val BUSY = setOf(Phase.CHECKING, Phase.DOWNLOADING, Phase.VERIFYING, Phase.INSTALLING, Phase.STARTING)
    }
}

/**
 * "Use this PC as the Kelo server": the app fetches the server's MSI itself (a file an app downloads carries no Mark of the Web, so
 * no SmartScreen warning), checks its signature row and sha256, runs it elevated and waits until the server answers. Nothing starts
 * without the user's button. The MSI's own user step opens the setup page in the browser, so this class does not open it again.
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
) {
    private val _state = MutableStateFlow(ServerInstallState())
    val state: StateFlow<ServerInstallState> = _state.asStateFlow()

    /** The signed server row of latest.json, or null when none is published or none carries a valid signature for this app's keys. */
    suspend fun latest(): LatestBuild? = withContext(Dispatchers.IO) {
        val body = client.newCall(
            Request.Builder().url("$base/download/latest.json").header("User-Agent", UpdateRules.userAgent(ReleaseInfo.VERSION, os)).build(),
        ).execute().use { r -> if (!r.isSuccessful) error("HTTP ${r.code}") else r.body?.string().orEmpty() }
        ServerInstallRules.pick(UpdateRules.parse(body), keys)
    }

    /** Checks the server row when the page opens: no row means UNAVAILABLE (no download is offered). */
    suspend fun prepare() {
        if (_state.value.busy || !ServerInstallRules.available(os)) return
        set(ServerInstallState.Phase.CHECKING)
        val build = runCatching { latest() }.getOrElse { return fail(ServerInstallState.Failure.DOWNLOAD, null) }
        if (build == null) {
            _state.value = ServerInstallState(ServerInstallState.Phase.UNAVAILABLE)
        } else {
            _state.value = ServerInstallState(ServerInstallState.Phase.IDLE, version = build.version, ready = true)
        }
    }

    /** This PC has the server installed (the MSI's folder is there). */
    fun installed(): Boolean = ServerInstallRules.installed()

    /** The whole flow: check, download, verify, install, wait for the server. A second call while one runs does nothing. */
    suspend fun install() {
        if (_state.value.busy || !ServerInstallRules.available(os)) return
        set(ServerInstallState.Phase.CHECKING)
        val build = runCatching { latest() }.getOrElse { return fail(ServerInstallState.Failure.DOWNLOAD, null) }
            ?: return unavailable()
        val version = build.version
        val name = ServerInstallRules.msiName(version) ?: return unavailable()
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
        when (ServerInstallRules.outcome(run(ServerInstallRules.command(target, log)))) {
            ServerInstallRules.Outcome.DONE -> {
                set(ServerInstallState.Phase.STARTING, version = version)
                waitForServer()
                set(ServerInstallState.Phase.DONE, version = version)
            }
            ServerInstallRules.Outcome.RESTART -> set(ServerInstallState.Phase.RESTART, version = version)
            ServerInstallRules.Outcome.CANCELLED -> set(ServerInstallState.Phase.CANCELLED, version = version)
            ServerInstallRules.Outcome.FAILED -> fail(ServerInstallState.Failure.INSTALLER, version, log)
        }
    }

    /** The server answers, or the wait runs out (then the setup page still opens; the install itself succeeded). */
    private suspend fun waitForServer() {
        var waited = 0L
        while (waited < waitMs) {
            if (healthy()) return
            delay(everyMs)
            waited += everyMs
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

    private fun fail(f: ServerInstallState.Failure, version: String?, log: File? = null) {
        _state.value = ServerInstallState(ServerInstallState.Phase.FAILED, f, 0f, log?.absolutePath, version, ready = _state.value.ready)
    }
}
