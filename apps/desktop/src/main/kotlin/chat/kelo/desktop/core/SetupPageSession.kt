package chat.kelo.desktop.core

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException

/**
 * Starts the guided setup page (the Mac's `kelo-server-setup setup-page`, which runs `kelo setup --web` inside the VM), reads the one
 * link it prints, and opens that link in the browser. The process stays alive until the page ends or the app quits. Nothing it
 * prints is logged, and the link carries a one-time token: it is only ever handed to [openUrl]. When no link comes in time the
 * server's own address is opened instead (the setup page then answers there, or the user sees the server).
 */
class SetupPageSession(
    private val command: List<String>,
    private val env: Map<String, String>,
    private val openUrl: (String) -> Unit,
    private val fallbackUrl: String = "http://localhost:${MacServerRules.serverPort()}/",
    private val timeoutMs: Long = 60_000,
    private val setupPort: Int = MacServerRules.setupPort(),
    private val start: (List<String>, Map<String, String>) -> Process = ::startPage,
) {
    @Volatile private var process: Process? = null

    /** Starts the page, opens its link (or the fallback), and returns the address that was opened. */
    suspend fun open(): String {
        val page = start(command, env)
        process = page
        Runtime.getRuntime().addShutdownHook(Thread { page.destroy() })
        val link = withContext(Dispatchers.IO) { linkWithin(page) }
        val url = link ?: fallbackUrl
        openUrl(url)
        return url
    }

    /** Ends the page's process (the app is quitting). */
    fun close() {
        process?.destroy()
    }

    private fun linkWithin(page: Process): String? {
        val reading = CompletableFuture.supplyAsync { readLink(page) }
        return try {
            reading.get(timeoutMs, TimeUnit.MILLISECONDS)
        } catch (_: TimeoutException) {
            null
        }
    }

    /** The first line that is the setup link; then the rest of the output is drained so the page never blocks on a full pipe. */
    private fun readLink(page: Process): String? {
        val reader = page.inputStream.bufferedReader()
        while (true) {
            val line = reader.readLine() ?: return null
            val link = MacServerRules.setupUrl(line.trim(), setupPort)
            if (link != null) {
                Thread({ reader.copyTo(java.io.Writer.nullWriter()) }, "kelo-setup-page-drain").apply { isDaemon = true; start() }
                return link
            }
        }
    }

    companion object {
        /** The page's process: stdout to us, stderr dropped (it can hold nothing that is shown or logged). */
        fun startPage(command: List<String>, env: Map<String, String>): Process {
            val builder = ProcessBuilder(command)
                .redirectError(ProcessBuilder.Redirect.DISCARD)
                .redirectInput(ProcessBuilder.Redirect.from(java.io.File("/dev/null")))
            builder.environment().putAll(env)
            return builder.start()
        }
    }
}
