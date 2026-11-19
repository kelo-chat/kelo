package chat.kelo.app.call

import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

/** 16 kHz mono PCM16 test signals: a tone of a level and quiet noise (the same bodies as SpeechGateTest's helpers). */
internal object TestPcm {
    fun tone(ms: Int, db: Float = -20f): ByteArray {
        val freq = 200.0
        val samples = ms * 16
        val out = ByteArray(samples * 2)
        val rmsTarget = 32768.0 * Math.pow(10.0, db / 20.0)
        val amp = rmsTarget * sqrt(2.0)
        for (i in 0 until samples) {
            val v = (amp * sin(2.0 * PI * freq * i / 16000.0)).toInt().coerceIn(-32768, 32767).toShort()
            out[i * 2] = (v.toInt() and 0xFF).toByte()
            out[i * 2 + 1] = ((v.toInt() shr 8) and 0xFF).toByte()
        }
        return out
    }

    fun silence(ms: Int): ByteArray {
        val db = -70f
        val samples = ms * 16
        val out = ByteArray(samples * 2)
        val rmsTarget = 32768.0 * Math.pow(10.0, db / 20.0)
        val amp = (rmsTarget * sqrt(3.0)).toInt().coerceAtLeast(1)
        val rng = Random(42)
        for (i in 0 until samples) {
            val v = rng.nextInt(-amp, amp + 1).toShort()
            out[i * 2] = (v.toInt() and 0xFF).toByte()
            out[i * 2 + 1] = ((v.toInt() shr 8) and 0xFF).toByte()
        }
        return out
    }
}
