package chat.kelo.app.voice

import chat.kelo.app.core.ApiError
import chat.kelo.app.platform.AudioRecorder
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** A recorder that emits the chunks the test gives it; stop returns everything recorded. */
private class FakeRecorder : AudioRecorder {
    override val available = true
    private var onChunk: ((ByteArray) -> Unit)? = null
    private val recorded = java.io.ByteArrayOutputStream()

    override fun start(onChunk: (ByteArray) -> Unit): Boolean {
        this.onChunk = onChunk
        return true
    }

    fun emit(chunk: ByteArray) {
        recorded.write(chunk)
        onChunk?.invoke(chunk)
    }

    override fun stop(): ByteArray {
        onChunk = null
        return recorded.toByteArray()
    }

    override fun cancel() {
        onChunk = null
    }
}

/** A loud 440 Hz tone, 16 kHz PCM16 mono, of [seconds]. */
private fun tone(seconds: Double): ByteArray {
    val n = (16_000 * seconds).toInt()
    val out = ByteArray(n * 2)
    for (i in 0 until n) {
        val v = (0.5 * 32767 * kotlin.math.sin(2 * kotlin.math.PI * 440 * i / 16_000)).toInt()
        out[2 * i] = v.toByte(); out[2 * i + 1] = (v shr 8).toByte()
    }
    return out
}

/** A transcriber from a block (the test's fake of the server's speech endpoint). */
private fun transcriberOf(block: suspend (ByteArray) -> String): Transcriber = object : Transcriber {
    override suspend fun transcribe(wav: ByteArray): String = block(wav)
}

class ServerMicControllerTest {
    private class Got(val text: String, val after: AfterSpeech)

    private fun controller(
        scope: kotlinx.coroutines.CoroutineScope,
        rec: FakeRecorder,
        got: MutableList<Got>,
        transcriber: Transcriber,
    ) = ServerMicController(rec, { transcriber }, scope, onBegin = {}, onText = { t, a -> got += Got(t, a) })

    private val hello = transcriberOf { "hello" }

    @Test fun finishFieldDeliversTheWordsAsFieldText() = runTest {
        val rec = FakeRecorder(); val got = mutableListOf<Got>()
        val c = controller(this, rec, got, hello)
        c.start()
        rec.emit(tone(1.0))
        c.finish(AfterSpeech.FIELD)
        advanceUntilIdle()
        assertEquals(listOf("hello"), got.map { it.text })
        assertEquals(AfterSpeech.FIELD, got.single().after)
        assertNull(c.bar.value)
    }

    @Test fun finishSendDeliversTheWordsForSending() = runTest {
        val rec = FakeRecorder(); val got = mutableListOf<Got>()
        val c = controller(this, rec, got, hello)
        c.start()
        rec.emit(tone(1.0))
        c.finish(AfterSpeech.SEND)
        advanceUntilIdle()
        assertEquals(AfterSpeech.SEND, got.single().after)
    }

    @Test fun theTwoMinuteCapPutsTheWordsInTheFieldNeverSent() = runTest {
        val rec = FakeRecorder(); val got = mutableListOf<Got>()
        val c = controller(this, rec, got, hello)
        c.start()
        rec.emit(tone(120.0))
        advanceTimeBy(200)
        advanceUntilIdle()
        assertEquals(AfterSpeech.FIELD, got.single().after)
    }

    @Test fun discardWhileTurningToTextDeliversNothing() = runTest {
        val rec = FakeRecorder(); val got = mutableListOf<Got>()
        val slow = transcriberOf { delay(10_000); "late" }
        val c = controller(this, rec, got, slow)
        c.start()
        rec.emit(tone(1.0))
        c.finish(AfterSpeech.SEND)
        assertEquals(RecordingBarState.Stage.TURNING_TO_TEXT, c.bar.value?.stage)
        c.discard()
        advanceUntilIdle()
        assertTrue(got.isEmpty())
        assertNull(c.bar.value)
    }

    @Test fun emptyTextIsNothingHeardWhateverWasAsked() = runTest {
        val rec = FakeRecorder(); val got = mutableListOf<Got>()
        val c = controller(this, rec, got, transcriberOf { "   " })
        c.start()
        rec.emit(tone(1.0))
        c.finish(AfterSpeech.SEND)
        advanceUntilIdle()
        assertTrue(got.isEmpty())
        assertEquals(VoiceProblem.NOTHING_HEARD, c.problem.value)
    }

    @Test fun offlineAfterSendKeepsTheRecordingAndRetryPutsItInTheField() = runTest {
        val rec = FakeRecorder(); val got = mutableListOf<Got>()
        var calls = 0
        val flaky = transcriberOf {
            calls++
            if (calls == 1) throw ApiError(ApiError.NO_RESPONSE, "offline") else "again"
        }
        val c = controller(this, rec, got, flaky)
        c.start()
        rec.emit(tone(1.0))
        c.finish(AfterSpeech.SEND)
        advanceUntilIdle()
        assertEquals(VoiceProblem.OFFLINE, c.problem.value)
        assertTrue(got.isEmpty())
        c.retry()
        advanceUntilIdle()
        assertEquals(listOf("again"), got.map { it.text })
        assertEquals(AfterSpeech.FIELD, got.single().after)
    }

    @Test fun heldIsCarriedOnTheBar() = runTest {
        val rec = FakeRecorder()
        val c = controller(this, rec, mutableListOf(), hello)
        c.start()
        assertFalse(c.bar.value!!.held)
        c.held = true
        assertTrue(c.bar.value!!.held)
        c.discard()
    }

    @Test fun levelsReachTheTrail() = runTest {
        val rec = FakeRecorder()
        val c = controller(this, rec, mutableListOf(), hello)
        c.start()
        rec.emit(tone(0.5))
        advanceTimeBy(120)
        val out = FloatArray(256)
        val n = c.trail.copyInto(out)
        assertTrue(n > 0)
        assertTrue(out.take(n).max() > 0.5f)
        c.discard()
    }
}
