package chat.kelo.app.voice

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class VoiceDownloadRingTest {
    private fun view(state: String, progress: VoiceProgress? = null) = VoiceView(state = state, progress = progress)

    @Test fun downloadingWithProgressShowsThePercent() {
        assertEquals(50, VoiceDownloadRing.of(view("downloading", VoiceProgress(done = 50, total = 100)))?.percent)
        assertEquals(0, VoiceDownloadRing.of(view("downloading", VoiceProgress(done = 0, total = 100)))?.percent)
    }

    @Test fun downloadingWithoutAPercentIsIndeterminate() {
        assertEquals(VoiceDownloadRing.Ring(null), VoiceDownloadRing.of(view("downloading")))
        assertEquals(VoiceDownloadRing.Ring(null), VoiceDownloadRing.of(view("downloading", VoiceProgress(done = 5, total = 0))))
    }

    @Test fun doneOrFailedGivesTheMicBack() {
        assertNull(VoiceDownloadRing.of(view("ready", VoiceProgress(done = 100, total = 100))))
        assertNull(VoiceDownloadRing.of(view("failed")))
        assertNull(VoiceDownloadRing.of(view("offered")))
        assertNull(VoiceDownloadRing.of(null))
    }
}
