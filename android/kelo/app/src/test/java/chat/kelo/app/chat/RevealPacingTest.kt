package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** The reveal counter, driven with fake frames (16 ms) and fake chunks; no screen, no server. */
class RevealPacingTest {
    private val frame = 16f

    /** Runs frames until done (or [limitMs]); returns the time it took. */
    private fun runUntilDone(start: RevealPacing.State, limitMs: Float = 10_000f): Pair<RevealPacing.State, Float> {
        var s = start
        var t = 0f
        while (!s.done && t < limitMs) { s = RevealPacing.advance(s, frame); t += frame }
        return s to t
    }

    @Test fun historyAndReducedMotionShowEverythingAtOnce() {
        val s = RevealPacing.start(500, animate = false)
        assertTrue(s.done)
        assertEquals(500f, s.shown)
        assertEquals(s, RevealPacing.advance(s, frame))
    }

    @Test fun aSmallChunkIsWrittenAtReadingSpeed() {
        // 42 characters at 0.14 per ms: about 300 ms
        val (s, t) = runUntilDone(RevealPacing.start(42, animate = true))
        assertTrue(s.done)
        assertTrue("took $t", t in 280f..340f)
    }

    @Test fun aLargeBacklogCatchesUpWithinSevenHundredMs() {
        val (_, t) = runUntilDone(RevealPacing.start(3000, animate = true))
        assertTrue("took $t", t in 680f..740f)
        // 98 characters is exactly what the reading speed covers in the catch-up time
        val (_, mid) = runUntilDone(RevealPacing.start(98, animate = true))
        assertTrue("took $mid", mid in 680f..740f)
    }

    @Test fun neverOvershootsAndNeverGoesBack() {
        var s = RevealPacing.start(10, animate = true)
        var prev = 0f
        repeat(200) { i ->
            if (i % 20 == 0) s = RevealPacing.retarget(s, s.target + 37)
            s = RevealPacing.advance(s, frame)
            assertTrue(s.shown <= s.target)
            assertTrue(s.shown >= prev)
            prev = s.shown
        }
    }

    @Test fun steadyChunksNeverLagMoreThanOneAndAHalfSeconds() {
        // a chunk of 25..400 characters every 120..700 ms, for 20 s
        var s = RevealPacing.start(0, animate = true)
        val rnd = java.util.Random(7)
        var nextChunk = 0f
        var t = 0f
        var worst = 0f
        while (t < 20_000f) {
            if (t >= nextChunk) {
                s = RevealPacing.retarget(s, s.target + 25 + rnd.nextInt(376))
                nextChunk = t + 120 + rnd.nextInt(581)
            }
            s = RevealPacing.advance(s, frame)
            t += frame
            worst = maxOf(worst, RevealPacing.lagMs(s))
        }
        assertTrue("worst lag $worst", worst <= RevealPacing.MAX_LAG_MS)
        val (end, rest) = runUntilDone(s)
        assertTrue(end.done)
        assertTrue(rest <= RevealPacing.MAX_LAG_MS + frame)
    }

    @Test fun aStalledFrameCatchesUpWithoutOvershooting() {
        var s = RevealPacing.start(100, animate = true)
        s = RevealPacing.advance(s, 5_000f)   // the app was paused
        assertTrue(s.done)
        assertEquals(100f, s.shown)
    }

    @Test fun textThatShrankSnapsToTheNewLength() {
        var s = RevealPacing.start(100, animate = true)
        repeat(30) { s = RevealPacing.advance(s, frame) }
        val shown = s.shown
        s = RevealPacing.retarget(s, (shown / 2).toInt())   // e.g. "**bold" became bold: the markers are gone
        assertTrue(s.done)
        assertEquals((shown / 2).toInt().toFloat(), s.shown)
    }

    @Test fun aSpeedOfZeroIsLiftedSoItNeverStalls() {
        val s = RevealPacing.State(0f, 300, 0f)
        val next = RevealPacing.advance(s, frame)
        assertTrue(next.shown > 0f)
        assertTrue(RevealPacing.lagMs(next) <= RevealPacing.MAX_LAG_MS)
    }

    @Test fun theEdgeFadesIn() {
        val shown = 50f
        assertEquals(1f, RevealPacing.alpha(30, shown))
        assertEquals(1f, RevealPacing.alpha(44, shown))
        assertTrue(RevealPacing.alpha(47, shown) in 0.4f..0.6f)
        assertTrue(RevealPacing.alpha(49, shown) < 0.2f)
        assertEquals(RevealPacing.MIN_ALPHA, RevealPacing.alpha(60, shown))
        assertTrue(RevealPacing.alpha(48, shown) < RevealPacing.alpha(46, shown))
    }

    @Test fun theSpeedIsReadingSpeedOrTheCatchUp() {
        assertEquals(RevealPacing.READ_PER_MS, RevealPacing.speed(10f))
        assertEquals(10_000f / RevealPacing.CATCH_UP_MS, RevealPacing.speed(10_000f))
        assertEquals(0f, RevealPacing.duration(0f))
        assertEquals(RevealPacing.CATCH_UP_MS, RevealPacing.duration(10_000f))
    }
}
