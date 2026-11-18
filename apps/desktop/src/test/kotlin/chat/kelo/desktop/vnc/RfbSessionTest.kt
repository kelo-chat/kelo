package chat.kelo.desktop.vnc

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import java.io.ByteArrayOutputStream
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The handshake, the first requests and the framing of the server's messages, against a scripted server. */
class RfbSessionTest {
    @Test fun handshakeAnswersWithNoneThenAsksForTheWholeScreen() {
        val r = run(listOf(serverStart()))
        assertContentEquals(clientStart(), r.sent)
        assertEquals(64, r.frame.width)
        assertEquals(48, r.frame.height)
        assertEquals(listOf("the screen closed"), r.failures)
    }

    @Test fun aMessageSplitAnywhereReadsTheSame() {
        val stream = cat(serverStart(), ints(2), ints(0, 0, 0, 0))   // a bell, then an update with no rectangles
        val expected = cat(clientStart(), RfbClientMessage.updateRequest(true, 0, 0, 64, 48))
        for (cut in 1 until stream.size) {
            val r = run(listOf(stream.copyOfRange(0, cut), stream.copyOfRange(cut, stream.size)))
            assertContentEquals(expected, r.sent, "split at $cut")
            assertEquals(1, r.updates, "split at $cut")
        }
    }

    @Test fun securityWithoutNoneFails() {
        val r = run(listOf(cat(ascii("RFB 003.008\n"), ints(1, 2))))   // only VNC password authentication
        assertEquals(listOf("the server asks for a password, which this client does not do"), r.failures)
        assertContentEquals(ascii("RFB 003.008\n"), r.sent)
    }

    @Test fun aRefusedConnectionSaysWhy() {
        val r = run(listOf(cat(ascii("RFB 003.008\n"), ints(1, 1), ints(0, 0, 0, 1), ints(0, 0, 0, 4), ascii("nope"))))
        assertEquals(listOf("nope"), r.failures)
    }

    @Test fun aGreetingThatIsNotRfbFails() {
        val r = run(listOf(ascii("HTTP/1.1 400 Bad")))
        assertEquals(listOf("the server does not speak RFB 3"), r.failures)
    }

    @Test fun aHugeNameIsRefused() {
        val r = run(listOf(cat(ascii("RFB 003.008\n"), ints(1, 1), ints(0, 0, 0, 0), ints(0, 64, 0, 48), ByteArray(16), ints(0x7f, 0xff, 0xff, 0xff))))
        assertTrue(r.failures.single().contains("is not allowed"), r.failures.toString())
    }

    @Test fun anUnknownServerMessageEndsTheSession() {
        val r = run(listOf(serverStart(), ints(99)))
        assertEquals(listOf("an unknown server message 99"), r.failures)
    }

    @Test fun bellSetColourMapAndCutTextAreReadAndSkipped() {
        val stream = cat(
            serverStart(),
            ints(2),                                             // Bell
            ints(1, 0, 0, 0, 0, 1), ByteArray(6),                // SetColourMapEntries: one entry
            ints(3, 0, 0, 0, 0, 0, 0, 3), ascii("abc"),          // ServerCutText, legacy: three bytes
            ints(3, 0, 0, 0, 0xff, 0xff, 0xff, 0xf8), ByteArray(8),   // ServerCutText, extended: flags and eight bytes
            ints(0, 0, 0, 0),                                    // FramebufferUpdate with no rectangles
        )
        val r = run(listOf(stream))
        assertEquals(1, r.updates)
        assertContentEquals(cat(clientStart(), RfbClientMessage.updateRequest(true, 0, 0, 64, 48)), r.sent)
        assertEquals(listOf("the screen closed"), r.failures)
    }

    @Test fun theRequestsHaveTheRfbLayout() {
        assertContentEquals(ints(32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0), RfbClientMessage.setPixelFormat().copyOfRange(4, 20))
        assertEquals(20, RfbClientMessage.setPixelFormat().size)
        assertContentEquals(ints(2, 0, 0, 2, 0, 0, 0, 7, 0xff, 0xff, 0xff, 0xd1), RfbClientMessage.setEncodings(intArrayOf(7, -47)))
        assertContentEquals(ints(3, 1, 0, 10, 0, 20, 1, 0xe0, 0, 0x30), RfbClientMessage.updateRequest(true, 10, 20, 480, 48))
    }
}
