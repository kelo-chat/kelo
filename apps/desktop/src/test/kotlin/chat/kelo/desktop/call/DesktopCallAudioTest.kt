package chat.kelo.desktop.call

import chat.kelo.desktop.core.DesktopCallAudio
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DesktopCallAudioTest {

    @Test
    fun `desktop call audio defaults reflect platform capabilities`() {
        val audio = DesktopCallAudio()
        assertFalse("Desktop does not have hardware echo cancellation", audio.echoCancel)
        assertFalse("Desktop does not have speaker routing switch", audio.hasSpeakerSwitch)
        assertTrue(audio.speakerOn)
        audio.release()
    }

    @Test
    fun `desktop chime pcm byte count matches 360ms mono 16bit`() {
        val pcm = DesktopCallAudio.generateChimePcm(16000)
        // 360ms at 16kHz = 5760 samples * 2 bytes = 11520 bytes
        assertEquals(11520, pcm.size)
    }

    @Test
    fun `desktop chime pcm has fades at envelope boundaries`() {
        val pcm = DesktopCallAudio.generateChimePcm(16000)

        fun sampleAt(idx: Int): Short {
            val low = pcm[idx * 2].toInt() and 0xFF
            val high = pcm[idx * 2 + 1].toInt() shl 8
            return (low or high).toShort()
        }

        val firstSample = kotlin.math.abs(sampleAt(0).toInt())
        assertTrue("First sample should be small, was $firstSample", firstSample < 500)

        var maxMidNote1 = 0
        for (i in 1400..1480) {
            maxMidNote1 = maxOf(maxMidNote1, kotlin.math.abs(sampleAt(i).toInt()))
        }
        assertTrue("Mid note 1 amplitude should be high, was $maxMidNote1", maxMidNote1 > 5000)

        val endNote1Sample = kotlin.math.abs(sampleAt(2879).toInt())
        assertTrue("End note 1 should be small, was $endNote1Sample", endNote1Sample < 500)
    }
}
