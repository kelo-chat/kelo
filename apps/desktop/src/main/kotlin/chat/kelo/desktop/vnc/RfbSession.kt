package chat.kelo.desktop.vnc

import java.io.EOFException

/** A protocol error or an unsupported server: the session ends. The message never includes the screen's content. */
class RfbError(message: String) : Exception(message)

/** What a session tells its host. Called from the session's coroutine. */
interface RfbEvents {
    /** A whole update was drawn into the frame */
    fun updated() {}

    /** The session ended for a reason the owner did not cause: a protocol error, an unsupported server, a closed socket */
    fun failed(why: String) {}

    /** The server's cursor changed (null: no cursor to show) */
    fun cursor(image: RfbCursorImage?) {}
}

/**
 * One RFB connection: the handshake (RFB 3.8, security None), then the server's messages until the socket ends. [send] takes the
 * client's bytes; [reader] gets the server's bytes from the relay. Run it once, in its own coroutine.
 */
class RfbSession(
    private val reader: RfbByteReader,
    private val send: (ByteArray) -> Unit,
    val frame: FrameBuffer,
    private val events: RfbEvents,
    private val clipboard: RfbClipboard = RfbClipboard({}, { false }, {}),
) {
    private val tight = TightDecoder()

    /** The next request asks for the whole picture (after a resize) instead of the changes. */
    private var wholeNext = true

    suspend fun run() {
        try {
            handshake()
            while (true) message(reader.u8())
        } catch (e: EOFException) {
            events.failed("the screen closed")
        } catch (e: RfbError) {
            events.failed(e.message ?: "the screen could not be read")
        } catch (e: IllegalArgumentException) {
            events.failed("the screen sent a size that cannot be read")
        } catch (e: IndexOutOfBoundsException) {
            events.failed("the screen sent a rectangle outside the picture")
        } finally {
            tight.close()
        }
    }

    private suspend fun handshake() {
        val version = String(reader.bytes(12), Charsets.US_ASCII)
        val minor = Regex("RFB 003\\.(\\d{3})\\n").matchEntire(version)?.groupValues?.get(1)?.toInt()
            ?: throw RfbError("the server does not speak RFB 3")
        // answer with the older of the two versions, as the protocol asks
        val ours = minOf(minor, 8)
        send("RFB 003.%03d\n".format(ours).toByteArray(Charsets.US_ASCII))
        if (minor >= 7) {
            val count = reader.u8()
            if (count == 0) throw RfbError(reason())
            val types = reader.bytes(count)
            if (types.none { it.toInt() == SECURITY_NONE }) throw RfbError("the server asks for a password, which this client does not do")
        } else {
            if (reader.s32() != SECURITY_NONE) throw RfbError("the server asks for a password, which this client does not do")
        }
        send(byteArrayOf(SECURITY_NONE.toByte()))
        if (minor >= 7) {
            val result = reader.s32()
            if (result != 0) throw RfbError(if (minor >= 8) reason() else "the server refused the connection")
        }
        send(byteArrayOf(1))   // ClientInit: shared, so the owner's screen is not taken from the phone
        val width = reader.u16()
        val height = reader.u16()
        reader.skip(16)        // the server's own pixel format: the client asks for its own below
        val name = reader.s32()
        checkLength(name)
        reader.skip(name)
        checkSize(width, height)
        frame.resize(width, height)
        send(RfbClientMessage.setPixelFormat())
        send(RfbClientMessage.setEncodings(ENCODINGS))
        wholeNext = true
        requestUpdate()
    }

    private suspend fun reason(): String {
        val n = reader.s32()
        checkLength(n)
        return String(reader.bytes(n), Charsets.UTF_8).take(200)
    }

    private suspend fun message(type: Int) {
        when (type) {
            RfbServerMessage.FRAMEBUFFER_UPDATE -> framebufferUpdate()
            RfbServerMessage.SET_COLOUR_MAP -> {
                reader.skip(1)
                reader.skip(2)
                val n = reader.u16()
                reader.skip(n * 6)
            }
            RfbServerMessage.BELL -> Unit
            RfbServerMessage.CUT_TEXT -> cutText()
            RfbServerMessage.CONTINUOUS_UPDATES -> Unit
            RfbServerMessage.FENCE -> {
                reader.skip(3)
                reader.s32()                    // flags: this client asks for no fence, so there is nothing to answer
                reader.skip(reader.u8())
            }
            else -> throw RfbError("an unknown server message $type")
        }
    }

    private suspend fun framebufferUpdate() {
        reader.skip(1)
        val count = reader.u16()
        var read = 0
        // a server that counts its rectangles ends after them; 0xffff means "until LastRect"
        while (count == 0xffff || read < count) {
            val x = reader.u16()
            val y = reader.u16()
            val w = reader.u16()
            val h = reader.u16()
            val encoding = reader.s32()
            if (encoding == RfbEncoding.LAST_RECT) break
            rect(x, y, w, h, encoding)
            read++
        }
        frame.finish()
        events.updated()
        requestUpdate()
    }

    private suspend fun rect(x: Int, y: Int, w: Int, h: Int, encoding: Int) {
        when (encoding) {
            RfbEncoding.RAW -> {
                checkRect(x, y, w, h)
                frame.put(x, y, w, h, reader.bytes(bytesOf(w, h)))
            }
            RfbEncoding.TIGHT -> {
                checkRect(x, y, w, h)
                tight.rect(reader, frame, x, y, w, h)
            }
            RfbEncoding.CURSOR -> {
                if (w == 0 || h == 0) {
                    events.cursor(null)
                } else {
                    if (w > RfbCursor.MAX_SIDE || h > RfbCursor.MAX_SIDE) throw RfbError("a cursor of $w x $h is not allowed")
                    val pixels = reader.bytes(RfbCursor.pixelBytes(w, h))
                    val mask = reader.bytes(RfbCursor.maskBytes(w, h))
                    events.cursor(RfbCursor.image(w, h, x, y, pixels, mask))
                }
            }
            RfbEncoding.COPY_RECT -> {
                val sx = reader.u16()
                val sy = reader.u16()
                checkRect(x, y, w, h)
                checkRect(sx, sy, w, h)
                frame.copyRect(sx, sy, x, y, w, h)
            }
            RfbEncoding.DESKTOP_SIZE -> {
                checkSize(w, h)
                frame.resize(w, h)
                wholeNext = true
            }
            RfbEncoding.EXTENDED_DESKTOP_SIZE -> {
                val screens = reader.u8()
                reader.skip(3)
                reader.skip(screens * 16)
                checkSize(w, h)
                frame.resize(w, h)
                wholeNext = true
            }
            else -> throw RfbError("an encoding this client does not read: $encoding")
        }
    }

    private suspend fun cutText() {
        reader.skip(3)
        val n = reader.s32()
        val size = if (n < 0) -n else n
        checkLength(size)
        val text = reader.bytes(size)
        if (n < 0) clipboard.extended(text) else clipboard.legacy(text)
    }

    private fun requestUpdate() {
        val request = if (wholeNext) RfbClientMessage.updateRequest(false, 0, 0, frame.width, frame.height)
            else RfbClientMessage.updateRequest(true, 0, 0, frame.width, frame.height)
        wholeNext = false
        send(request)
    }

    private fun checkLength(n: Int) {
        if (n < 0 || n > MAX_BYTES) throw RfbError("a length of $n bytes is not allowed")
    }

    /** A rectangle must lie inside the picture; a sub-rectangle of zero size is fine. */
    private fun checkRect(x: Int, y: Int, w: Int, h: Int) {
        if (!frame.contains(x, y, w, h)) throw RfbError("a rectangle outside the picture")
    }

    private fun checkSize(w: Int, h: Int) {
        if (w <= 0 || h <= 0 || w > MAX_SIDE || h > MAX_SIDE) throw RfbError("a screen size of $w x $h is not allowed")
    }

    private fun bytesOf(w: Int, h: Int): Int {
        val n = w.toLong() * h * 4
        if (n > MAX_BYTES) throw RfbError("a rectangle of $w x $h is too large")
        return n.toInt()
    }

    companion object {
        const val SECURITY_NONE = 1
        const val MAX_BYTES = 64 * 1024 * 1024
        const val MAX_SIDE = 16_384

        /** Preferred first: Tight, CopyRect, Raw, the size and cursor pseudo-encodings, the clipboard, JPEG quality 6, compression 2. */
        val ENCODINGS = intArrayOf(
            RfbEncoding.TIGHT, RfbEncoding.COPY_RECT, RfbEncoding.RAW, RfbEncoding.DESKTOP_SIZE, RfbEncoding.EXTENDED_DESKTOP_SIZE,
            RfbEncoding.LAST_RECT, RfbEncoding.CURSOR, RfbEncoding.EXTENDED_CLIPBOARD, RfbEncoding.JPEG_QUALITY_6, RfbEncoding.COMPRESS_LEVEL_2,
        )
    }
}
