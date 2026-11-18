package chat.kelo.desktop.vnc

import androidx.compose.ui.input.key.Key

/**
 * The X keysyms the page understands, for the keys the owner presses in the screen. Printable keys send their code point (Latin-1
 * as it is, anything else as 0x01000000 + the code point, which the server adds to the page's keymap). With Ctrl held (⌘ on macOS)
 * the key sends its base letter or digit, so Ctrl+C reaches the page as copy. On macOS ⌘ is sent as Control_L: the page runs in
 * Linux Chrome, where copy, paste and select-all are Ctrl. Windows/Super and the macOS Option key are not sent.
 */
object Keysyms {
    private val named: Map<Long, Int> = mapOf(
        Key.Enter.keyCode to 0xff0d,
        Key.Backspace.keyCode to 0xff08,
        Key.Tab.keyCode to 0xff09,
        Key.Escape.keyCode to 0xff1b,
        Key.Delete.keyCode to 0xffff,
        Key.Insert.keyCode to 0xff63,
        Key.MoveHome.keyCode to 0xff50,
        Key.DirectionLeft.keyCode to 0xff51,
        Key.DirectionUp.keyCode to 0xff52,
        Key.DirectionRight.keyCode to 0xff53,
        Key.DirectionDown.keyCode to 0xff54,
        Key.PageUp.keyCode to 0xff55,
        Key.PageDown.keyCode to 0xff56,
        Key.MoveEnd.keyCode to 0xff57,
        Key.F1.keyCode to 0xffbe, Key.F2.keyCode to 0xffbf, Key.F3.keyCode to 0xffc0, Key.F4.keyCode to 0xffc1,
        Key.F5.keyCode to 0xffc2, Key.F6.keyCode to 0xffc3, Key.F7.keyCode to 0xffc4, Key.F8.keyCode to 0xffc5,
        Key.F9.keyCode to 0xffc6, Key.F10.keyCode to 0xffc7, Key.F11.keyCode to 0xffc8, Key.F12.keyCode to 0xffc9,
        Key.ShiftLeft.keyCode to 0xffe1,
        Key.ShiftRight.keyCode to 0xffe2,
        Key.CtrlLeft.keyCode to 0xffe3,
        Key.CtrlRight.keyCode to 0xffe4,
        Key.AltLeft.keyCode to 0xffe9,
        Key.AltRight.keyCode to 0xffea,
        Key.CapsLock.keyCode to 0xffe5,
        Key.Spacebar.keyCode to 0x20,
    )

    private val letters: Map<Long, Int> = buildMap {
        val keys = listOf(
            Key.A, Key.B, Key.C, Key.D, Key.E, Key.F, Key.G, Key.H, Key.I, Key.J, Key.K, Key.L, Key.M,
            Key.N, Key.O, Key.P, Key.Q, Key.R, Key.S, Key.T, Key.U, Key.V, Key.W, Key.X, Key.Y, Key.Z,
        )
        keys.forEachIndexed { i, k -> put(k.keyCode, 'a'.code + i) }
        val digits = listOf(Key.Zero, Key.One, Key.Two, Key.Three, Key.Four, Key.Five, Key.Six, Key.Seven, Key.Eight, Key.Nine)
        digits.forEachIndexed { i, k -> put(k.keyCode, '0'.code + i) }
    }

    /** The left Meta key (⌘ on macOS) and the right one; they are sent as Control on macOS and not at all elsewhere. */
    private val metaLeft = Key.MetaLeft.keyCode
    private val metaRight = Key.MetaRight.keyCode

    /** A key that is a modifier: Shift, Ctrl, Alt, ⌘ (macOS) — the keysym to send for it, or null. */
    fun isMeta(keyCode: Long): Boolean = keyCode == metaLeft || keyCode == metaRight

    /**
     * The keysym for a key press: [keyCode] is the physical key, [codePoint] what it types (0 for none), [ctrl] whether Ctrl (or ⌘ on
     * macOS, where [macOs] is true) is held. Null for a key with no meaning to the page.
     */
    fun of(keyCode: Long, codePoint: Int, ctrl: Boolean, macOs: Boolean): Int? {
        if (isMeta(keyCode)) return if (macOs) 0xffe3 else null
        named[keyCode]?.let { return it }
        if (ctrl) letters[keyCode]?.let { return it }
        if (codePoint < 0x20 || codePoint == 0x7f) return null
        return printable(codePoint)
    }

    /** A printable code point as a keysym. */
    fun printable(codePoint: Int): Int = if (codePoint in 0x20..0x7e || codePoint in 0xa0..0xff) codePoint else 0x01000000 + codePoint
}

/**
 * The keys down on the page, by the owner's physical key. A key-up sends the keysym its key-down sent, so a modifier that changed in
 * between leaves no key stuck. [releaseAll] lifts every key still down (holding ends, the window loses focus).
 */
class RfbKeys(private val send: (ByteArray) -> Unit, private val macOs: Boolean) {
    private val down = LinkedHashMap<Long, Int>()

    /** The owner pressed [keyCode]: [codePoint] is what it types, [ctrl] whether Ctrl (⌘ on macOS) is held (see [Keysyms.of]). */
    fun press(keyCode: Long, codePoint: Int, ctrl: Boolean) {
        val keysym = Keysyms.of(keyCode, codePoint, ctrl, macOs) ?: return
        if (keyCode in down) return                    // a key held down repeats on the page with its own repeat: not sent twice
        down[keyCode] = keysym
        send(RfbClientMessage.key(true, keysym))
    }

    /** The owner let [keyCode] go: the keysym of its press goes up. */
    fun release(keyCode: Long) {
        val keysym = down.remove(keyCode) ?: return
        send(RfbClientMessage.key(false, keysym))
    }

    /** Every key still down goes up. */
    fun releaseAll() {
        val keys = down.values.toList()
        down.clear()
        for (keysym in keys) send(RfbClientMessage.key(false, keysym))
    }
}
