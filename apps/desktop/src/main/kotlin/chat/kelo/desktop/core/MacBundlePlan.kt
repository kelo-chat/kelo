package chat.kelo.desktop.core

import chat.kelo.desktop.core.MacServerRules.ServiceMode
import chat.kelo.desktop.core.ServerInstallRules.Outcome
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.io.File
import java.net.InetAddress
import java.net.ServerSocket
import java.util.concurrent.TimeUnit

/** A finished command: its exit code and the last part of its output (the rest is in the log file). */
data class ProcessResult(val exit: Int, val output: String)

/**
 * The Mac side of the server install (I/O): the port check and the downgrade check before anything changes, the root stage through the
 * one administrator prompt, the user stage (the VM and Kelo), the setup page, and Remove. Every process goes through [run], so the
 * tests and the rules stay separate from the system; the pure rules are in MacServerRules.
 */
class MacBundlePlan(
    /** Runs a command with extra environment; its output goes to [log] as it comes (live) and back as [ProcessResult.output]. */
    private val run: suspend (List<String>, Map<String, String>, File) -> ProcessResult = MacBundlePlan::runCommand,
    private val portFree: (Int) -> Boolean = MacBundlePlan::portFreeOnLoopback,
    private val home: File = MacHardware.home(),
    private val userName: String = System.getProperty("user.name").orEmpty(),
    private val uid: Int = MacHardware.uidOf(System.getProperty("user.name").orEmpty()),
    private val openUrl: (String) -> Unit = { Browser.open(it) },
    private val mode: ServiceMode = MacServerRules.SERVICE_MODE,
    private val installRoot: File = File(MacServerRules.INSTALL_ROOT),
    private val daemonPlist: File = File(MacServerRules.DAEMON_PLIST),
    private val setupPageCommand: List<String> = MacServerRules.setupPageCommand(),
    private val setupPageTimeoutMs: Long = 60_000,
    private val size: () -> MacServerRules.VmSize = { MacServerRules.vmSize(MacHardware.memoryBytes(), MacHardware.cores()) },
) : ServerInstallPlan {
    override val row: String = MacServerRules.ROW
    override val extension: String = MacServerRules.EXTENSION
    override fun artifactName(version: String): String? = MacServerRules.bundleName(version)
    override val canRemove: Boolean = true

    private val agentPlist: File get() = File(home, "Library/LaunchAgents/${MacServerRules.AGENT_PLIST_NAME}")

    /** The program and a job's plist are both there. */
    override fun installed(): Boolean =
        File(installRoot, "current/version.txt").isFile && (daemonPlist.isFile || agentPlist.isFile)

    /** Both ports must be free (PORT_BUSY names the port), and the offered version must not be older than the installed one. */
    override suspend fun preflight(build: LatestBuild): ServerInstallRefusal? {
        for (port in listOf(MacServerRules.serverPort(), MacServerRules.setupPort())) {
            if (!portFree(port)) return ServerInstallRefusal(ServerInstallState.Failure.PORT_BUSY, port)
        }
        val installedVersion = File(installRoot, "current/version.txt").takeIf { it.isFile }?.readText()?.trim()
        if (installedVersion != null && MacServerRules.isDowngrade(installedVersion, build.version)) {
            return ServerInstallRefusal(ServerInstallState.Failure.DOWNGRADE)
        }
        return null
    }

    /** The root stage (one administrator prompt), then the job's user step for agent mode, then the VM and Kelo as the user. */
    override suspend fun install(artifact: File, build: LatestBuild, log: File): Outcome {
        val root = run(
            MacServerRules.adminCommand(
                MacServerRules.ROOT_SCRIPT,
                listOf(artifact.absolutePath, build.sha256, build.version, userName, if (mode == ServiceMode.DAEMON) "daemon" else "agent"),
            ),
            emptyMap(), log,
        )
        val rootOutcome = MacServerRules.adminOutcome(root.exit, root.output)
        if (rootOutcome != Outcome.DONE) return rootOutcome
        if (mode == ServiceMode.AGENT) {
            val boot = run(listOf("/bin/launchctl", "bootstrap", "gui/$uid", agentPlist.absolutePath), emptyMap(), log)
            if (boot.exit != 0) return Outcome.FAILED
        }
        val prepare = withTimeoutOrNull(PREPARE_LIMIT_MS) {
            run(
                MacServerRules.setupCommand(size()),
                MacServerRules.limaEnvironment(home, MacServerRules.serverPort(), MacServerRules.setupPort()),
                log,
            )
        } ?: return Outcome.FAILED
        return if (prepare.exit == 0) Outcome.DONE else Outcome.FAILED
    }

    /** The guided setup page opens (its own link, or the server's address when the link does not come). */
    override suspend fun afterInstall() {
        SetupPageSession(
            command = setupPageCommand,
            env = MacServerRules.limaEnvironment(home, MacServerRules.serverPort(), MacServerRules.setupPort()),
            openUrl = openUrl,
            timeoutMs = setupPageTimeoutMs,
        ).open()
    }

    /** kelo-server-admin remove (or the same steps inline when the program is gone). Data only with [deleteData]. */
    override suspend fun remove(deleteData: Boolean, log: File): Outcome {
        val result = run(
            MacServerRules.adminCommand(MacServerRules.REMOVE_SCRIPT, listOf(userName, if (deleteData) "1" else "0")),
            emptyMap(), log,
        )
        return MacServerRules.adminOutcome(result.exit, result.output)
    }

    companion object {
        /** The user stage waits for the VM and the install; the brief's limit is 45 minutes, and the log stays live meanwhile. */
        const val PREPARE_LIMIT_MS = 45 * 60 * 1000L
        private const val KEPT_OUTPUT = 64 * 1024

        /** A free port is one we can bind on 127.0.0.1 and close at once: nothing is connected to. */
        fun portFreeOnLoopback(port: Int): Boolean =
            runCatching { ServerSocket(port, 1, InetAddress.getByName("127.0.0.1")).close() }.isSuccess

        /** Runs [cmd]: its output goes to [log] as it is written, and the last part is returned for the outcome check. */
        suspend fun runCommand(cmd: List<String>, env: Map<String, String>, log: File): ProcessResult = withContext(Dispatchers.IO) {
            log.parentFile?.mkdirs()
            val builder = ProcessBuilder(cmd).redirectErrorStream(true)
            builder.environment().putAll(env)
            val process = builder.start()
            val kept = StringBuilder()
            log.appendText("$ ${cmd.first()} (${cmd.size} arguments)\n")
            process.inputStream.bufferedReader().useLines { lines ->
                lines.forEach { line ->
                    log.appendText(line + "\n")
                    kept.append(line).append('\n')
                    if (kept.length > KEPT_OUTPUT) kept.delete(0, kept.length - KEPT_OUTPUT)
                }
            }
            process.waitFor(TimeUnit.DAYS.toSeconds(1), TimeUnit.SECONDS)
            ProcessResult(process.exitValue(), kept.toString())
        }
    }
}
