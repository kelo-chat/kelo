package chat.kelo.app.call

import chat.kelo.app.chat.SpokenText

/**
 * Speakable pieces from a streaming reply (brief call-mode §4.3). Each piece is already through [SpokenText.from], so Markdown marks
 * are gone. Fenced code (``` blocks) is dropped: code is not read aloud.
 */
class SentenceCutter(private val firstMax: Int = 80, private val max: Int = 300) {
    private val pending = StringBuilder()   // text of the current piece (not yet cut)
    private val line = StringBuilder()      // the current line while its fence decision is not made yet
    private var decided = false             // the current line is known to be prose (or a fence)
    private var lineFence = false           // the current line opens or closes a fence
    private var inFence = false
    private var first = true                // no piece emitted yet in this reply

    /** The finished pieces from [delta]; text that may still be a sentence end waits for more text. */
    fun add(delta: String): List<String> {
        val out = ArrayList<String>()
        for (c in delta) {
            if (c == '\n') {
                endLine()
                flush(out, final = false, newline = true)
                continue
            }
            if (!decided) {
                line.append(c)
                if (line.trimStart().length >= 3) {
                    decided = true
                    lineFence = line.trimStart().startsWith("```")
                    if (!lineFence && !inFence) pending.append(line)
                    line.clear()
                }
            } else if (!lineFence && !inFence) {
                pending.append(c)
            }
        }
        cut(out, final = false)
        return out
    }

    /** The rest of the reply: called when the reply ends. */
    fun finish(): List<String> {
        val out = ArrayList<String>()
        endLine()
        flush(out, final = true, newline = false)
        inFence = false
        return out
    }

    fun reset() {
        pending.clear(); line.clear(); decided = false; lineFence = false; inFence = false; first = true
    }

    /** Ends the current line: a short line that never got a decision is prose unless a fence is open; a fence line toggles the fence. */
    private fun endLine() {
        if (!decided) {
            if (!inFence) pending.append(line)
        } else if (lineFence) {
            inFence = !inFence
        }
        line.clear(); decided = false; lineFence = false
    }

    private fun flush(out: MutableList<String>, final: Boolean, newline: Boolean) {
        cut(out, final)
        if (newline || final) emit(out, pending.toString())
        if (newline || final) pending.clear()
    }

    private fun cut(out: MutableList<String>, final: Boolean) {
        while (true) {
            val idx = sentenceEnd(final)
            if (idx >= 0) {
                emit(out, pending.substring(0, idx + 1)); pending.delete(0, idx + 1); continue
            }
            if (first && pending.length > firstMax) {
                val comma = lastComma(pending)
                if (comma >= 20) { emit(out, pending.substring(0, comma + 1)); pending.delete(0, comma + 1); continue }
            }
            if (pending.length > max) {
                val space = pending.lastIndexOf(" ", max)
                val at = if (space >= 1) space else max
                emit(out, pending.substring(0, at)); pending.delete(0, at); continue
            }
            return
        }
    }

    /** Index of a sentence end that may be cut here, or -1. Waits for the next character unless [final]. */
    private fun sentenceEnd(final: Boolean): Int {
        for (i in pending.indices) {
            val c = pending[i]
            if (c !in ENDS) continue
            if (c in CJK_ENDS) return i       // no space in Japanese or Chinese text
            if (i + 1 >= pending.length && !final) return -1
            if (i + 1 < pending.length && !pending[i + 1].isWhitespace()) continue
            if (c == '.' && singleLetterBefore(i)) continue   // "U.S." and "A." stay together
            return i
        }
        return -1
    }

    private fun singleLetterBefore(i: Int): Boolean {
        if (i < 1 || !pending[i - 1].isLetter()) return false
        return i < 2 || !pending[i - 2].isLetter()
    }

    private fun lastComma(s: CharSequence): Int {
        for (i in s.indices.reversed()) if (s[i] in COMMAS) return i
        return -1
    }

    private fun emit(out: MutableList<String>, raw: String) {
        val text = raw.trim()
        var piece = SpokenText.from(text)
        // SpokenText ends a paragraph with a period; a piece cut in the middle of a sentence must not get one (a pause there is wrong).
        if (piece.endsWith(".") && !text.endsWith(".")) piece = piece.dropLast(1)
        if (piece.isBlank()) return
        out.add(piece)
        first = false
    }

    private companion object {
        const val ENDS = ".!?…。！？"
        const val CJK_ENDS = "。！？"
        const val COMMAS = ",、，;:"
    }
}
