package chat.kelo.desktop.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import java.util.Base64
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

/**
 * The Microsoft Store copy's own question: is a newer build of Kelo in the Store? Windows answers it only to a packaged app (StoreContext,
 * WinRT), and the JVM cannot call WinRT, so the app starts Windows PowerShell (present on Windows 10 and 11; the child process keeps the
 * package identity of the app that started it), which asks StoreContext.GetAppAndOptionalStorePackageUpdatesAsync and prints
 * `{"updates": n}`. n > 0 means the Store has an update for Kelo (or for an optional package of it). Any failure, timeout or odd
 * output is no answer: no notice. The answer is kept for six hours ([Last], in the cache folder), across runs of the app.
 */
object StoreUpdates {
    const val EVERY_MS = 6L * 60 * 60 * 1000
    /** The helper is cut after this; a Store that answers slowly gives no notice this time. */
    const val TIMEOUT_MS = 20_000L

    /**
     * The check's script (run with -EncodedCommand, so no quoting reaches the shell). Writes the count as JSON and nothing else.
     * The PowerShell dollar signs are written as ~ here (a dollar sign would start a Kotlin template).
     */
    val SCRIPT: String = """
        Add-Type -AssemblyName System.Runtime.WindowsRuntime
        [void][Windows.Services.Store.StoreContext, Windows.Services.Store, ContentType = WindowsRuntime]
        ~asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { ~_.Name -eq 'AsTask' -and ~_.GetParameters().Count -eq 1 -and ~_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
        Function Await(~WinRtTask, ~ResultType) {
            ~asTask = ~asTaskGeneric.MakeGenericMethod(~ResultType)
            ~netTask = ~asTask.Invoke(~null, @(~WinRtTask))
            ~netTask.Wait(-1) | Out-Null
            ~netTask.Result
        }
        ~context = [Windows.Services.Store.StoreContext]::GetDefault()
        ~updates = Await (~context.GetAppAndOptionalStorePackageUpdatesAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Services.Store.StorePackageUpdate]])
        Write-Output ('{"updates":' + [int]~updates.Count + '}')
    """.trimIndent().replace('~', '$')

    /** The command that runs [SCRIPT] with Windows PowerShell, no profile, no prompt. */
    fun command(): List<String> = listOf(
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", Base64.getEncoder().encodeToString(SCRIPT.toByteArray(Charsets.UTF_16LE)),
    )

    /** The count of `{"updates": n}`, or null: anything else (no JSON, no number, a string, a negative count) is no answer. */
    fun parse(output: String?): Int? {
        val obj = runCatching { Json.parseToJsonElement(output?.trim().orEmpty()) as? JsonObject }.getOrNull() ?: return null
        val n = obj["updates"] as? JsonPrimitive ?: return null
        if (n.isString) return null
        return n.intOrNull?.takeIf { it >= 0 }
    }

    /** The helper's standard output, or null when it could not start, failed, or did not finish within [timeoutMs] (then it is killed). */
    fun run(command: List<String>, timeoutMs: Long = TIMEOUT_MS): String? = runCatching {
        val process = ProcessBuilder(command).redirectError(ProcessBuilder.Redirect.DISCARD).start()
        val output = CompletableFuture.supplyAsync { process.inputStream.bufferedReader().use { it.readText() } }
        try {
            output.get(timeoutMs, TimeUnit.MILLISECONDS).also { process.waitFor(5, TimeUnit.SECONDS) }
        } catch (e: Exception) {
            process.destroyForcibly()
            null
        }
    }.getOrNull()

    /** The last answer and when it came: [updates] is -1 when the check failed. */
    data class Last(val atMs: Long, val updates: Int)

    /** Due for a check: never asked, six hours have passed, or the clock was set back. */
    fun due(last: Last?, nowMs: Long): Boolean = last == null || nowMs - last.atMs >= EVERY_MS || nowMs < last.atMs

    fun encode(last: Last): String = "${last.atMs} ${last.updates}"

    fun decode(text: String?): Last? {
        val parts = text?.trim()?.split(' ') ?: return null
        if (parts.size != 2) return null
        return Last(parts[0].toLongOrNull() ?: return null, parts[1].toIntOrNull() ?: return null)
    }
}
