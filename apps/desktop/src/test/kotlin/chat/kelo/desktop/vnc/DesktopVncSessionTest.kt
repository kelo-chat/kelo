package chat.kelo.desktop.vnc

import androidx.compose.ui.input.key.Key
import chat.kelo.app.agent.FramePoint
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertSame
import kotlin.test.assertTrue

/** The page side of the desktop VNC screen: the first picture, the swap on a new size, input while held, and failures. */
class DesktopVncSessionTest {
    private val sent = ArrayList<ByteArray>()
    private val offered = ArrayList<String>()
    private val failures = ArrayList<String>()
    private var now = 0L
    private val scope = CoroutineScope(Dispatchers.Unconfined)

    private fun session(): DesktopVncSession = DesktopVncSession(
        scope = scope,
        send = { sent += it },
        offerClipboard = { offered += it },
        onFailed = { failures += it },
        macOs = false,
        dispatcher = Dispatchers.Unconfined,
        clock = { now },
    )

    private fun header(x: Int, y: Int, w: Int, h: Int, encoding: Int): ByteArray = ints(
        x shr 8, x and 0xff, y shr 8, y and 0xff, w shr 8, w and 0xff, h shr 8, h and 0xff,
        encoding ushr 24, (encoding shr 16) and 0xff, (encoding shr 8) and 0xff, encoding and 0xff,
    )

    /** A FramebufferUpdate with one Raw rectangle filled with [v] (BGRA bytes). */
    private fun rawUpdate(w: Int, h: Int, v: Int): ByteArray =
        cat(ints(0, 0, 0, 1), header(0, 0, w, h, RfbEncoding.RAW), ByteArray(w * h * 4) { v.toByte() })

    private fun pixelAt0(fb: FrameBuffer): Int {
        val b = ByteArray(fb.width * fb.height * 4)
        fb.copyTo(b)
        return b[0].toInt() and 0xff
    }

    @Test fun theFirstUpdateIsTheFirstPicture() {
        val s = session()
        assertNull(s.frame.value)
        s.toPage(cat(serverStart(4, 4), rawUpdate(4, 4, 42)))
        val fb = assertNotNull(s.frame.value)
        assertEquals(4, fb.width)
        assertEquals(42, pixelAt0(fb))
    }

    @Test fun theOldPictureStaysUntilTheNewOneDraws() {
        val s = session()
        s.toPage(cat(serverStart(4, 4), rawUpdate(4, 4, 1)))
        val old = assertNotNull(s.frame.value)
        s.reset()
        s.toPage(serverStart(8, 6))                       // the new socket's greeting: no picture yet
        assertSame(old, s.frame.value)
        s.toPage(rawUpdate(8, 6, 2))
        val fresh = assertNotNull(s.frame.value)
        assertTrue(fresh !== old)
        assertEquals(8, fresh.width)
        assertEquals(2, pixelAt0(fresh))
    }

    @Test fun theSwapAtTheCapIsDrivenByTheClock() {
        val s = session()
        s.toPage(cat(serverStart(4, 4), rawUpdate(4, 4, 1)))
        val old = assertNotNull(s.frame.value)
        now = 100
        s.reset()
        now = 100 + DesktopVncSession.ANSWER_CAP_MS - 1
        s.tick()
        assertSame(old, s.frame.value)
        now = 100 + DesktopVncSession.ANSWER_CAP_MS
        s.tick()
        assertTrue(s.frame.value !== old)
    }

    @Test fun inputIsSentOnlyWhileHeld() {
        val s = session()
        s.toPage(serverStart(4, 4))
        sent.clear()
        s.keyDown(Key.A.keyCode, 'a'.code, ctrl = false)
        s.pointer.move(FramePoint(1, 1), 0)
        assertEquals(0, sent.size)
    }

    @Test fun givingTheTabBackLiftsKeysAndButtons() {
        val s = session()
        s.toPage(serverStart(4, 4))
        s.setHolding(true)
        s.keyDown(Key.A.keyCode, 'a'.code, ctrl = false)
        s.pointer.button(RfbPointer.LEFT, true, FramePoint(1, 1))
        sent.clear()
        s.setHolding(false)
        assertContentEquals(RfbClientMessage.pointer(0, 1, 1), sent[0])
        assertContentEquals(RfbClientMessage.key(false, 0x61), sent[1])
        assertEquals(2, sent.size)
    }

    @Test fun aLostFocusLiftsKeysAndButtonsToo() {
        val s = session()
        s.toPage(serverStart(4, 4))
        s.setHolding(true)
        s.keyDown(Key.Enter.keyCode, 0, ctrl = false)
        sent.clear()
        s.focusLost()
        assertContentEquals(RfbClientMessage.key(false, 0xff0d), sent.single())
    }

    @Test fun anRfbErrorEndsWithFailed() {
        val s = session()
        s.toPage(cat(serverStart(4, 4), ints(99)))
        assertEquals(listOf("an unknown server message 99"), failures)
    }

    @Test fun aCutTextWhileHeldIsOffered() {
        val s = session()
        s.setHolding(true)
        s.toPage(cat(serverStart(4, 4), ints(3, 0, 0, 0, 0, 0, 0, 2), "hi".toByteArray()))
        assertEquals(listOf("hi"), offered)
    }

    @Test fun aCutTextWhileNotHeldIsNotOffered() {
        val s = session()
        s.toPage(cat(serverStart(4, 4), ints(3, 0, 0, 0, 0, 0, 0, 2), "hi".toByteArray()))
        assertEquals(emptyList(), offered)
    }
}
