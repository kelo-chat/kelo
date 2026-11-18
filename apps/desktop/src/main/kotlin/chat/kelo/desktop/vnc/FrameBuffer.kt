package chat.kelo.desktop.vnc

/**
 * The server's screen as the client keeps it: BGRA bytes, a row is width * 4 bytes (the byte order of Skia's N32 on little-endian, so
 * the drawer copies it as it is). The session writes; the drawer [copyTo]s under the same lock. [version] counts finished updates.
 */
class FrameBuffer(w: Int, h: Int) {
    var width = w
        private set
    var height = h
        private set
    private var px = ByteArray(w * h * 4)
    private val lock = Any()
    private var finished = 0L

    /** Bumped by [finish] once per update the server completed. */
    val version: Long get() = synchronized(lock) { finished }

    /** A new size: the picture starts black, the session asks for all of it. */
    fun resize(w: Int, h: Int) = synchronized(lock) {
        width = w
        height = h
        px = ByteArray(w * h * 4)
    }

    fun contains(x: Int, y: Int, w: Int, h: Int): Boolean =
        x >= 0 && y >= 0 && w >= 0 && h >= 0 && x + w <= width && y + h <= height

    /** [src] holds w * h pixels of BGRA, row by row. The fourth byte is set opaque: the server's pad byte is not alpha. */
    fun put(x: Int, y: Int, w: Int, h: Int, src: ByteArray) = synchronized(lock) {
        val row = w * 4
        for (r in 0 until h) {
            val at = ((y + r) * width + x) * 4
            System.arraycopy(src, r * row, px, at, row)
            for (i in at + 3 until at + row step 4) px[i] = 0xff.toByte()
        }
    }

    fun fill(x: Int, y: Int, w: Int, h: Int, red: Int, green: Int, blue: Int) = synchronized(lock) {
        for (r in 0 until h) {
            var i = ((y + r) * width + x) * 4
            repeat(w) {
                px[i] = blue.toByte(); px[i + 1] = green.toByte(); px[i + 2] = red.toByte(); px[i + 3] = 0xff.toByte()
                i += 4
            }
        }
    }

    /** Copies a rectangle inside the picture. Overlapping rows are walked from the far side so a row is never read after it changed. */
    fun copyRect(sx: Int, sy: Int, dx: Int, dy: Int, w: Int, h: Int) = synchronized(lock) {
        val row = w * 4
        val order = if (dy > sy) (h - 1 downTo 0) else (0 until h)
        for (r in order) {
            System.arraycopy(px, ((sy + r) * width + sx) * 4, px, ((dy + r) * width + dx) * 4, row)
        }
    }

    /** Marks the end of an update. */
    fun finish() = synchronized(lock) { finished++ }

    /** What [copyTo] copied: the size and the version it was copied at. */
    data class Snapshot(val width: Int, val height: Int, val version: Long)

    /** Copies the picture into [dst] (which must be the size of the picture: width * height * 4); null when the size is different. */
    fun copyTo(dst: ByteArray): Snapshot? = synchronized(lock) {
        if (dst.size != px.size) return null
        System.arraycopy(px, 0, dst, 0, px.size)
        Snapshot(width, height, finished)
    }
}
