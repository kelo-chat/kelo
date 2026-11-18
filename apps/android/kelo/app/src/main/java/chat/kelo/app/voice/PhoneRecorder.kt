package chat.kelo.app.voice

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import androidx.core.content.ContextCompat
import chat.kelo.app.platform.AudioRecorder
import java.io.ByteArrayOutputStream

/**
 * The phone's microphone for server voice input: 16 kHz mono PCM16 kept in memory. Opens 16 kHz first; if the phone refuses that
 * (rare), 48 kHz, and [Resample] brings each piece down to 16 kHz before it is kept or passed on.
 */
class PhoneRecorder(private val context: Context) : AudioRecorder {
    private val lock = Any()
    private var record: AudioRecord? = null
    private var reader: Thread? = null
    private var rate = 16_000
    @Volatile private var running = false
    private var kept = ByteArrayOutputStream()

    override val available: Boolean
        get() = context.packageManager.hasSystemFeature(PackageManager.FEATURE_MICROPHONE)

    override fun start(onChunk: (ByteArray) -> Unit): Boolean {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return false
        val opened = open(16_000) ?: open(48_000) ?: return false
        synchronized(lock) { kept = ByteArrayOutputStream() }
        record = opened.first
        rate = opened.second
        running = true
        val r = opened.first
        val frame = FRAME_BYTES * rate / 16_000
        reader = Thread({
            val buf = ByteArray(frame)
            while (running) {
                val n = r.read(buf, 0, buf.size)
                if (n <= 0) break
                val pcm = Resample.to16k(buf.copyOf(n - n % 2), rate)
                synchronized(lock) { kept.write(pcm) }
                onChunk(pcm)
            }
        }, "kelo-voice-record").apply { start() }
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
        record?.let { runCatching { it.stop() }; runCatching { it.release() } }
        record = null
    }

    private fun open(sampleRate: Int): Pair<AudioRecord, Int>? {
        val min = AudioRecord.getMinBufferSize(sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        if (min <= 0) return null
        val size = maxOf(min, FRAME_BYTES * sampleRate / 16_000 * 4)
        val r = try {
            AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, size)
        } catch (_: IllegalArgumentException) {
            return null
        }
        if (r.state != AudioRecord.STATE_INITIALIZED) {
            r.release()
            return null
        }
        r.startRecording()
        return r to sampleRate
    }

    private companion object {
        /** 100 ms of 16 kHz mono PCM16. */
        const val FRAME_BYTES = 3_200
    }
}
