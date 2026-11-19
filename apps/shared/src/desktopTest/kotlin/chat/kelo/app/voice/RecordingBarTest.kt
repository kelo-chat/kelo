package chat.kelo.app.voice

import kotlin.math.PI
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNull
import kotlin.test.assertTrue

class RecordingBarRulesTest {
    private fun state(stage: RecordingBarState.Stage, held: Boolean = false) =
        RecordingBarState(stage = stage, elapsedMs = 0, endsInSeconds = null, held = held)

    @Test fun showBarOnlyWhileListeningAndNotHeld() {
        assertFalse(RecordingBarRules.showBar(null))
        assertFalse(RecordingBarRules.showBar(state(RecordingBarState.Stage.LISTENING, held = true)))
        assertTrue(RecordingBarRules.showBar(state(RecordingBarState.Stage.LISTENING)))
    }

    @Test fun levelMapsDbfsFromMinus55ToMinus15() {
        assertEquals(0f, RecordingBarRules.level(0f), 0.0001f)
        assertEquals(0f, RecordingBarRules.level(0.001f), 0.0001f)    // -60 dBFS
        assertEquals(1f, RecordingBarRules.level(0.1778f), 0.0001f)  // -15 dBFS
        assertEquals(1f, RecordingBarRules.level(1f), 0.0001f)
        // a 0.1-amplitude sine has an rms of 0.1 / sqrt(2), about -23 dBFS
        assertEquals(0.8f, RecordingBarRules.level(0.1f / sqrt(2f)), 0.05f)
    }

    @Test fun heightRunsFromADotToTwentyFourDp() {
        assertEquals(3f, RecordingBarRules.height(0f), 0.0001f)
        assertEquals(24f, RecordingBarRules.height(1f), 0.0001f)
    }

    @Test fun visibleBarsFitTheWidth() {
        assertEquals(40, RecordingBarRules.visibleBars(200f))
        assertEquals(0, RecordingBarRules.visibleBars(-5f))
    }

    @Test fun endsInCountsTheLastTenSeconds() {
        assertNull(RecordingBarRules.endsIn(109_999L))
        assertEquals(10, RecordingBarRules.endsIn(110_000L))
        assertEquals(0, RecordingBarRules.endsIn(120_000L))
        assertEquals(0, RecordingBarRules.endsIn(130_000L))
    }

    @Test fun sendIsOffWhileTurningToTextOrKeloReplies() {
        assertFalse(RecordingBarRules.sendEnabled(RecordingBarState.Stage.TURNING_TO_TEXT, busy = false))
        assertFalse(RecordingBarRules.sendEnabled(RecordingBarState.Stage.LISTENING, busy = true))
        assertTrue(RecordingBarRules.sendEnabled(RecordingBarState.Stage.LISTENING, busy = false))
    }
}

class LevelMeterTest {
    private fun pcm(seconds: Double, amplitude: Double = 0.0): ByteArray {
        val n = (16_000 * seconds).toInt()
        val out = ByteArray(n * 2)
        for (i in 0 until n) {
            val v = (amplitude * 32767 * sin(2 * PI * 440 * i / 16_000)).toInt()
            out[2 * i] = v.toByte(); out[2 * i + 1] = (v shr 8).toByte()
        }
        return out
    }

    @Test fun oneSecondInHundredMsChunksGivesTwelveLevelsThenTheThirteenthWithCarry() {
        val meter = LevelMeter()
        val audio = pcm(1.0, 0.1)
        var count = 0
        for (c in 0 until 10) count += meter.add(audio.copyOfRange(c * 3200, (c + 1) * 3200)).size
        assertEquals(12, count)
        count += meter.add(pcm(0.06, 0.1)).size
        assertEquals(13, count)
    }

    @Test fun silenceGivesZero() {
        val levels = LevelMeter().add(pcm(0.5))
        assertEquals(6, levels.size)
        levels.forEach { assertEquals(0f, it, 0.0001f) }
    }

    @Test fun oddChunkSizesGiveTheSameLevelsAsOneBigChunk() {
        val audio = pcm(1.0, 0.05)
        val whole = LevelMeter().add(audio)
        val meter = LevelMeter()
        val pieces = ArrayList<Float>()
        var at = 0
        while (at < audio.size) {
            val end = minOf(audio.size, at + 333)
            meter.add(audio.copyOfRange(at, end)).forEach { pieces.add(it) }
            at = end
        }
        assertEquals(whole.toList(), pieces)
    }
}

class LevelTrailTest {
    @Test fun keepsTheNewestInOrderOldestToNewest() {
        val trail = LevelTrail(capacity = 4)
        for (i in 1..6) trail.push(i / 10f, atNanos = i.toLong())
        val out = FloatArray(4)
        assertEquals(4, trail.copyInto(out))
        assertEquals(listOf(0.3f, 0.4f, 0.5f, 0.6f), out.toList())
        assertEquals(6L, trail.lastPushNanos)
    }

    @Test fun releaseFadesAfterALoudBar() {
        val trail = LevelTrail()
        trail.push(1f, 1L)
        trail.push(0f, 2L)
        val out = FloatArray(2)
        assertEquals(2, trail.copyInto(out))
        assertEquals(0.55f, out[1], 0.0001f)
    }

    @Test fun versionChangesOnPushAndClear() {
        val trail = LevelTrail()
        val v0 = trail.version
        trail.push(0.5f, 1L)
        assertEquals(v0 + 1, trail.version)
        trail.clear()
        assertEquals(v0 + 2, trail.version)
    }

    @Test fun clearEmptiesIt() {
        val trail = LevelTrail()
        trail.push(0.5f, 1L)
        trail.clear()
        assertEquals(0, trail.copyInto(FloatArray(8)))
    }
}


class PhoneDictationStepsTest {
    private val idle = PhoneDictationSteps.State(listening = false, after = null)

    private fun afterAll(vararg events: PhoneDictationSteps.Event): PhoneDictationSteps.State =
        events.fold(idle) { s, e -> PhoneDictationSteps.step(s, e).first }

    @Test fun startListens() {
        val (s, a) = PhoneDictationSteps.step(idle, PhoneDictationSteps.Event.Start)
        assertTrue(s.listening)
        assertEquals(PhoneDictationSteps.Action.NONE, a)
    }

    @Test fun stopButtonStopsTheRecognizerAndSendsNothing() {
        val s = afterAll(PhoneDictationSteps.Event.Start, PhoneDictationSteps.Event.Finish(AfterSpeech.FIELD))
        val (_, a) = PhoneDictationSteps.step(s, PhoneDictationSteps.Event.Ended(false))
        assertEquals(PhoneDictationSteps.Action.NONE, a)
        assertEquals(PhoneDictationSteps.Action.STOP_RECOGNIZER, PhoneDictationSteps.step(afterAll(PhoneDictationSteps.Event.Start), PhoneDictationSteps.Event.Finish(AfterSpeech.FIELD)).second)
    }

    @Test fun sendAfterTheRecognizerEndsSendsTheField() {
        val s = afterAll(PhoneDictationSteps.Event.Start, PhoneDictationSteps.Event.Finish(AfterSpeech.SEND))
        assertEquals(PhoneDictationSteps.Action.SEND_FIELD, PhoneDictationSteps.step(s, PhoneDictationSteps.Event.Ended(false)).second)
    }

    @Test fun sendThenARecognizerErrorSendsNothing() {
        val s = afterAll(PhoneDictationSteps.Event.Start, PhoneDictationSteps.Event.Finish(AfterSpeech.SEND))
        val (after, a) = PhoneDictationSteps.step(s, PhoneDictationSteps.Event.Ended(true))
        assertEquals(PhoneDictationSteps.Action.NONE, a)
        assertFalse(after.listening)
    }

    @Test fun aPauseEndWithoutAButtonSendsNothing() {
        val s = afterAll(PhoneDictationSteps.Event.Start)
        val (after, a) = PhoneDictationSteps.step(s, PhoneDictationSteps.Event.Ended(false))
        assertEquals(PhoneDictationSteps.Action.NONE, a)
        assertFalse(after.listening)
    }

    @Test fun discardRestoresTheFieldAndStops() {
        val (after, a) = PhoneDictationSteps.step(afterAll(PhoneDictationSteps.Event.Start), PhoneDictationSteps.Event.Discard)
        assertEquals(PhoneDictationSteps.Action.RESTORE_FIELD, a)
        assertFalse(after.listening)
    }

    @Test fun discardAfterSendStillDropsTheSend() {
        val s = afterAll(PhoneDictationSteps.Event.Start, PhoneDictationSteps.Event.Finish(AfterSpeech.SEND))
        assertEquals(PhoneDictationSteps.Action.RESTORE_FIELD, PhoneDictationSteps.step(s, PhoneDictationSteps.Event.Discard).second)
    }
}
