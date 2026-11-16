package chat.kelo.app.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RecognizerChoiceTest {
    private val W = RecognizerChoice.Way.entries

    @Test fun `on-device first on Android 12+, then Google, then default`() =
        assertEquals(listOf(W[0], W[1], W[2]), RecognizerChoice.order(34, onDevice = true, googleService = true, onDeviceFailed = false))

    @Test fun `no on-device below Android 12`() =
        assertEquals(listOf(W[1], W[2]), RecognizerChoice.order(30, onDevice = true, googleService = true, onDeviceFailed = false))

    @Test fun `after the on-device model said no, it is skipped`() =
        assertEquals(listOf(W[1], W[2]), RecognizerChoice.order(34, onDevice = true, googleService = true, onDeviceFailed = true))

    @Test fun `without Google's service the default is last`() =
        assertEquals(listOf(W[0], W[2]), RecognizerChoice.order(34, onDevice = true, googleService = false, onDeviceFailed = false))

    @Test fun `language errors`() {
        assertTrue(RecognizerChoice.languageMissing(12))
        assertTrue(RecognizerChoice.languageMissing(13))
        assertFalse(RecognizerChoice.languageMissing(7))
    }
}
