package chat.kelo.desktop.vnc

import androidx.compose.ui.input.key.Key
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertNull

class KeysymsTest {
    private val sent = ArrayList<ByteArray>()

    /** The (down, keysym) pairs sent, in order. */
    private fun messages(): List<Pair<Boolean, Int>> = sent.map { (it[1].toInt() != 0) to (((it[4].toInt() and 0xff) shl 24) or ((it[5].toInt() and 0xff) shl 16) or ((it[6].toInt() and 0xff) shl 8) or (it[7].toInt() and 0xff)) }

    @Test fun namedKeys() {
        assertEquals(0xff0d, Keysyms.of(Key.Enter.keyCode, 0, ctrl = false, macOs = false))
        assertEquals(0xff08, Keysyms.of(Key.Backspace.keyCode, 0, ctrl = false, macOs = false))
        assertEquals(0xff09, Keysyms.of(Key.Tab.keyCode, 0, ctrl = false, macOs = false))
        assertEquals(0xffbe, Keysyms.of(Key.F1.keyCode, 0, ctrl = false, macOs = false))
        assertEquals(0x20, Keysyms.of(Key.Spacebar.keyCode, 0, ctrl = false, macOs = false))
    }

    @Test fun printableKeysSendTheirCodePoint() {
        assertEquals(0x61, Keysyms.of(Key.A.keyCode, 'a'.code, ctrl = false, macOs = false))
        assertEquals(0xe9, Keysyms.of(0L, 0xe9, ctrl = false, macOs = false))
        assertEquals(0x0100d55c, Keysyms.of(0L, 0xd55c, ctrl = false, macOs = false))   // 한
    }

    @Test fun aControlCharacterNeverSendsItselfAsText() {
        assertNull(Keysyms.of(0L, 0x03, ctrl = false, macOs = false))
    }

    @Test fun commandCOnMacIsControlAndTheBaseLetter() {
        val keys = RfbKeys({ sent += it }, macOs = true)
        keys.press(Key.MetaLeft.keyCode, 0, ctrl = false)
        keys.press(Key.C.keyCode, 3, ctrl = true)          // the key types a control character; the base letter is sent
        keys.release(Key.C.keyCode)
        keys.release(Key.MetaLeft.keyCode)
        assertEquals(
            listOf(true to 0xffe3, true to 0x63, false to 0x63, false to 0xffe3),
            messages(),
        )
    }

    @Test fun ctrlCOnWindowsIsControl() {
        val keys = RfbKeys({ sent += it }, macOs = false)
        keys.press(Key.CtrlLeft.keyCode, 0, ctrl = false)
        keys.press(Key.C.keyCode, 3, ctrl = true)
        keys.release(Key.C.keyCode)
        keys.release(Key.CtrlLeft.keyCode)
        assertEquals(listOf(true to 0xffe3, true to 0x63, false to 0x63, false to 0xffe3), messages())
    }

    @Test fun theWindowsKeyIsNotSent() {
        val keys = RfbKeys({ sent += it }, macOs = false)
        keys.press(Key.MetaLeft.keyCode, 0, ctrl = false)
        assertEquals(emptyList(), messages())
    }

    @Test fun aKeyUpSendsTheKeysymOfItsKeyDown() {
        val keys = RfbKeys({ sent += it }, macOs = false)
        keys.press(Key.ShiftLeft.keyCode, 0, ctrl = false)
        keys.press(Key.A.keyCode, 'A'.code, ctrl = false)
        keys.release(Key.ShiftLeft.keyCode)               // the shift is let go before the letter
        keys.release(Key.A.keyCode)
        assertEquals(listOf(true to 0xffe1, true to 0x41, false to 0xffe1, false to 0x41), messages())
    }

    @Test fun releaseAllLiftsEveryKeyOnce() {
        val keys = RfbKeys({ sent += it }, macOs = false)
        keys.press(Key.ShiftLeft.keyCode, 0, ctrl = false)
        keys.press(Key.Enter.keyCode, 0, ctrl = false)
        sent.clear()
        keys.releaseAll()
        assertEquals(listOf(false to 0xffe1, false to 0xff0d), messages())
        keys.releaseAll()
        assertEquals(2, sent.size)
    }

    @Test fun aHeldKeyIsNotSentTwice() {
        val keys = RfbKeys({ sent += it }, macOs = false)
        keys.press(Key.A.keyCode, 'a'.code, ctrl = false)
        keys.press(Key.A.keyCode, 'a'.code, ctrl = false)
        assertEquals(1, sent.size)
        assertContentEquals(RfbClientMessage.key(true, 0x61), sent[0])
    }
}
