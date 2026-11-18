package chat.kelo.desktop.vnc

import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import java.util.zip.Deflater
import javax.imageio.ImageIO
import kotlin.math.abs
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The Tight rectangles, built in the test with zlib (SYNC_FLUSH, one deflater per stream id) and read back through a session. */
class TightDecoderTest {
    /** The screen is wider than the rectangles of the palette test (9 pixels) */
    private val FW = 16

    /** The sending side: one zlib stream per id, kept across rectangles like the server does. */
    private inner class Writer {
        private val deflaters = Array(4) { Deflater() }

        fun reset(stream: Int) = deflaters[stream].reset()

        /** A compact length and the zlib data for [data] on [stream] (the data continues the stream's earlier output). */
        fun zlib(stream: Int, data: ByteArray): ByteArray {
            val d = deflaters[stream]
            d.setInput(data)
            val out = ByteArrayOutputStream()
            val buf = ByteArray(4096)
            while (true) {
                val n = d.deflate(buf, 0, buf.size, Deflater.SYNC_FLUSH)
                out.write(buf, 0, n)
                if (n < buf.size) break
            }
            return compact(out.size()) + out.toByteArray()
        }
    }

    private fun compact(len: Int): ByteArray = when {
        len < 128 -> byteArrayOf(len.toByte())
        len < 16384 -> byteArrayOf((0x80 or (len and 0x7f)).toByte(), (len shr 7).toByte())
        else -> byteArrayOf((0x80 or (len and 0x7f)).toByte(), (0x80 or ((len shr 7) and 0x7f)).toByte(), (len shr 14).toByte())
    }

    private fun cat(vararg parts: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        parts.forEach { out.write(it) }
        return out.toByteArray()
    }

    private fun header(x: Int, y: Int, w: Int, h: Int, encoding: Int): ByteArray = ints(
        x shr 8, x and 0xff, y shr 8, y and 0xff, w shr 8, w and 0xff, h shr 8, h and 0xff,
        encoding ushr 24, (encoding shr 16) and 0xff, (encoding shr 8) and 0xff, encoding and 0xff,
    )

    /** A FramebufferUpdate with one Tight rectangle of [w] x [h] at ([x], [y]). */
    private fun tightRect(x: Int, y: Int, w: Int, h: Int, body: ByteArray): ByteArray =
        cat(ints(0, 0, 0, 1), header(x, y, w, h, RfbEncoding.TIGHT), body)

    /** Runs a session on a FW x 8 screen with the given updates, then returns the frame. */
    private fun frameAfter(vararg updates: ByteArray): FrameBuffer {
        val chunks = ArrayList<ByteArray>()
        chunks += serverStart(FW, 8)
        chunks += updates.toList()
        return run(chunks, FrameBuffer(FW, 8)).frame
    }

    private fun pixels(fb: FrameBuffer): ByteArray = ByteArray(fb.width * fb.height * 4).also { fb.copyTo(it) }

    /** Pixel (x, y) as R, G, B (the frame is BGRA). */
    private fun rgb(b: ByteArray, w: Int, x: Int, y: Int): List<Int> {
        val o = (y * w + x) * 4
        return listOf(b[o + 2].toInt() and 0xff, b[o + 1].toInt() and 0xff, b[o].toInt() and 0xff)
    }

    private fun rgbBytes(vararg px: Int): ByteArray = ByteArray(px.size) { px[it].toByte() }

    @Test fun fillPaintsTheWholeRectangle() {
        val fb = frameAfter(tightRect(0, 0, 4, 2, cat(ints(0x80), ints(1, 2, 3))))
        val b = pixels(fb)
        assertEquals(listOf(1, 2, 3), rgb(b, FW, 0, 0))
        assertEquals(listOf(1, 2, 3), rgb(b, FW, 3, 1))
        assertEquals(listOf(0, 0, 0), rgb(b, FW, 4, 0))
    }

    @Test fun basicCopyUnderTwelveBytesIsNotCompressed() {
        val fb = frameAfter(tightRect(0, 0, 2, 1, cat(ints(0x00), rgbBytes(9, 8, 7, 6, 5, 4))))
        val b = pixels(fb)
        assertEquals(listOf(9, 8, 7), rgb(b, FW, 0, 0))
        assertEquals(listOf(6, 5, 4), rgb(b, FW, 1, 0))
    }

    @Test fun basicCopyOverTwelveBytesIsZlib() {
        val w = Writer()
        val data = ByteArray(4 * 2 * 3) { it.toByte() }
        val fb = frameAfter(tightRect(0, 0, 4, 2, cat(ints(0x00), w.zlib(0, data))))
        val b = pixels(fb)
        assertEquals(listOf(0, 1, 2), rgb(b, FW, 0, 0))
        assertEquals(listOf(21, 22, 23), rgb(b, FW, 3, 1))
    }

    @Test fun aStreamContinuesOverTwoRectangles() {
        val w = Writer()
        val first = ByteArray(12) { (it + 1).toByte() }
        val second = ByteArray(12) { (it + 50).toByte() }
        val fb = frameAfter(
            tightRect(0, 0, 4, 1, cat(ints(0x00), w.zlib(0, first))),
            tightRect(0, 1, 4, 1, cat(ints(0x00), w.zlib(0, second))),
        )
        val b = pixels(fb)
        assertEquals(listOf(1, 2, 3), rgb(b, FW, 0, 0))
        assertEquals(listOf(50, 51, 52), rgb(b, FW, 0, 1))
        assertEquals(listOf(59, 60, 61), rgb(b, FW, 3, 1))
    }

    @Test fun aResetBitStartsTheStreamAgain() {
        val w = Writer()
        val first = ByteArray(12) { 7 }
        val second = ByteArray(12) { (it * 2).toByte() }
        w.reset(0)
        val fb = frameAfter(
            tightRect(0, 0, 4, 1, cat(ints(0x00), w.zlib(0, first))),
            tightRect(0, 1, 4, 1, cat(ints(0x01), run { w.reset(0); w.zlib(0, second) })),   // bit 0 = reset stream 0 before this rectangle
        )
        assertEquals(listOf(0, 2, 4), rgb(pixels(fb), FW, 0, 1))
    }

    @Test fun paletteOfTwoColoursUsesOneBitPerPixelWithRowPadding() {
        // 9 pixels wide: each row is two bytes, the last bit of the second byte is pixel 8
        val body = cat(ints(0x40, 1), ints(1), rgbBytes(0, 0, 0, 255, 255, 255), ints(0xaa, 0x80, 0x00, 0x00))
        val fb = frameAfter(tightRect(0, 0, 9, 2, body))
        val b = pixels(fb)
        assertEquals(listOf(255, 255, 255), rgb(b, FW, 0, 0))   // index 1
        assertEquals(listOf(0, 0, 0), rgb(b, FW, 1, 0))         // index 0
        assertEquals(listOf(255, 255, 255), rgb(b, FW, 6, 0))   // bits 1010 1010: pixel 6 is index 1, pixel 7 index 0
        assertEquals(listOf(0, 0, 0), rgb(b, FW, 7, 0))
        assertEquals(listOf(255, 255, 255), rgb(b, FW, 8, 0))   // the second byte's top bit
        assertEquals(listOf(0, 0, 0), rgb(b, FW, 0, 1))
    }

    @Test fun paletteOfFiveColoursUsesABytePerPixelThroughZlib() {
        val w = Writer()
        val table = rgbBytes(10, 0, 0, 0, 10, 0, 0, 0, 10, 10, 10, 10, 200, 200, 200)
        val indexes = ByteArray(6 * 3) { (it % 5).toByte() }
        val fb = frameAfter(tightRect(0, 0, 6, 3, cat(ints(0x40, 1), ints(4), table, w.zlib(0, indexes))))
        val b = pixels(fb)
        assertEquals(listOf(10, 0, 0), rgb(b, FW, 0, 0))        // index 0
        assertEquals(listOf(200, 200, 200), rgb(b, FW, 4, 0))   // index 4
        assertEquals(listOf(0, 0, 10), rgb(b, FW, 2, 0))        // index 2
    }

    @Test fun gradientRebuildsThePicture() {
        // intensities 10, 20 / 30, 40 in every channel; the differences from the prediction are 10, 10 / 20, 0
        val diffs = ByteArray(2 * 2 * 3)
        val d = intArrayOf(10, 10, 20, 0)
        for (i in 0 until 4) for (c in 0 until 3) diffs[i * 3 + c] = d[i].toByte()
        val w = Writer()
        val fb = frameAfter(tightRect(0, 0, 2, 2, cat(ints(0x40, 2), w.zlib(0, diffs))))
        val b = pixels(fb)
        assertEquals(listOf(10, 10, 10), rgb(b, FW, 0, 0))
        assertEquals(listOf(20, 20, 20), rgb(b, FW, 1, 0))
        assertEquals(listOf(30, 30, 30), rgb(b, FW, 0, 1))
        assertEquals(listOf(40, 40, 40), rgb(b, FW, 1, 1))
    }

    @Test fun jpegIsDecodedToItsColour() {
        val image = BufferedImage(8, 8, BufferedImage.TYPE_INT_RGB)
        for (y in 0 until 8) for (x in 0 until 8) image.setRGB(x, y, (200 shl 16) or (100 shl 8) or 50)
        val jpeg = ByteArrayOutputStream().also { ImageIO.write(image, "jpeg", it) }.toByteArray()
        val fb = frameAfter(tightRect(0, 0, 8, 8, cat(ints(0x90), compact(jpeg.size), jpeg)))
        val b = pixels(fb)
        val got = rgb(b, FW, 4, 4)
        assertTrue(abs(got[0] - 200) <= 6 && abs(got[1] - 100) <= 6 && abs(got[2] - 50) <= 6, "got $got")
    }

    @Test fun aBadCompressedLengthEndsTheSession() {
        // a zlib length far longer than the rectangle can need
        val run = run(listOf(serverStart(8, 8), tightRect(0, 0, 4, 2, cat(ints(0x00), compact(5000), ByteArray(5000)))), FrameBuffer(8, 8))
        assertEquals(1, run.failures.size)
        assertTrue(run.failures.single().contains("is not allowed"), run.failures.toString())
    }

    @Test fun garbageThatIsNotZlibEndsTheSession() {
        val run = run(listOf(serverStart(8, 8), tightRect(0, 0, 4, 2, cat(ints(0x00), compact(6), ints(0xff, 0xff, 0xff, 0xff, 0xff, 0xff)))), FrameBuffer(8, 8))
        assertEquals(listOf("the Tight data is not valid zlib"), run.failures)
    }

    @Test fun theDecodedBytesAreBgraWithAnOpaqueAlpha() {
        val fb = frameAfter(tightRect(0, 0, 1, 1, cat(ints(0x80), ints(4, 5, 6))))
        assertContentEquals(ints(6, 5, 4, 0xff), pixels(fb).copyOfRange(0, 4))
    }
}
