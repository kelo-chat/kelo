package chat.kelo.desktop.core

import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.chat.SpokenText
import java.util.Locale
import java.util.concurrent.TimeUnit

/** Which voice of macOS's `say -v ?` list reads a language (pure, so it can be tested). */
object MacVoices {
    private val LINE = Regex("""^(.+?)\s+([A-Za-z]{2,3})[_-]([A-Za-z0-9]+)\s+#""")

    /** The voice for [language] (a BCP-47 tag such as "ko" or "pt-BR"): the first of the same language and region, else the first of the same language; null when there is none. */
    fun pick(listing: String, language: String): String? {
        val wantLang = language.substringBefore('-').substringBefore('_').lowercase(Locale.ROOT)
        val wantRegion = language.replace('_', '-').substringAfter('-', "").uppercase(Locale.ROOT)
        val voices = listing.lineSequence().mapNotNull { LINE.find(it.trim()) }
            .map { Triple(it.groupValues[1].trim(), it.groupValues[2].lowercase(Locale.ROOT), it.groupValues[3].uppercase(Locale.ROOT)) }
            .filter { it.second == wantLang }.toList()
        return (voices.firstOrNull { wantRegion.isNotEmpty() && it.third == wantRegion } ?: voices.firstOrNull())?.first
    }
}

/**
 * The computer's own text-to-speech without any added library: macOS `say`, and Windows' System.Speech through PowerShell. Other systems
 * have none that is always there, so [create] gives null and "Read aloud" is not offered. [onDone] runs after the text ends or the
 * program fails; [stop] and a new [speak] stop the running one without calling its [onDone].
 */
class DesktopSpeechEngine private constructor(private val mac: Boolean) : SpeechEngine {
    private var process: Process? = null
    @Volatile private var turn = 0
    private val macListing: String by lazy {
        runCatching {
            val p = ProcessBuilder("say", "-v", "?").redirectErrorStream(true).start()
            val out = p.inputStream.readBytes().toString(Charsets.UTF_8)
            p.waitFor(5, TimeUnit.SECONDS)
            out
        }.getOrDefault("")
    }

    @Synchronized
    override fun speak(text: String, language: String, onDone: () -> Unit) {
        stop()
        val mine = ++turn
        Thread {
            runCatching { if (mac) sayMac(text, language, mine) else sayWindows(text, language, mine) }
            java.awt.EventQueue.invokeLater { if (mine == turn) onDone() }
        }.apply { isDaemon = true; name = "kelo-read-aloud" }.start()
    }

    private fun sayMac(text: String, language: String, mine: Int): Boolean {
        val voice = MacVoices.pick(macListing, language)
        val command = buildList { add("say"); if (voice != null) { add("-v"); add(voice) } }
        val p = ProcessBuilder(command).redirectErrorStream(true).start()
        synchronized(this) { if (mine != turn) { p.destroy(); return false }; process = p }
        p.outputStream.use { it.write(text.toByteArray(Charsets.UTF_8)) }
        p.inputStream.readBytes()
        return p.waitFor() == 0
    }

    private fun sayWindows(text: String, language: String, mine: Int): Boolean {
        // the text and language go in through environment variables, so nothing in them can be taken for a command
        for (piece in SpokenText.chunks(text, max = 8000)) {
            val builder = ProcessBuilder(
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", WINDOWS_SCRIPT,
            ).redirectErrorStream(true)
            builder.environment()["KELO_SPEECH_TEXT"] = piece
            builder.environment()["KELO_SPEECH_LANG"] = language
            val p = builder.start()
            synchronized(this) { if (mine != turn) { p.destroy(); return false }; process = p }
            p.outputStream.close()
            p.inputStream.readBytes()
            if (p.waitFor() != 0) return false
            if (mine != turn) return false
        }
        return true
    }

    @Synchronized
    override fun stop() {
        turn++
        process?.let { runCatching { it.destroy() } }
        process = null
    }

    companion object {
        private const val WINDOWS_SCRIPT = "Add-Type -AssemblyName System.Speech; " +
            "\$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; " +
            "\$l = \$env:KELO_SPEECH_LANG; " +
            "\$v = \$s.GetInstalledVoices() | Where-Object { \$_.Enabled -and \$_.VoiceInfo.Culture.Name -like (\$l.Split('-')[0] + '*') } | Select-Object -First 1; " +
            "if (\$v) { \$s.SelectVoice(\$v.VoiceInfo.Name) }; " +
            "\$s.Speak(\$env:KELO_SPEECH_TEXT)"

        /** The engine of this computer, or null when it has no system text-to-speech to use (anything but macOS and Windows). */
        fun create(osName: String = System.getProperty("os.name").orEmpty()): DesktopSpeechEngine? {
            val os = osName.lowercase(Locale.ROOT)
            return when {
                os.contains("mac") -> DesktopSpeechEngine(mac = true)
                os.contains("win") -> DesktopSpeechEngine(mac = false)
                else -> null
            }
        }
    }
}
