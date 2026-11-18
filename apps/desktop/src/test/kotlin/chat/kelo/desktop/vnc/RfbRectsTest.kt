package chat.kelo.desktop.vnc

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals

/** The rectangles of a FramebufferUpdate: Raw, CopyRect, the size changes and LastRect, framed against a scripted server. */
class RfbRectsTest {
    /** A rectangle header: x, y, w, h (u16 each) and the encoding (s32). */
    private fun header(x: Int, y: Int, w: Int, h: Int, encoding: Int): ByteArray = ints(
        x shr 8, x and 0xff, y shr 8, y and 0xff, w shr 8, w and 0xff, h shr 8, h and 0xff,
        encoding ushr 24, (encoding shr 16) and 0xff, (encoding shr 8) and 0xff, encoding and 0xff,
    )

    /** A FramebufferUpdate with [count] rectangles (0xffff for "until LastRect") and their bytes. */
    private fun update(count: Int, vararg rects: ByteArray): ByteArray = cat(ints(0, 0, count shr 8, count and 0xff), *rects)

    /** Pixel (x, y) of a frame [w] pixels wide: blue, green, red (the fourth byte is always opaque). */
    private fun pixel(b: ByteArray, w: Int, x: Int, y: Int): List<Int> =
        (0 until 3).map { b[(y * w + x) * 4 + it].toInt() and 0xff }

    private fun picture(fb: FrameBuffer): ByteArray = ByteArray(fb.width * fb.height * 4).also { fb.copyTo(it) }

    private fun sequential(n: Int) = ByteArray(n) { it.toByte() }

    @Test fun rawPixelsLandWhereTheRectSays() {
        val r = run(listOf(serverStart(4, 4), update(1, header(1, 1, 2, 2, RfbEncoding.RAW), sequential(16))))
        val b = picture(r.frame)
        assertEquals(listOf(0, 1, 2), pixel(b, 4, 1, 1))
        assertEquals(listOf(4, 5, 6), pixel(b, 4, 2, 1))
        assertEquals(listOf(8, 9, 10), pixel(b, 4, 1, 2))
        assertEquals(listOf(12, 13, 14), pixel(b, 4, 2, 2))
        assertEquals(listOf(0, 0, 0), pixel(b, 4, 0, 0))
        assertEquals(1, r.updates)
    }

    @Test fun copyRectCopiesInsideThePicture() {
        val first = update(1, header(0, 0, 4, 4, RfbEncoding.RAW), sequential(64))
        val copy = update(1, header(2, 2, 2, 2, RfbEncoding.COPY_RECT), ints(0, 0, 0, 0))
        val r = run(listOf(serverStart(4, 4), first, copy))
        val b = picture(r.frame)
        assertEquals(pixel(sequential(64), 4, 0, 0), pixel(b, 4, 2, 2))
        assertEquals(pixel(sequential(64), 4, 1, 1), pixel(b, 4, 3, 3))
        assertEquals(2, r.updates)
    }

    @Test fun desktopSizeResizesAndAsksForTheWholePicture() {
        val r = run(listOf(serverStart(4, 4), update(1, header(0, 0, 8, 6, RfbEncoding.DESKTOP_SIZE))))
        assertEquals(8, r.frame.width)
        assertEquals(6, r.frame.height)
        val last = RfbClientMessage.updateRequest(false, 0, 0, 8, 6)
        assertContentEquals(last, r.sent.copyOfRange(r.sent.size - last.size, r.sent.size))
    }

    @Test fun extendedDesktopSizeReadsItsScreensAndResizes() {
        val ext = update(1, header(0, 0, 10, 5, RfbEncoding.EXTENDED_DESKTOP_SIZE), ints(1, 0, 0, 0), ByteArray(16))
        val r = run(listOf(serverStart(4, 4), ext))
        assertEquals(10, r.frame.width)
        assertEquals(5, r.frame.height)
        assertEquals(listOf("the screen closed"), r.failures)
    }

    @Test fun lastRectEndsAnUpdateThatCountsNoRectangles() {
        val u = update(0xffff, header(0, 0, 1, 1, RfbEncoding.RAW), ints(9, 8, 7, 6), header(0, 0, 0, 0, RfbEncoding.LAST_RECT))
        val r = run(listOf(serverStart(4, 4), u))
        assertEquals(1, r.updates)
        assertEquals(listOf(9, 8, 7), pixel(picture(r.frame), 4, 0, 0))
    }

    @Test fun aRectOutsideThePictureEndsTheSession() {
        val r = run(listOf(serverStart(4, 4), update(1, header(3, 3, 2, 2, RfbEncoding.RAW), ByteArray(16))))
        assertEquals(listOf("a rectangle outside the picture"), r.failures)
        assertEquals(0, r.updates)
    }

    @Test fun anEncodingThisClientDoesNotReadEndsTheSession() {
        val r = run(listOf(serverStart(4, 4), update(1, header(0, 0, 1, 1, 99))))
        assertEquals(listOf("an encoding this client does not read: 99"), r.failures)
    }

    @Test fun aCursorRectIsReadWhollyAndTheNextRectStillLinesUp() {
        val cursor = header(1, 1, 2, 2, RfbEncoding.CURSOR) + sequential(16) + ints(0x80, 0x00)
        val raw = header(0, 0, 1, 1, RfbEncoding.RAW) + ints(9, 8, 7, 6)
        val r = run(listOf(serverStart(4, 4), update(2, cursor, raw)))
        assertEquals(1, r.updates)
        assertEquals(listOf(9, 8, 7), pixel(picture(r.frame), 4, 0, 0))
        assertEquals(listOf("the screen closed"), r.failures)
    }

    @Test fun aZeroSizeScreenIsRefused() {
        val r = run(listOf(serverStart(4, 4), update(1, header(0, 0, 0, 5, RfbEncoding.DESKTOP_SIZE))))
        assertEquals(listOf("a screen size of 0 x 5 is not allowed"), r.failures)
    }
}
