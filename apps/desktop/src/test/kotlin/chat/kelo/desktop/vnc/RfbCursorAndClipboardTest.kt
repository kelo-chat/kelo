package chat.kelo.desktop.vnc

import chat.kelo.app.agent.FramePoint
import java.io.ByteArrayOutputStream
import java.util.zip.Deflater
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class RfbCursorTest {
    @Test fun theMaskMakesThePixelsVisibleOrTransparent() {
        // 2 x 2: the top row has its first pixel visible, the bottom row none
        val pixels = ints(1, 2, 3, 0, 4, 5, 6, 0, 7, 8, 9, 0, 10, 11, 12, 0)   // BGRX per pixel
        val mask = ints(0x80, 0x00)
        val c = RfbCursor.image(2, 2, hotX = 1, hotY = 0, pixels = pixels, mask = mask)
        assertEquals(2, c.width)
        assertEquals(1, c.hotX)
        assertEquals(0xff030201.toInt(), c.argb[0])          // visible: alpha and the colour, red 3, green 2, blue 1
        assertEquals(0, c.argb[1] ushr 24)                   // masked out
        assertEquals(0, c.argb[3] ushr 24)
    }

    @Test fun theMaskRowsArePaddedToAByte() {
        assertEquals(2, RfbCursor.maskBytes(9, 1))
        assertEquals(4, RfbCursor.maskBytes(9, 2))
        assertEquals(16, RfbCursor.pixelBytes(2, 2))
    }

    private fun ints(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }
}

class RfbClipboardTest {
    private val sent = ArrayList<ByteArray>()
    private val offered = ArrayList<String>()

    private fun clipboard(holding: Boolean) = RfbClipboard(
        send = { sent += it },
        holding = { holding },
        offer = { offered += it },
    )

    private fun u32(v: Int) = byteArrayOf((v shr 24).toByte(), (v shr 16).toByte(), (v shr 8).toByte(), v.toByte())

    /** A provide message: the flags, then zlib data of the named formats (here: text only). */
    private fun provide(text: ByteArray, flags: Int = RfbClipboard.PROVIDE or RfbClipboard.TEXT): ByteArray {
        val plain = u32(text.size) + text
        val d = Deflater()
        d.setInput(plain)
        d.finish()
        val out = ByteArrayOutputStream()
        val buf = ByteArray(1024)
        while (!d.finished()) out.write(buf, 0, d.deflate(buf))
        d.end()
        return u32(flags) + out.toByteArray()
    }

    @Test fun capsAreAnsweredWithTextAndTheActions() {
        clipboard(holding = false).extended(u32(RfbClipboard.CAPS or RfbClipboard.TEXT) + u32(0))
        assertEquals(1, sent.size)
        assertContentEquals(RfbClientMessage.clipboardCaps(), sent[0])
    }

    @Test fun notifyAsksForTheTextOnlyWhileHolding() {
        val notify = u32(RfbClipboard.NOTIFY or RfbClipboard.TEXT)
        clipboard(holding = false).extended(notify)
        assertEquals(0, sent.size)
        clipboard(holding = true).extended(notify)
        assertContentEquals(RfbClientMessage.clipboardRequest(), sent.single())
    }

    @Test fun aProvidedTextIsOfferedWhileHolding() {
        clipboard(holding = true).extended(provide("안녕 copy".toByteArray(Charsets.UTF_8) + byteArrayOf(0)))
        assertEquals(listOf("안녕 copy"), offered)
    }

    @Test fun nothingIsOfferedWhileNotHolding() {
        clipboard(holding = false).extended(provide("secret".toByteArray() + byteArrayOf(0)))
        clipboard(holding = false).legacy("latin".toByteArray(Charsets.ISO_8859_1))
        assertEquals(emptyList(), offered)
    }

    @Test fun legacyTextIsLatin1AndOffered() {
        clipboard(holding = true).legacy(byteArrayOf(0x63, 0x61, 0x66, 0xe9.toByte()))   // "café"
        assertEquals(listOf("café"), offered)
    }

    @Test fun anOfferIsCutAtTenThousandCharacters() {
        clipboard(holding = true).legacy(ByteArray(10_001) { 'a'.code.toByte() })
        assertEquals(10_000, offered.single().length)
    }

    @Test fun aBlankTextIsNotOffered() {
        clipboard(holding = true).legacy("   ".toByteArray())
        assertEquals(emptyList(), offered)
    }
}
