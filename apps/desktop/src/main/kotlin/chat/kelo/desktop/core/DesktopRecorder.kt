package chat.kelo.desktop.core

import chat.kelo.app.platform.AudioRecorder
import chat.kelo.app.voice.Resample
import java.io.ByteArrayOutputStream
import javax.sound.sampled.AudioFormat
import javax.sound.sampled.AudioSystem
import javax.sound.sampled.DataLine
import javax.sound.sampled.TargetDataLine

/**
 * The computer's microphone for server voice input (javax.sound.sampled): 16 kHz, mono, 16-bit little endian, kept in memory. Tries
 * 16 kHz first, then 48 and 44.1 kHz with [Resample] down to 16 kHz. A system with no line (or no permission) reports [available] =
 * false and [start] = false.
 */
class DesktopRecorder : AudioRecorder {
    private val lock = Any()
    private var line: TargetDataLine? = null
    private var reader: Thread? = null
    private var rate = 16_000
    @Volatile private var running = false
    private var kept = ByteArrayOutputStream()

    override val available: Boolean
        get() = RATES.any { supported(it) }

    override fun start(onChunk: (ByteArray) -> Unit): Boolean {
        val (l, used) = RATES.firstNotNullOfOrNull { r -> open(r)?.let { it to r } } ?: return false
        synchronized(lock) { kept = ByteArrayOutputStream() }
        line = l
        rate = used
        running = true
        val bufferBytes = maxOf(FRAME_16K, FRAME_16K * used / 16_000)
        reader = Thread({
            val buf = ByteArray(bufferBytes)
            while (running) {
                val n = l.read(buf, 0, buf.size)
                if (n <= 0) break
                val pcm = Resample.to16k(buf.copyOf(n - n % 2), rate)
                synchronized(lock) { kept.write(pcm) }
                onChunk(pcm)
            }
        }, "kelo-voice-record").apply { isDaemon = true; start() }
        return true
    }

    override fun stop(): ByteArray {
        finish()
        return synchronized(lock) { kept.toByteArray().also { kept = ByteArrayOutputStream() } }
    }

    override fun cancel() {
        finish()
        synchronized(lock) { kept = ByteArrayOutputStream() }
    }

    private fun finish() {
        running = false
        reader?.let { runCatching { it.join(1_000) } }
        reader = null
        line?.let { runCatching { it.stop() }; runCatching { it.close() } }
        line = null
    }

    private fun format(sampleRate: Int) = AudioFormat(sampleRate.toFloat(), 16, 1, true, false)

    private fun supported(sampleRate: Int): Boolean =
        runCatching { AudioSystem.isLineSupported(DataLine.Info(TargetDataLine::class.java, format(sampleRate))) }.getOrDefault(false)

    private fun open(sampleRate: Int): TargetDataLine? = runCatching {
        if (!supported(sampleRate)) return@runCatching null
        val format = format(sampleRate)
        (AudioSystem.getLine(DataLine.Info(TargetDataLine::class.java, format)) as TargetDataLine).also {
            it.open(format, FRAME_16K * sampleRate / 16_000 * 4)
            it.start()
        }
    }.getOrNull()

    private companion object {
        val RATES = listOf(16_000, 48_000, 44_100)
        /** 100 ms of 16 kHz mono PCM16. */
        const val FRAME_16K = 3_200
    }
}
