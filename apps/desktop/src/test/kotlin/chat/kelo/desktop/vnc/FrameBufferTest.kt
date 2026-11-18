package chat.kelo.desktop.vnc

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertNull

class FrameBufferTest {
    private fun bytes(fb: FrameBuffer): ByteArray {
        val out = ByteArray(fb.width * fb.height * 4)
        fb.copyTo(out)
        return out
    }

    /** Pixel (x, y)'s blue byte, for a frame of [w] pixels per row. */
    private fun blue(b: ByteArray, w: Int, x: Int, y: Int): Int = b[(y * w + x) * 4].toInt() and 0xff

    /** A frame whose blue byte of pixel (x, y) is y * w + x + 1. */
    private fun numbered(w: Int, h: Int): FrameBuffer {
        val fb = FrameBuffer(w, h)
        val src = ByteArray(w * h * 4)
        for (i in 0 until w * h) { src[i * 4] = (i + 1).toByte(); src[i * 4 + 3] = 0xff.toByte() }
        fb.put(0, 0, w, h, src)
        return fb
    }

    @Test fun putCopiesTheRowsWhereTheRectSays() {
        val fb = FrameBuffer(4, 3)
        fb.put(1, 1, 2, 2, byteArrayOf(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16))
        val b = bytes(fb)
        assertEquals(0, b[0].toInt())                               // (0, 0) untouched
        assertContentEquals(byteArrayOf(1, 2, 3, -1), b.copyOfRange((1 * 4 + 1) * 4, (1 * 4 + 1) * 4 + 4))
        assertContentEquals(byteArrayOf(5, 6, 7, -1), b.copyOfRange((1 * 4 + 2) * 4, (1 * 4 + 2) * 4 + 4))
        assertContentEquals(byteArrayOf(9, 10, 11, -1), b.copyOfRange((2 * 4 + 1) * 4, (2 * 4 + 1) * 4 + 4))
        assertContentEquals(byteArrayOf(13, 14, 15, -1), b.copyOfRange((2 * 4 + 2) * 4, (2 * 4 + 2) * 4 + 4))
    }

    @Test fun fillWritesBlueGreenRedAndOpaque() {
        val fb = FrameBuffer(2, 2)
        fb.fill(0, 0, 1, 1, red = 10, green = 20, blue = 30)
        assertContentEquals(byteArrayOf(30, 20, 10, 0xff.toByte()), bytes(fb).copyOfRange(0, 4))
        assertEquals(0, bytes(fb)[4].toInt())                       // the next pixel is still black
    }

    @Test fun copyRectDownRightOverlapsSafely() {
        val fb = numbered(3, 3)
        fb.copyRect(0, 0, 1, 1, 2, 2)                               // new (x, y) = old (x - 1, y - 1) for x, y in 1..2
        val b = bytes(fb)
        assertEquals(1, blue(b, 3, 1, 1))
        assertEquals(2, blue(b, 3, 2, 1))
        assertEquals(4, blue(b, 3, 1, 2))
        assertEquals(5, blue(b, 3, 2, 2))
        assertEquals(1, blue(b, 3, 0, 0))                           // the source row stays as it was
        assertEquals(3, blue(b, 3, 2, 0))
    }

    @Test fun copyRectUpLeftOverlapsSafely() {
        val fb = numbered(3, 3)
        fb.copyRect(1, 1, 0, 0, 2, 2)                               // new (x, y) = old (x + 1, y + 1) for x, y in 0..1
        val b = bytes(fb)
        assertEquals(5, blue(b, 3, 0, 0))
        assertEquals(6, blue(b, 3, 1, 0))
        assertEquals(8, blue(b, 3, 0, 1))
        assertEquals(9, blue(b, 3, 1, 1))
    }

    @Test fun resizeStartsBlack() {
        val fb = numbered(2, 2)
        fb.resize(3, 1)
        assertEquals(3, fb.width)
        assertEquals(1, fb.height)
        assertContentEquals(ByteArray(12), bytes(fb))
    }

    @Test fun versionCountsFinishedUpdatesOnly() {
        val fb = FrameBuffer(1, 1)
        fb.put(0, 0, 1, 1, byteArrayOf(1, 2, 3, 4))
        assertEquals(0L, fb.version)
        fb.finish()
        assertEquals(1L, fb.version)
    }

    @Test fun copyToNeedsThePictureSize() {
        val fb = FrameBuffer(2, 2)
        assertNull(fb.copyTo(ByteArray(3)))
        val snap = fb.copyTo(ByteArray(16))
        assertEquals(FrameBuffer.Snapshot(2, 2, 0L), snap)
    }
}
