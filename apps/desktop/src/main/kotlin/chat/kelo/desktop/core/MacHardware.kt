package chat.kelo.desktop.core

import java.io.File

/**
 * What this Mac is (read once, never changed): Apple silicon, the memory and cores the VM size is taken from, the user's id.
 * Only the reads live here; the rules that use them are in MacServerRules.
 */
object MacHardware {
    private val arm64: Boolean by lazy { sysctl("hw.optional.arm64") == "1" }

    /** Apple silicon. `hw.optional.arm64` is also 1 when the JVM runs under Rosetta, which is what the rule needs. */
    fun isArm64(): Boolean = arm64

    fun osVersion(): String = System.getProperty("os.version").orEmpty()

    fun memoryBytes(): Long =
        (java.lang.management.ManagementFactory.getOperatingSystemMXBean() as? com.sun.management.OperatingSystemMXBean)
            ?.totalMemorySize ?: 0L

    fun cores(): Int = Runtime.getRuntime().availableProcessors()

    /** The numeric id of [user] (`id -u`), or -1. */
    fun uidOf(user: String): Int = runCatching {
        ProcessBuilder("/usr/bin/id", "-u", user).redirectErrorStream(true).start().let { p ->
            val out = p.inputStream.bufferedReader().readText().trim()
            if (p.waitFor() == 0) out.toIntOrNull() ?: -1 else -1
        }
    }.getOrDefault(-1)

    private fun sysctl(key: String): String = runCatching {
        ProcessBuilder("/usr/sbin/sysctl", "-n", key).redirectErrorStream(true).start().let { p ->
            val out = p.inputStream.bufferedReader().readText().trim()
            if (p.waitFor() == 0) out else ""
        }
    }.getOrDefault("")

    /** The folder the user's Library lives in (the home folder). */
    fun home(): File = File(System.getProperty("user.home"))
}
