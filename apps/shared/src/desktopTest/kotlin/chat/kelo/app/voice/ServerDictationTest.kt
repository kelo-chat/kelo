package chat.kelo.app.voice

import kotlin.math.PI
import kotlin.math.sin
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class DictationWayTest {
    private fun view(state: String, runtime: String = "ready", declined: Boolean = false) = VoiceView(
        state = state, declined = declined, runtime = runtime,
        recommended = ModelView(id = "sensevoice-small", name = "SenseVoice Small", sizeBytes = 240_000_000L),
    )

    @Test fun noViewMeansThePhoneOrNothing() {
        assertEquals(DictationWay.Way.PHONE, DictationWay.pick(null, phoneRecognizer = true))
        assertEquals(DictationWay.Way.NONE, DictationWay.pick(null, phoneRecognizer = false))
    }

    @Test fun readyWithRuntimeIsTheServer() {
        assertEquals(DictationWay.Way.SERVER, DictationWay.pick(view("ready"), true))
        assertEquals(DictationWay.Way.SERVER, DictationWay.pick(view("ready"), false))
    }

    @Test fun offeredAsksThenPhoneOrNone() {
        assertEquals(DictationWay.Way.OFFER_THEN_PHONE, DictationWay.pick(view("offered"), true))
        assertEquals(DictationWay.Way.OFFER_THEN_NONE, DictationWay.pick(view("offered"), false))
    }

    @Test fun downloadingKeepsTheOldWay() {
        assertEquals(DictationWay.Way.DOWNLOADING_PHONE, DictationWay.pick(view("downloading"), true))
        assertEquals(DictationWay.Way.DOWNLOADING_NONE, DictationWay.pick(view("downloading"), false))
    }

    @Test fun unsupportedDeclinedFailedAndMissingRuntimeAreThePhoneOrNone() {
        for (state in listOf("unsupported", "off", "failed")) {
            assertEquals(DictationWay.Way.PHONE, DictationWay.pick(view(state), true), state)
            assertEquals(DictationWay.Way.NONE, DictationWay.pick(view(state), false), state)
        }
        assertEquals(DictationWay.Way.PHONE, DictationWay.pick(view("ready", runtime = "missing"), true))
        assertEquals(DictationWay.Way.NONE, DictationWay.pick(view("offered", runtime = "missing"), false))
    }
}

class RecordingStepsTest {
    @Test fun tapStartOpensTheMicAndStopSends() {
        val start = RecordingSteps.step(RecordingSteps.Phase.IDLE, RecordingSteps.Event.Start)
        assertEquals(RecordingSteps.Phase.RECORDING, start.phase)
        assertEquals(RecordingSteps.Action.OPEN_MIC, start.action)
        val stop = RecordingSteps.step(RecordingSteps.Phase.RECORDING, RecordingSteps.Event.Stop)
        assertEquals(RecordingSteps.Phase.SENDING, stop.phase)
        assertEquals(RecordingSteps.Action.CLOSE_AND_SEND, stop.action)
    }

    @Test fun stopWhileSendingDoesNothing() {
        val s = RecordingSteps.step(RecordingSteps.Phase.SENDING, RecordingSteps.Event.Stop)
        assertEquals(RecordingSteps.Phase.SENDING, s.phase)
        assertEquals(RecordingSteps.Action.NONE, s.action)
    }

    @Test fun autoStopAtTwoMinutes() {
        assertEquals(RecordingSteps.MAX_MS, 120_000L)
        val s = RecordingSteps.step(RecordingSteps.Phase.RECORDING, RecordingSteps.Event.Tick(RecordingSteps.MAX_MS))
        assertEquals(RecordingSteps.Action.CLOSE_AND_SEND, s.action)
        assertEquals(RecordingSteps.Phase.SENDING, s.phase)
        val before = RecordingSteps.step(RecordingSteps.Phase.RECORDING, RecordingSteps.Event.Tick(RecordingSteps.MAX_MS - 1))
        assertEquals(RecordingSteps.Action.NONE, before.action)
    }

    @Test fun countdownStartsAtOneHundredTenSeconds() {
        assertEquals(110_000L, RecordingSteps.COUNTDOWN_FROM_MS)
    }

    @Test fun cancelWhileSendingDropsTheReply() {
        val s = RecordingSteps.step(RecordingSteps.Phase.SENDING, RecordingSteps.Event.Cancel)
        assertEquals(RecordingSteps.Phase.IDLE, s.phase)
        assertEquals(RecordingSteps.Action.NONE, s.action)
        val reply = RecordingSteps.step(RecordingSteps.Phase.IDLE, RecordingSteps.Event.Sent("안녕"))
        assertEquals(RecordingSteps.Action.INSERT, reply.action)
        assertEquals("안녕", reply.text)
    }

    @Test fun holdReleaseSendsAndSlideOffCancels() {
        assertEquals(RecordingSteps.Action.CLOSE_AND_SEND, RecordingSteps.step(RecordingSteps.Phase.RECORDING, RecordingSteps.Event.Stop).action)
        assertEquals(RecordingSteps.Action.CLOSE_AND_DROP, RecordingSteps.step(RecordingSteps.Phase.RECORDING, RecordingSteps.Event.Cancel).action)
    }

    @Test fun failureShowsTheProblem() {
        val s = RecordingSteps.step(RecordingSteps.Phase.SENDING, RecordingSteps.Event.Failed("voice_busy"))
        assertEquals(RecordingSteps.Phase.IDLE, s.phase)
        assertEquals(RecordingSteps.Action.SHOW, s.action)
        assertEquals("voice_busy", s.code)
    }
}

class WavTest {
    @Test fun headerForAKnownInput() {
        val pcm = byteArrayOf(1, 0, 2, 0, 3, 0, 4, 0)            // four 16-bit samples
        val wav = Wav.encode(pcm)
        assertEquals(44 + pcm.size, wav.size)
        assertEquals("RIFF", String(wav, 0, 4))
        assertEquals("WAVE", String(wav, 8, 4))
        assertEquals("fmt ", String(wav, 12, 4))
        assertEquals(16, le32(wav, 16))            // fmt chunk size
        assertEquals(1, le16(wav, 20))             // PCM
        assertEquals(1, le16(wav, 22))             // mono
        assertEquals(16000, le32(wav, 24))         // sample rate
        assertEquals(32000, le32(wav, 28))         // byte rate
        assertEquals(2, le16(wav, 32))             // block align
        assertEquals(16, le16(wav, 34))            // bits per sample
        assertEquals("data", String(wav, 36, 4))
        assertEquals(pcm.size, le32(wav, 40))
        assertEquals(36 + pcm.size, le32(wav, 4))
        assertContentEquals(pcm, wav.copyOfRange(44, wav.size))
    }

    private fun le16(b: ByteArray, at: Int) = (b[at].toInt() and 0xff) or ((b[at + 1].toInt() and 0xff) shl 8)
    private fun le32(b: ByteArray, at: Int) = le16(b, at) or (le16(b, at + 2) shl 16)
}

class SoundBarsTest {
    @Test fun silenceIsZeroAndLoudIsOne() {
        val silent = ByteArray(3200)
        assertEquals(0f, SoundBars.rms(silent, 0, silent.size), 0.0001f)
        val loud = ByteArray(3200)
        for (i in 0 until loud.size step 2) { loud[i] = 0xff.toByte(); loud[i + 1] = 0x7f }   // 32767
        assertTrue(SoundBars.rms(loud, 0, loud.size) > 0.95f)
    }

}

class ResampleTest {
    @Test fun fortyEightToSixteenKeepsALengthOfOneThird() {
        val pcm = ByteArray(48000 * 2)           // one second at 48 kHz
        assertEquals(16000 * 2, Resample.to16k(pcm, 48000).size)
    }

    @Test fun aOneKilohertzToneKeepsItsFrequency() {
        val n = 48000
        val pcm = ByteArray(n * 2)
        for (i in 0 until n) {
            val v = (sin(2 * PI * 1000.0 * i / 48000.0) * 16000).toInt()
            pcm[2 * i] = (v and 0xff).toByte(); pcm[2 * i + 1] = ((v shr 8) and 0xff).toByte()
        }
        val out = Resample.to16k(pcm, 48000)
        val samples = IntArray(out.size / 2) { i -> (out[2 * i].toInt() and 0xff) or (out[2 * i + 1].toInt() shl 8) }
        var crossings = 0
        for (i in 1 until samples.size) if ((samples[i - 1] < 0) != (samples[i] < 0)) crossings++
        // 1 kHz for one second: about 2000 zero crossings
        assertTrue(crossings in 1900..2100, "crossings=$crossings")
    }
}

class VoiceProblemTest {
    @Test fun noConnectionIsOffline() = assertEquals(VoiceProblem.OFFLINE, voiceProblem(null, null))
    @Test fun busyAndTooLongAndNotReady() {
        assertEquals(VoiceProblem.BUSY, voiceProblem(429, "voice_busy"))
        assertEquals(VoiceProblem.TOO_LONG, voiceProblem(413, "voice_too_long"))
        assertEquals(VoiceProblem.NOT_READY, voiceProblem(409, "voice_not_ready"))
    }
    @Test fun runtimeProblemsAreFailed() {
        assertEquals(VoiceProblem.FAILED, voiceProblem(503, "voice_runtime_missing"))
        assertEquals(VoiceProblem.FAILED, voiceProblem(504, "voice_timeout"))
        assertEquals(VoiceProblem.FAILED, voiceProblem(415, "voice_format"))
    }
}

class VoiceViewJsonTest {
    @Test fun decodesTheServerShape() {
        val json = kotlinx.serialization.json.Json { ignoreUnknownKeys = true }
        val text = """{"state":"offered","declined":false,"recommended":{"id":"parakeet-tdt-v3","name":"Parakeet TDT 0.6B v3",
            "maker":"NVIDIA","size_bytes":670576784,"memory_bytes":1610612736,"languages":["en","de"],"license_name":"CC BY 4.0",
            "license_url":"https://creativecommons.org/licenses/by/4.0/","source_url":"https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3",
            "attribution":"x"},"model":null,"progress":null,"error":null,"models":[],"max_seconds":120,"runtime":"ready"}"""
        val v = json.decodeFromString(VoiceView.serializer(), text)
        assertEquals("offered", v.state)
        assertEquals("Parakeet TDT 0.6B v3", v.recommended?.name)
        assertEquals(670576784L, v.recommended?.sizeBytes)
        assertEquals(120, v.maxSeconds)
    }
}
