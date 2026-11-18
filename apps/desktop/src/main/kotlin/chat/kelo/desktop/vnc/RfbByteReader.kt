package chat.kelo.desktop.vnc

import kotlinx.coroutines.channels.Channel
import java.io.EOFException

/**
 * The server's bytes, read in order. The relay thread [feed]s each chunk as it arrives and the session reads with suspend calls, so a
 * message may arrive split at any byte: the RFB stream has no framing of its own.
 */
class RfbByteReader {
    private val chunks = Channel<ByteArray>(Channel.UNLIMITED)
    private var cur = ByteArray(0)
    private var pos = 0

    /** Bytes from the socket, in order. Ignored once [end] was called. */
    fun feed(bytes: ByteArray) {
        chunks.trySend(bytes)
    }

    /** The socket ended: the next read fails with [EOFException]. */
    fun end() {
        chunks.close()
    }

    private suspend fun fill() {
        while (pos >= cur.size) {
            cur = chunks.receiveCatching().getOrNull() ?: throw EOFException("the screen closed")
            pos = 0
        }
    }

    suspend fun u8(): Int {
        fill()
        return cur[pos++].toInt() and 0xff
    }

    suspend fun u16(): Int {
        val hi = u8()
        return (hi shl 8) or u8()
    }

    /** A big-endian 32-bit value (signed, as RFB's S32 and U32 are read the same way here). */
    suspend fun s32(): Int {
        val b0 = u8()
        val b1 = u8()
        val b2 = u8()
        return (b0 shl 24) or (b1 shl 16) or (b2 shl 8) or u8()
    }

    suspend fun bytes(n: Int): ByteArray {
        require(n >= 0) { "negative length" }
        val out = ByteArray(n)
        var done = 0
        while (done < n) {
            fill()
            val k = minOf(n - done, cur.size - pos)
            System.arraycopy(cur, pos, out, done, k)
            pos += k
            done += k
        }
        return out
    }

    /** Reads and drops [n] bytes. */
    suspend fun skip(n: Int) {
        require(n >= 0) { "negative length" }
        var left = n
        while (left > 0) {
            fill()
            val k = minOf(left, cur.size - pos)
            pos += k
            left -= k
        }
    }
}
