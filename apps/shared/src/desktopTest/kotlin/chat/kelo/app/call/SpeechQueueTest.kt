package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.voice.Wav
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class SpeechQueueTest {

    private class FakeCallAudio : CallAudio {
        override val echoCancel: Boolean = false
        override val hasSpeakerSwitch: Boolean = false
        override var speakerOn: Boolean = true
        var micStarted = false
        val played = mutableListOf<ByteArray>()
        var stopped = false
        override fun startMic(onChunk: (ByteArray) -> Unit): Boolean { micStarted = true; return true }
        override fun stopMic() { micStarted = false }
        override suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit) {
            played.add(pcm16le)
            delay(50) // simulate playback duration
        }
        override fun stopPlayback() { stopped = true }
        override fun chime(on: Boolean) {}
        override fun release() {}
        override var onInterrupted: (() -> Unit)? = null
    }

    private class FakeDeviceEngine : SpeechEngine {
        val spoken = mutableListOf<String>()
        override fun speak(text: String, language: String, onDone: () -> Unit) {
            spoken.add(text)
            onDone()
        }
        override fun stop() {}
    }

    @Test fun `order is kept piece 2 goes to device engine when server returns null and DEVICE_SPOKE fires`() = runTest {
        val audio = FakeCallAudio()
        val device = FakeDeviceEngine()
        val events = mutableListOf<SpeechQueue.Event>()
        var serverRequests = 0
        var maxConcurrentFetches = 0
        var activeFetches = 0

        val fakeServer: suspend (String, String) -> ByteArray? = { text, lang ->
            activeFetches++
            if (activeFetches > maxConcurrentFetches) maxConcurrentFetches = activeFetches
            serverRequests++
            delay(20)
            activeFetches--
            if (text == "piece2") null else Wav.encode(text.encodeToByteArray(), 44100)
        }

        val queue = SpeechQueue(
            scope = this,
            audio = audio,
            server = fakeServer,
            device = { device },
            serverLang = { "ko" },
            appLang = { "ko" },
            onLevel = {},
            onEvent = { events.add(it) },
        )

        queue.add("piece1")
        queue.add("piece2")
        queue.add("piece3")

        advanceUntilIdle()

        // 1. Events contain FIRST_AUDIO, FIRST_PLAY, DEVICE_SPOKE, IDLE
        assertTrue("Contains FIRST_AUDIO", events.contains(SpeechQueue.Event.FIRST_AUDIO))
        assertTrue("Contains FIRST_PLAY", events.contains(SpeechQueue.Event.FIRST_PLAY))
        assertEquals("DEVICE_SPOKE fired once", 1, events.count { it == SpeechQueue.Event.DEVICE_SPOKE })
        assertEquals("IDLE fired at the end", SpeechQueue.Event.IDLE, events.last())

        // 2. Piece 2 went to device engine
        assertEquals(listOf("piece2"), device.spoken)

        // 3. Audio played piece1 and piece3 in order
        assertEquals(2, audio.played.size)
        assertEquals("piece1", audio.played[0].decodeToString())
        assertEquals("piece3", audio.played[1].decodeToString())

        // 4. Prefetch never exceeds 2 ahead: server fetches are serialized or at most CallRules.PREFETCH
        assertTrue("Prefetch max concurrent fetches <= 2", maxConcurrentFetches <= 2)
        assertFalse(queue.playing)
    }

    @Test fun `stop mid play cancels everything and plays nothing more`() = runTest {
        val audio = FakeCallAudio()
        val device = FakeDeviceEngine()
        val events = mutableListOf<SpeechQueue.Event>()

        val fakeServer: suspend (String, String) -> ByteArray? = { text, _ ->
            delay(50)
            Wav.encode(text.encodeToByteArray(), 44100)
        }

        val queue = SpeechQueue(
            scope = this,
            audio = audio,
            server = fakeServer,
            device = { device },
            serverLang = { "ko" },
            appLang = { "ko" },
            onLevel = {},
            onEvent = { events.add(it) },
        )

        queue.add("piece1")
        queue.add("piece2")
        queue.add("piece3")

        advanceTimeBy(30)
        queue.stop()
        advanceUntilIdle()

        assertTrue(audio.stopped)
        assertTrue(audio.played.isEmpty())
        assertFalse(queue.playing)
    }
}
