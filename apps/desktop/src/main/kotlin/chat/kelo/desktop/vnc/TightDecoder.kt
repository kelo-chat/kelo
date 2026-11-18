package chat.kelo.desktop.vnc

import java.io.ByteArrayInputStream
import java.util.zip.DataFormatException
import java.util.zip.Inflater
import javax.imageio.ImageIO

/**
 * The Tight rectangle (RFB encoding 7). A session keeps four zlib streams: a stream is reset by the control byte's low bits, and its
 * data continues from one rectangle to the next. Pixels are TPIXELs: three bytes, red, green, blue (32 bpp, depth 24, 8-bit channels).
 * The decoded pixels are written as BGRA into the frame.
 */
class TightDecoder {
    private val streams = Array(4) { Inflater() }

    fun close() {
        streams.forEach { it.end() }
    }

    /** Decodes one Tight rectangle of [w] x [h] at ([x], [y]). The rectangle has been checked to lie inside [frame]. */
    suspend fun rect(reader: RfbByteReader, frame: FrameBuffer, x: Int, y: Int, w: Int, h: Int) {
        val control = reader.u8()
        for (i in 0 until 4) if (control and (1 shl i) != 0) streams[i].reset()
        when (control and 0xf0) {
            FILL -> {
                val p = reader.bytes(3)
                frame.fill(x, y, w, h, red = p[0].toInt() and 0xff, green = p[1].toInt() and 0xff, blue = p[2].toInt() and 0xff)
            }
            JPEG -> frame.put(x, y, w, h, jpeg(reader, w, h))
            else -> {
                if (control and 0x80 != 0) throw RfbError("an unknown Tight compression $control")
                val filter = if (control and 0x40 != 0) reader.u8() else COPY_FILTER
                val stream = (control shr 4) and 3
                when (filter) {
                    COPY_FILTER -> frame.put(x, y, w, h, toBgra(data(reader, stream, w * h * 3), w * h))
                    PALETTE_FILTER -> frame.put(x, y, w, h, palette(reader, stream, w, h))
                    GRADIENT_FILTER -> frame.put(x, y, w, h, toBgra(gradient(data(reader, stream, w * h * 3), w, h), w * h))
                    else -> throw RfbError("an unknown Tight filter $filter")
                }
            }
        }
    }

    private suspend fun jpeg(reader: RfbByteReader, w: Int, h: Int): ByteArray {
        val len = compactLength(reader)
        checkLength(len, JPEG_SLACK + w * h * 4)
        val image = ImageIO.read(ByteArrayInputStream(reader.bytes(len))) ?: throw RfbError("a Tight JPEG that cannot be read")
        if (image.width != w || image.height != h) throw RfbError("a Tight JPEG of the wrong size")
        val argb = image.getRGB(0, 0, w, h, null, 0, w)
        val out = ByteArray(w * h * 4)
        for (i in argb.indices) {
            val p = argb[i]
            out[i * 4] = p.toByte()
            out[i * 4 + 1] = (p shr 8).toByte()
            out[i * 4 + 2] = (p shr 16).toByte()
            out[i * 4 + 3] = 0xff.toByte()
        }
        return out
    }

    /** The palette (read as it is, before the indexes), then the indexes: one bit per pixel for two colours, else one byte. */
    private suspend fun palette(reader: RfbByteReader, stream: Int, w: Int, h: Int): ByteArray {
        val colours = reader.u8() + 1
        val table = reader.bytes(colours * 3)
        val rowBytes = if (colours <= 2) (w + 7) / 8 else w
        val indexes = data(reader, stream, rowBytes * h)
        val out = ByteArray(w * h * 4)
        for (row in 0 until h) for (col in 0 until w) {
            val index = if (colours <= 2) {
                (indexes[row * rowBytes + col / 8].toInt() shr (7 - col % 8)) and 1
            } else {
                indexes[row * rowBytes + col].toInt() and 0xff
            }
            if (index >= colours) throw RfbError("a Tight palette index outside the palette")
            val o = (row * w + col) * 4
            out[o] = table[index * 3 + 2]
            out[o + 1] = table[index * 3 + 1]
            out[o + 2] = table[index * 3]
            out[o + 3] = 0xff.toByte()
        }
        return out
    }

    /** The data of [expected] bytes: as it is when shorter than 12 bytes, else a compact length and zlib data on [stream]. */
    private suspend fun data(reader: RfbByteReader, stream: Int, expected: Int): ByteArray {
        if (expected == 0) return ByteArray(0)
        if (expected < MIN_TO_COMPRESS) return reader.bytes(expected)
        val len = compactLength(reader)
        checkLength(len, expected + expected / 8 + 64)
        return inflate(streams[stream], reader.bytes(len), expected)
    }

    /** Exactly [expected] bytes of the stream's output. The rest of the input is consumed too, so the next rectangle starts clean. */
    private fun inflate(inflater: Inflater, compressed: ByteArray, expected: Int): ByteArray {
        val out = ByteArray(expected)
        var got = 0
        try {
            inflater.setInput(compressed)
            while (got < expected) {
                val n = inflater.inflate(out, got, expected - got)
                if (n == 0 && (inflater.needsInput() || inflater.needsDictionary() || inflater.finished())) {
                    throw RfbError("the Tight data ended early")
                }
                got += n
            }
            val spill = ByteArray(16)
            while (!inflater.needsInput() && !inflater.finished()) {
                if (inflater.inflate(spill) > 0) throw RfbError("the Tight data is longer than its rectangle")
                if (inflater.needsDictionary()) throw RfbError("a Tight stream that needs a dictionary")
            }
        } catch (e: DataFormatException) {
            throw RfbError("the Tight data is not valid zlib")
        }
        return out
    }

    /** Predicted-gradient filter: each component is stored as the difference from the prediction, so the picture is rebuilt pixel by pixel. */
    private fun gradient(diffs: ByteArray, w: Int, h: Int): ByteArray {
        val out = ByteArray(w * h * 3)
        fun at(row: Int, col: Int, c: Int) = (row * w + col) * 3 + c
        for (row in 0 until h) for (col in 0 until w) for (c in 0 until 3) {
            val left = if (col > 0) out[at(row, col - 1, c)].toInt() and 0xff else 0
            val up = if (row > 0) out[at(row - 1, col, c)].toInt() and 0xff else 0
            val upLeft = if (row > 0 && col > 0) out[at(row - 1, col - 1, c)].toInt() and 0xff else 0
            val predicted = (left + up - upLeft).coerceIn(0, 255)
            out[at(row, col, c)] = ((diffs[at(row, col, c)].toInt() and 0xff) + predicted).toByte()
        }
        return out
    }

    /** RGB pixels ([count] of them) as BGRA. */
    private fun toBgra(rgb: ByteArray, count: Int): ByteArray {
        val out = ByteArray(count * 4)
        for (i in 0 until count) {
            out[i * 4] = rgb[i * 3 + 2]
            out[i * 4 + 1] = rgb[i * 3 + 1]
            out[i * 4 + 2] = rgb[i * 3]
            out[i * 4 + 3] = 0xff.toByte()
        }
        return out
    }

    /** The length in one, two or three bytes: 7 bits each, the top bit says another byte follows. */
    private suspend fun compactLength(reader: RfbByteReader): Int {
        val b0 = reader.u8()
        var len = b0 and 0x7f
        if (b0 and 0x80 != 0) {
            val b1 = reader.u8()
            len = len or ((b1 and 0x7f) shl 7)
            if (b1 and 0x80 != 0) len = len or (reader.u8() shl 14)
        }
        return len
    }

    private fun checkLength(len: Int, max: Int) {
        if (len < 0 || len > max) throw RfbError("a Tight length of $len bytes is not allowed")
    }

    companion object {
        const val FILL = 0x80
        const val JPEG = 0x90
        const val COPY_FILTER = 0
        const val PALETTE_FILTER = 1
        const val GRADIENT_FILTER = 2
        /** Data shorter than this is sent without zlib */
        const val MIN_TO_COMPRESS = 12
        /** A JPEG is at most a little more than its pixels */
        const val JPEG_SLACK = 64 * 1024
    }
}
