package chat.kelo.app.call

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

class SpeechGateTest {
    private fun tone(ms: Int, freq: Double = 200.0, db: Float = -20f): ByteArray {
        val samples = ms * 16
        val out = ByteArray(samples * 2)
        val rmsTarget = 32768.0 * Math.pow(10.0, db / 20.0)
        val amp = rmsTarget * kotlin.math.sqrt(2.0)
        for (i in 0 until samples) {
            val v = (amp * sin(2.0 * PI * freq * i / 16000.0)).toInt().coerceIn(-32768, 32767).toShort()
            out[i * 2] = (v.toInt() and 0xFF).toByte()
            out[i * 2 + 1] = ((v.toInt() shr 8) and 0xFF).toByte()
        }
        return out
    }

    private fun silence(ms: Int, db: Float = -70f): ByteArray {
        val samples = ms * 16
        val out = ByteArray(samples * 2)
        val rmsTarget = 32768.0 * Math.pow(10.0, db / 20.0)
        val amp = (rmsTarget * kotlin.math.sqrt(3.0)).toInt().coerceAtLeast(1)
        val rng = Random(42)
        for (i in 0 until samples) {
            val v = rng.nextInt(-amp, amp + 1).toShort()
            out[i * 2] = (v.toInt() and 0xFF).toByte()
            out[i * 2 + 1] = ((v.toInt() shr 8) and 0xFF).toByte()
        }
        return out
    }

    private fun noise(ms: Int, db: Float = -45f): ByteArray {
        val samples = ms * 16
        val out = ByteArray(samples * 2)
        val rmsTarget = 32768.0 * Math.pow(10.0, db / 20.0)
        val amp = (rmsTarget * kotlin.math.sqrt(3.0)).toInt().coerceAtLeast(1)
        val rng = Random(1234)
        for (i in 0 until samples) {
            val v = rng.nextInt(-amp, amp + 1).toShort()
            out[i * 2] = (v.toInt() and 0xFF).toByte()
            out[i * 2 + 1] = ((v.toInt() shr 8) and 0xFF).toByte()
        }
        return out
    }

    @Test fun `a tone of 300 ms then 700 ms of silence emits Started then Ended with preroll`() {
        val gate = SpeechGate()
        val pcm = silence(400) + tone(300) + silence(800)
        val events = gate.feed(pcm)
        assertEquals(2, events.size)
        assertEquals(SpeechGate.Out.Started, events[0])
        assertTrue(events[1] is SpeechGate.Out.Ended)
        val ended = events[1] as SpeechGate.Out.Ended
        // Pre-roll (up to 300 ms) + tone (300 ms) + silence (700 ms) is at least (300 + 300) * 32 bytes
        assertTrue("Ended audio should include pre-roll and speech", ended.pcm16le.size >= 600 * 32)
    }

    @Test fun `150 ms of tone produces nothing`() {
        val gate = SpeechGate()
        val pcm = silence(300) + tone(150) + silence(300)
        val events = gate.feed(pcm)
        assertEquals(emptyList<SpeechGate.Out>(), events)
    }

    @Test fun `250 ms of tone and silence is Dropped under minSpeechMs`() {
        val gate = SpeechGate()
        val pcm = silence(300) + tone(250) + silence(800)
        val events = gate.feed(pcm)
        assertEquals(2, events.size)
        assertEquals(SpeechGate.Out.Started, events[0])
        assertEquals(SpeechGate.Out.Dropped, events[1])
    }

    @Test fun `steady noise at -45 never starts and floor follows it`() {
        val gate = SpeechGate()
        val pcm = noise(2000, db = -45f)
        val events = gate.feed(pcm)
        assertEquals(emptyList<SpeechGate.Out>(), events)
        assertTrue("Floor should adapt towards -45 dBFS, got ${gate.floorDb}", gate.floorDb >= -50f)
    }

    @Test fun `speaking true needs +18 dB and 300 ms`() {
        val gate = SpeechGate()
        gate.speaking = true
        // Tone at -35 dBFS: with floor at -60, +18 dB is -42 dBFS, but if bargeStartDb is 18 dB,
        // let's test duration requirement: 250 ms of tone does not trigger Started when bargeStartMs is 300 ms
        val pcmShort = silence(300) + tone(250, db = -20f)
        val ev1 = gate.feed(pcmShort)
        assertEquals(emptyList<SpeechGate.Out>(), ev1)

        val ev2 = gate.feed(tone(100, db = -20f))
        assertEquals(listOf(SpeechGate.Out.Started), ev2)
    }

    @Test fun `61 s of tone ends at 60 s`() {
        val gate = SpeechGate()
        // 61 seconds in chunks
        val events = mutableListOf<SpeechGate.Out>()
        val chunk1s = tone(1000)
        for (i in 0 until 61) {
            events.addAll(gate.feed(chunk1s))
        }
        assertTrue(events.contains(SpeechGate.Out.Started))
        val ended = events.filterIsInstance<SpeechGate.Out.Ended>()
        assertEquals(1, ended.size)
        // 60 s = 60 * 16000 * 2 = 1,920,000 bytes
        assertEquals(60 * 16000 * 2, ended[0].pcm16le.size)
    }

    @Test fun `odd chunk sizes give the same result as 640`() {
        val gate1 = SpeechGate()
        val gate2 = SpeechGate()
        val pcm = silence(400) + tone(300) + silence(800)

        // gate1 fed in 640-byte chunks
        val ev1 = mutableListOf<SpeechGate.Out>()
        for (i in 0 until pcm.size step 640) {
            val chunk = pcm.copyOfRange(i, minOf(i + 640, pcm.size))
            ev1.addAll(gate1.feed(chunk))
        }

        // gate2 fed in 333-byte chunks
        val ev2 = mutableListOf<SpeechGate.Out>()
        for (i in 0 until pcm.size step 333) {
            val chunk = pcm.copyOfRange(i, minOf(i + 333, pcm.size))
            ev2.addAll(gate2.feed(chunk))
        }

        assertEquals(ev1.size, ev2.size)
        assertEquals(ev1[0], ev2[0])
        val end1 = ev1[1] as SpeechGate.Out.Ended
        val end2 = ev2[1] as SpeechGate.Out.Ended
        assertEquals(end1.pcm16le.size, end2.pcm16le.size)
    }
}
