package chat.kelo.app.call

import chat.kelo.app.voice.Wav
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class WavDecodeTest {
    @Test fun `round trip with Wav encode`() {
        val pcm = ByteArray(3200) { (it % 256).toByte() }
        val encoded = Wav.encode(pcm, sampleRate = 44100)
        val decoded = Wav.decode(encoded)
        assertNotNull(decoded)
        assertEquals(44100, decoded!!.second)
        assertArrayEquals(pcm, decoded.first)
    }

    @Test fun `stereo returns null`() {
        val pcm = ByteArray(3200)
        val wav = Wav.encode(pcm, sampleRate = 16000)
        // In standard 44-byte WAV, numChannels is at byte offset 22 (16-bit LE)
        wav[22] = 2 // stereo
        wav[23] = 0
        assertNull(Wav.decode(wav))
    }

    @Test fun `truncated or invalid wav returns null`() {
        assertNull(Wav.decode(ByteArray(10)))
        assertNull(Wav.decode("NOT_A_WAV_FILE_HEADER_AT_ALL_EXTRA_BYTES_12345678".encodeToByteArray()))
    }
}
