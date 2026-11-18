package chat.kelo.desktop.vnc

/** The server's cursor: ARGB pixels (row by row) and where its hotspot is. */
class RfbCursorImage(val width: Int, val height: Int, val hotX: Int, val hotY: Int, val argb: IntArray)

/**
 * The Cursor pseudo-encoding (-239): the cursor's pixels in the client's pixel format (BGRX), then a one-bit mask (rows padded to a
 * byte, the leftmost pixel in the top bit). A set bit is a visible pixel.
 */
object RfbCursor {
    const val MAX_SIDE = 256

    fun image(w: Int, h: Int, hotX: Int, hotY: Int, pixels: ByteArray, mask: ByteArray): RfbCursorImage {
        val rowBytes = (w + 7) / 8
        val argb = IntArray(w * h)
        for (y in 0 until h) for (x in 0 until w) {
            val i = y * w + x
            val visible = (mask[y * rowBytes + x / 8].toInt() shr (7 - x % 8)) and 1 == 1
            val blue = pixels[i * 4].toInt() and 0xff
            val green = pixels[i * 4 + 1].toInt() and 0xff
            val red = pixels[i * 4 + 2].toInt() and 0xff
            argb[i] = (if (visible) 0xff000000.toInt() else 0) or (red shl 16) or (green shl 8) or blue
        }
        return RfbCursorImage(w, h, hotX, hotY, argb)
    }

    /** Bytes of pixels and of the mask for a [w] x [h] cursor. */
    fun pixelBytes(w: Int, h: Int): Int = w * h * 4

    fun maskBytes(w: Int, h: Int): Int = ((w + 7) / 8) * h
}
