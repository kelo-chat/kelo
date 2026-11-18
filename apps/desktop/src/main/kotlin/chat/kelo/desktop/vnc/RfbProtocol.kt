package chat.kelo.desktop.vnc

import java.io.ByteArrayOutputStream

/**
 * The RFB 3.8 numbers this client uses, from the public protocol description (rfbproto, rfbproto.rst). Only the values; the
 * behaviour is written here from that description.
 */
object RfbEncoding {
    const val RAW = 0
    const val COPY_RECT = 1
    const val TIGHT = 7
    const val DESKTOP_SIZE = -223
    const val LAST_RECT = -224
    const val CURSOR = -239
    const val EXTENDED_DESKTOP_SIZE = -308
    /** The extended clipboard pseudo-encoding (a capability; the server answers with its own caps) */
    const val EXTENDED_CLIPBOARD = 0xC0A1E5CE.toInt()
    /** JPEG quality level 6 (pseudo-encoding -26 .. -23 is levels 0..9) */
    const val JPEG_QUALITY_6 = -26
    /** Compression level 2 (pseudo-encoding -256 .. -247 is levels 0..9) */
    const val COMPRESS_LEVEL_2 = -254
}

/** The server's message types this client handles. */
object RfbServerMessage {
    const val FRAMEBUFFER_UPDATE = 0
    const val SET_COLOUR_MAP = 1
    const val BELL = 2
    const val CUT_TEXT = 3
    const val CONTINUOUS_UPDATES = 150
    const val FENCE = 248
}

/** The client's message writers. The client sends only types 0, 2, 3, 4, 5 and 6 (see the server's ClientFilter). */
object RfbClientMessage {
    const val SET_PIXEL_FORMAT = 0
    const val SET_ENCODINGS = 2
    const val UPDATE_REQUEST = 3
    const val KEY = 4
    const val POINTER = 5
    const val CUT_TEXT = 6

    /** Extended clipboard: the caps flag and the actions and formats the client answers with. */
    const val CLIP_CAPS = 1 shl 24
    const val CLIP_REQUEST = 1 shl 25
    const val CLIP_NOTIFY = 1 shl 27
    const val CLIP_PROVIDE = 1 shl 28
    const val CLIP_TEXT = 1 shl 0

    /** 32 bits per pixel, depth 24, little-endian, true colour, 8 bits per channel: the bytes come out as B, G, R, unused. */
    fun setPixelFormat(): ByteArray = ByteArrayOutputStream().apply {
        write(SET_PIXEL_FORMAT); write(0); write(0); write(0)
        pixelFormat32(this)
    }.toByteArray()

    fun setEncodings(encodings: IntArray): ByteArray = ByteArrayOutputStream().apply {
        write(SET_ENCODINGS); write(0)
        u16(this, encodings.size)
        for (e in encodings) s32(this, e)
    }.toByteArray()

    /** Asks for the rectangle; [incremental] false asks for the whole picture again (after a resize, or the first time). */
    fun updateRequest(incremental: Boolean, x: Int, y: Int, w: Int, h: Int): ByteArray = ByteArrayOutputStream().apply {
        write(UPDATE_REQUEST); write(if (incremental) 1 else 0)
        u16(this, x); u16(this, y); u16(this, w); u16(this, h)
    }.toByteArray()

    fun key(down: Boolean, keysym: Int): ByteArray = ByteArrayOutputStream().apply {
        write(KEY); write(if (down) 1 else 0); write(0); write(0)
        s32(this, keysym)
    }.toByteArray()

    /** [mask] is the buttons held: left 1, middle 2, right 4, wheel up 8, down 16, left 32, right 64. */
    fun pointer(mask: Int, x: Int, y: Int): ByteArray = ByteArrayOutputStream().apply {
        write(POINTER); write(mask and 0xff)
        u16(this, x); u16(this, y)
    }.toByteArray()

    /** The client's clipboard caps: text, with the request, notify and provide actions; the text's maximum size is 0 (unlimited is not asked). */
    fun clipboardCaps(): ByteArray = cutText(
        flags = CLIP_CAPS or CLIP_REQUEST or CLIP_NOTIFY or CLIP_PROVIDE or CLIP_TEXT,
        data = byteArrayOf(0, 0, 0, 0),
    )

    /** Asks the server for its text. */
    fun clipboardRequest(): ByteArray = cutText(flags = CLIP_REQUEST or CLIP_TEXT, data = ByteArray(0))

    /** An extended ClientCutText: the length is negative, the count of the bytes after it (flags and data). */
    private fun cutText(flags: Int, data: ByteArray): ByteArray = ByteArrayOutputStream().apply {
        write(CUT_TEXT); write(0); write(0); write(0)
        s32(this, -(4 + data.size))
        s32(this, flags)
        write(data)
    }.toByteArray()

    /** The 16-byte pixel format: 32 bpp, depth 24, little-endian, true colour, max 255 each, shifts R 16, G 8, B 0. */
    internal fun pixelFormat32(out: ByteArrayOutputStream) {
        out.write(32); out.write(24); out.write(0); out.write(1)
        u16(out, 255); u16(out, 255); u16(out, 255)
        out.write(16); out.write(8); out.write(0)
        out.write(0); out.write(0); out.write(0)
    }

    internal fun u16(out: ByteArrayOutputStream, v: Int) {
        out.write((v shr 8) and 0xff); out.write(v and 0xff)
    }

    internal fun s32(out: ByteArrayOutputStream, v: Int) {
        out.write((v shr 24) and 0xff); out.write((v shr 16) and 0xff); out.write((v shr 8) and 0xff); out.write(v and 0xff)
    }
}
