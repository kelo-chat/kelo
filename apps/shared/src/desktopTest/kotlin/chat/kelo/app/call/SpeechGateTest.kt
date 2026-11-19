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

    @Test fun `a tone of 300 ms then a clear pause emits Started then Ended with preroll`() {
        val gate = SpeechGate()
        val pcm = silence(400) + tone(300) + silence(1100)
        val events = gate.feed(pcm)
        assertEquals(2, events.size)
        assertEquals(SpeechGate.Out.Started, events[0])
        assertTrue(events[1] is SpeechGate.Out.Ended)
        val ended = events[1] as SpeechGate.Out.Ended
        // Pre-roll (up to 300 ms) + tone (300 ms) + silence (1000 ms) is at least (300 + 300) * 32 bytes
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
        val pcm = silence(300) + tone(250) + silence(1100)
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
        val pcm = silence(400) + tone(300) + silence(1100)

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

    @Test fun `a pause of 800 ms inside a sentence does not end it`() {   // owner 2026-11-20: "나 목소리 너무 끊겨"
        val events = SpeechGate().feed(silence(400) + tone(600) + silence(800) + tone(700) + silence(1400))
        assertEquals(2, events.size)
        assertEquals(SpeechGate.Out.Started, events[0])
        assertTrue((events[1] as SpeechGate.Out.Ended).pcm16le.size >= (600 + 800 + 700) * 32)
    }

    @Test fun `the turn ends after a clear pause of one second`() {
        val gate = SpeechGate()
        assertEquals(listOf<SpeechGate.Out>(SpeechGate.Out.Started), gate.feed(silence(400) + tone(800) + silence(950)))
        assertTrue(gate.feed(silence(100)).single() is SpeechGate.Out.Ended)
    }

    @Test fun `after a long pause inside the turn the end waits longer`() {
        val gate = SpeechGate()
        assertEquals(listOf<SpeechGate.Out>(SpeechGate.Out.Started), gate.feed(silence(400) + tone(600) + silence(900) + tone(600) + silence(1300)))
        assertTrue(gate.feed(silence(120)).single() is SpeechGate.Out.Ended)   // 900 + 500 = 1400 ms
    }

    @Test fun `the end never waits more than 1800 ms`() {
        val gate = SpeechGate()
        val pcm = silence(400) + tone(600) + silence(900) + tone(600) + silence(1300) + tone(600) + silence(1700) + tone(600) + silence(1780)
        assertEquals(listOf<SpeechGate.Out>(SpeechGate.Out.Started), gate.feed(pcm))
        assertTrue(gate.feed(silence(20)).single() is SpeechGate.Out.Ended)
    }

    @Test fun `a soft ending after loud speech is still speech`() {
        val events = SpeechGate().feed(silence(400) + tone(600, db = -20f) + tone(1100, db = -54f) + silence(1100))
        assertEquals(2, events.size)
        assertTrue((events[1] as SpeechGate.Out.Ended).pcm16le.size >= (400 + 1100) * 32)
    }

    @Test fun `single clicks in the quiet do not keep the turn open`() {
        var pcm = silence(400) + tone(600)
        repeat(6) { pcm += silence(180) + tone(20) }
        val events = SpeechGate().feed(pcm + silence(1000))
        assertEquals(2, events.size)
        assertTrue((events[1] as SpeechGate.Out.Ended).pcm16le.size <= (300 + 400 + 1000 + 40) * 32)
    }
}
