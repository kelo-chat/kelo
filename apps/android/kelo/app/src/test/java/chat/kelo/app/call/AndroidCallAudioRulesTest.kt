package chat.kelo.app.call

import android.media.AudioDeviceInfo
import chat.kelo.app.voice.AndroidCallAudioRules
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin

class AndroidCallAudioRulesTest {

    @Test
    fun `picks bluetooth when available even if speakerOn is true`() {
        val available = listOf(
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
        )
        val picked = AndroidCallAudioRules.pickCommunicationDeviceType(available, speakerOn = true)
        assertEquals(AudioDeviceInfo.TYPE_BLUETOOTH_SCO, picked)
    }

    @Test
    fun `picks ble headset when available`() {
        val available = listOf(
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            AudioDeviceInfo.TYPE_BLE_HEADSET,
        )
        val picked = AndroidCallAudioRules.pickCommunicationDeviceType(available, speakerOn = false)
        assertEquals(AudioDeviceInfo.TYPE_BLE_HEADSET, picked)
    }

    @Test
    fun `picks speaker when no bluetooth and speakerOn is true`() {
        val available = listOf(
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
        )
        val picked = AndroidCallAudioRules.pickCommunicationDeviceType(available, speakerOn = true)
        assertEquals(AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, picked)
    }

    @Test
    fun `picks earpiece when no bluetooth and speakerOn is false`() {
        val available = listOf(
            AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
        )
        val picked = AndroidCallAudioRules.pickCommunicationDeviceType(available, speakerOn = false)
        assertEquals(AudioDeviceInfo.TYPE_BUILTIN_EARPIECE, picked)
    }

    @Test
    fun `chime pcm byte count matches 360ms mono 16bit`() {
        val sampleRate = 16000
        val pcm = AndroidCallAudioRules.generateChimePcm(sampleRate)
        // 180ms + 180ms = 360ms. 360ms at 16kHz = 5760 samples * 2 bytes = 11520 bytes
        assertEquals(11520, pcm.size)
    }

    @Test
    fun `chime pcm has 20ms fades at start and transitions`() {
        val sampleRate = 16000
        val pcm = AndroidCallAudioRules.generateChimePcm(sampleRate)

        fun sampleAt(idx: Int): Short {
            val low = pcm[idx * 2].toInt() and 0xFF
            val high = pcm[idx * 2 + 1].toInt() shl 8
            return (low or high).toShort()
        }

        // First sample should be near 0 due to fade-in
        val firstSample = kotlin.math.abs(sampleAt(0).toInt())
        assertTrue("First sample should be small, was $firstSample", firstSample < 500)

        // Middle of first note (~90ms = sample 1440) should have high amplitude
        var maxMidNote1 = 0
        for (i in 1400..1480) {
            maxMidNote1 = maxOf(maxMidNote1, kotlin.math.abs(sampleAt(i).toInt()))
        }
        assertTrue("Mid note 1 amplitude should be high, was $maxMidNote1", maxMidNote1 > 5000)

        // End of first note (~180ms = sample 2879) should be faded out near 0
        val endNote1Sample = kotlin.math.abs(sampleAt(2879).toInt())
        assertTrue("End note 1 should be small, was $endNote1Sample", endNote1Sample < 500)
    }
}
