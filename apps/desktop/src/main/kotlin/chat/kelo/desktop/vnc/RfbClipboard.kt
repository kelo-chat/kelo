package chat.kelo.desktop.vnc

import chat.kelo.app.agent.ClipboardRules
import java.io.ByteArrayOutputStream
import java.util.zip.DataFormatException
import java.util.zip.Inflater

/**
 * The browser's clipboard as the owner sees it. The server sends ServerCutText; the text is offered only while the owner holds the tab
 * (as on the phones), capped by [ClipboardRules.offer], and never copied on its own. The client never sends text: it answers the
 * server's caps, and asks for the text when the server says it changed (only while holding).
 */
class RfbClipboard(
    private val send: (ByteArray) -> Unit,
    private val holding: () -> Boolean,
    private val offer: (String) -> Unit,
) {
    /** A legacy ServerCutText: ISO 8859-1 text. */
    fun legacy(bytes: ByteArray) {
        if (!holding()) return
        ClipboardRules.offer(String(bytes, Charsets.ISO_8859_1))?.let(offer)
    }

    /** An extended ServerCutText: the flags, then the data of the formats they name. [bytes] is what follows the negative length. */
    fun extended(bytes: ByteArray) {
        if (bytes.size < 4) return
        val flags = u32(bytes, 0)
        if (flags and CAPS != 0) send(RfbClientMessage.clipboardCaps())
        if (flags and NOTIFY != 0 && flags and TEXT != 0 && holding()) send(RfbClientMessage.clipboardRequest())
        if (flags and PROVIDE != 0 && holding()) provided(flags, bytes)
    }

    /** The data after the flags is zlib: per format the owner's text asks for, a u32 size and the bytes (the text is UTF-8, without its trailing NUL). */
    private fun provided(flags: Int, bytes: ByteArray) {
        val data = inflate(bytes.copyOfRange(4, bytes.size)) ?: return
        var at = 0
        for (bit in 0 until 16) {
            if (flags and (1 shl bit) == 0) continue
            if (at + 4 > data.size) return
            val size = u32(data, at)
            at += 4
            if (size < 0 || at + size > data.size) return
            if (bit == 0) {
                val text = String(data, at, size, Charsets.UTF_8).removeSuffix("\u0000")
                ClipboardRules.offer(text)?.let(offer)
                return
            }
            at += size
        }
    }

    private fun inflate(zlib: ByteArray): ByteArray? {
        val inflater = Inflater()
        try {
            inflater.setInput(zlib)
            val out = ByteArrayOutputStream()
            val buf = ByteArray(8192)
            while (!inflater.finished()) {
                val n = inflater.inflate(buf)
                if (n == 0 && (inflater.needsInput() || inflater.needsDictionary())) return null
                out.write(buf, 0, n)
                if (out.size() > MAX_INFLATED) return null
            }
            return out.toByteArray()
        } catch (e: DataFormatException) {
            return null
        } finally {
            inflater.end()
        }
    }

    private fun u32(b: ByteArray, at: Int): Int =
        ((b[at].toInt() and 0xff) shl 24) or ((b[at + 1].toInt() and 0xff) shl 16) or ((b[at + 2].toInt() and 0xff) shl 8) or (b[at + 3].toInt() and 0xff)

    companion object {
        const val TEXT = 1 shl 0
        const val CAPS = 1 shl 24
        const val NOTIFY = 1 shl 27
        const val PROVIDE = 1 shl 28
        /** Most text the browser may send in one message, before it is inflated */
        const val MAX_INFLATED = 1024 * 1024
    }
}
