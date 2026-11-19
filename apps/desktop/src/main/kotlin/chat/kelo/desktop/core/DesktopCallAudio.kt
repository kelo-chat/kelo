package chat.kelo.desktop.core

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.voice.RecordingBarRules
import chat.kelo.app.voice.Resample
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import javax.sound.sampled.AudioFormat
import javax.sound.sampled.AudioSystem
import javax.sound.sampled.DataLine
import javax.sound.sampled.SourceDataLine
import javax.sound.sampled.TargetDataLine
import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.sqrt

/**
 * Desktop platform implementation of [CallAudio] (brief §6.2).
 * Hardware echo cancellation and speaker switching are not present on desktop.
 */
class DesktopCallAudio : CallAudio {
    override val echoCancel: Boolean = false
    override val hasSpeakerSwitch: Boolean = false
    override var speakerOn: Boolean = true

    override var onInterrupted: (() -> Unit)? = null

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    // Mic recording state
    private val recordLock = Any()
    private var recordLine: TargetDataLine? = null
    private var reader: Thread? = null
    private var recordRate = 16000
    @Volatile private var recording = false

    // Playback state
    private val playLock = Any()
    private var activeLine: SourceDataLine? = null
    @Volatile private var playbackStopped = false

    // Chime state
    private var chimeJob: Job? = null
    private var chimeLine: SourceDataLine? = null

    override fun startMic(onChunk: (ByteArray) -> Unit): Boolean {
        synchronized(recordLock) {
            stopMicLocked()
            val (l, used) = RATES.firstNotNullOfOrNull { r -> openTargetLine(r)?.let { it to r } } ?: return false
            recordLine = l
            recordRate = used
            recording = true

            val bufferBytes = maxOf(FRAME_16K, FRAME_16K * used / 16000)
            reader = Thread({
                val buf = ByteArray(bufferBytes)
                while (recording) {
                    val n = l.read(buf, 0, buf.size)
                    if (n <= 0) break
                    val pcm = Resample.to16k(buf.copyOf(n - n % 2), recordRate)
                    onChunk(pcm)
                }
            }, "desktop-call-mic").apply { isDaemon = true; start() }
            return true
        }
    }

    override fun stopMic() {
        synchronized(recordLock) {
            stopMicLocked()
        }
    }

    private fun stopMicLocked() {
        recording = false
        reader?.let { runCatching { it.join(1000) } }
        reader = null
        recordLine?.let {
            runCatching { it.stop() }
            runCatching { it.close() }
        }
        recordLine = null
    }

    private fun targetFormat(sampleRate: Int) = AudioFormat(sampleRate.toFloat(), 16, 1, true, false)

    private fun supportedTarget(sampleRate: Int): Boolean =
        runCatching { AudioSystem.isLineSupported(DataLine.Info(TargetDataLine::class.java, targetFormat(sampleRate))) }.getOrDefault(false)

    private fun openTargetLine(sampleRate: Int): TargetDataLine? = runCatching {
        if (!supportedTarget(sampleRate)) return@runCatching null
        val format = targetFormat(sampleRate)
        (AudioSystem.getLine(DataLine.Info(TargetDataLine::class.java, format)) as TargetDataLine).also {
            it.open(format, FRAME_16K * sampleRate / 16000 * 4)
            it.start()
        }
    }.getOrNull()

    override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit): Unit = withContext(Dispatchers.IO) {
        playbackStopped = false
        val format = AudioFormat(sampleRate.toFloat(), 16, 1, true, false)
        val line = runCatching { AudioSystem.getSourceDataLine(format) }.getOrNull() ?: return@withContext

        val pieceBytes = ((sampleRate * 20L) / 1000L).toInt() * 2
        val bufSize = maxOf(line.bufferSize, pieceBytes * 4)

        try {
            line.open(format, bufSize)
            line.start()
            synchronized(playLock) {
                activeLine = line
            }

            var offset = 0
            while (offset < pcm16le.size && !playbackStopped) {
                val len = minOf(pieceBytes, pcm16le.size - offset)
                val written = line.write(pcm16le, offset, len)
                if (written > 0) {
                    var sumSq = 0.0
                    val samples = written / 2
                    for (i in 0 until written step 2) {
                        val s = (((pcm16le[offset + i].toInt() and 0xFF) or (pcm16le[offset + i + 1].toInt() shl 8)).toShort().toDouble()) / 32768.0
                        sumSq += s * s
                    }
                    val rms = if (samples > 0) sqrt(sumSq / samples).toFloat() else 0f
                    val level = RecordingBarRules.level(rms)
                    onLevel(level)
                    offset += written
                } else {
                    break
                }
            }
            if (!playbackStopped) {
                line.drain()
            }
        } finally {
            onLevel(0f)
            synchronized(playLock) {
                runCatching { line.stop() }
                runCatching { line.close() }
                if (activeLine === line) activeLine = null
            }
        }
    }

    override fun stopPlayback() {
        playbackStopped = true
        synchronized(playLock) {
            activeLine?.let {
                runCatching { it.stop() }
                runCatching { it.flush() }
            }
        }
    }

    override fun chime(on: Boolean) {
        chimeJob?.cancel()
        chimeJob = null
        synchronized(playLock) {
            chimeLine?.let {
                runCatching { it.stop() }
                runCatching { it.close() }
            }
            chimeLine = null
        }

        if (!on) return

        chimeJob = scope.launch(Dispatchers.IO) {
            val sampleRate = 16000
            val pcm = generateChimePcm(sampleRate)
            val format = AudioFormat(sampleRate.toFloat(), 16, 1, true, false)
            val line = runCatching { AudioSystem.getSourceDataLine(format) }.getOrNull() ?: return@launch
            synchronized(playLock) {
                chimeLine = line
            }
            try {
                line.open(format, pcm.size * 2)
                line.start()
                while (isActive) {
                    line.write(pcm, 0, pcm.size)
                    line.drain()
                    delay(1640)
                }
            } finally {
                runCatching { line.stop() }
                runCatching { line.close() }
            }
        }
    }

    override fun release() {
        stopMic()
        stopPlayback()
        chime(false)
        scope.cancel()
    }

    companion object {
        private val RATES = listOf(16000, 48000, 44100)
        private const val FRAME_16K = 3200

        /**
         * Generates the 2-note sine chime (660 Hz then 880 Hz, 180 ms each, 20 ms fades, total 360 ms)
         * as 16-bit mono little-endian PCM audio.
         */
        fun generateChimePcm(sampleRate: Int = 16000): ByteArray {
            val note1Ms = 180
            val note2Ms = 180
            val fadeMs = 20
            val note1Samples = (sampleRate * note1Ms) / 1000
            val note2Samples = (sampleRate * note2Ms) / 1000
            val fadeSamples = (sampleRate * fadeMs) / 1000
            val totalSamples = note1Samples + note2Samples
            val out = ByteArray(totalSamples * 2)

            val amplitude = 18000.0

            // Note 1: 660 Hz
            for (i in 0 until note1Samples) {
                val env = when {
                    i < fadeSamples -> i.toDouble() / fadeSamples
                    i > note1Samples - fadeSamples -> (note1Samples - 1 - i).toDouble() / fadeSamples
                    else -> 1.0
                }
                val sample = (sin(2.0 * PI * 660.0 * i / sampleRate) * env * amplitude).toInt().coerceIn(-32768, 32767).toShort()
                out[i * 2] = (sample.toInt() and 0xFF).toByte()
                out[i * 2 + 1] = ((sample.toInt() shr 8) and 0xFF).toByte()
            }

            // Note 2: 880 Hz
            val offset = note1Samples
            for (i in 0 until note2Samples) {
                val env = when {
                    i < fadeSamples -> i.toDouble() / fadeSamples
                    i > note2Samples - fadeSamples -> (note2Samples - 1 - i).toDouble() / fadeSamples
                    else -> 1.0
                }
                val sample = (sin(2.0 * PI * 880.0 * i / sampleRate) * env * amplitude).toInt().coerceIn(-32768, 32767).toShort()
                val sampleIdx = offset + i
                out[sampleIdx * 2] = (sample.toInt() and 0xFF).toByte()
                out[sampleIdx * 2 + 1] = ((sample.toInt() shr 8) and 0xFF).toByte()
            }

            return out
        }
    }
}
