package chat.kelo.app.call

import chat.kelo.app.chat.SpokenText

/**
 * Speakable pieces from a streaming reply (brief call-mode §4.3). Each piece is already through [SpokenText.from], so Markdown marks
 * are gone. Fenced code (``` blocks) is dropped: code is not read aloud.
 *
 * The first piece is a short phrase so Kelo starts speaking early (owner 2026-11-20); later pieces are sentences, long ones cut at a
 * clause break.
 */
class SentenceCutter(private val max: Int = 300) {
    private val pending = StringBuilder()   // text of the current piece (not yet cut)
    private val line = StringBuilder()      // the current line while its fence decision is not made yet
    private var decided = false             // the current line is known to be prose (or a fence)
    private var lineFence = false           // the current line opens or closes a fence
    private var inFence = false
    private var spoken = 0                  // characters given out in this reply
    private var pieces = 0                  // pieces given out in this reply

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
        pending.clear(); line.clear(); decided = false; lineFence = false; inFence = false; spoken = 0; pieces = 0
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
            if (spoken < FIRST_MIN_CHARS) {                       // the opening phrase: out as soon as it is a phrase
                val b = openingBreak(final)
                if (b >= 0) { take(out, b + 1); continue }
                if (pending.length > FIRST_WORD_CUT) {
                    val at = wordCut()
                    if (at > 0) { take(out, at); continue }
                }
            }
            val end = sentenceEnd(final)
            val soft = if (pieces <= 1) SECOND_SOFT else LATER_SOFT
            val len = if (end >= 0) end + 1 else pending.length
            if (len > soft) {
                val b = clauseBreak(soft, if (end >= 0) end else pending.length, final)
                if (b >= 0) { take(out, b + 1); continue }
            }
            if (end >= 0) { take(out, end + 1); continue }
            if (pending.length > max) {
                val space = pending.lastIndexOf(" ", max)
                take(out, if (space >= 1) space else max); continue
            }
            return
        }
    }

    private fun take(out: MutableList<String>, n: Int) { emit(out, pending.substring(0, n)); pending.delete(0, n) }

    /** Whether the text may be cut right after the mark at [i]: a CJK mark always; else the next character is known and is a space
     *  ("1,000", "10:30", "3.5" are not cut), and a period after a single letter ("U.S.") is not an end. */
    private fun breakHere(i: Int, final: Boolean): Boolean {
        val c = pending[i]
        if (c in CJK_ENDS || c in CJK_COMMAS) return true
        if (i + 1 >= pending.length) return final
        if (!pending[i + 1].isWhitespace()) return false
        return !(c == '.' && singleLetterBefore(i))
    }

    /** Index of the first sentence end that may be cut, or -1. */
    private fun sentenceEnd(final: Boolean): Int {
        for (i in pending.indices) if (pending[i] in ENDS && breakHere(i, final)) return i
        return -1
    }

    /** The first place the opening phrase may end within its first [FIRST_WORD_CUT] characters: a sentence end, or a clause break once
     *  the phrase has [FIRST_MIN_CHARS] characters and, in text with spaces, [FIRST_MIN_WORDS] words. -1: none yet. */
    private fun openingBreak(final: Boolean): Int {
        for (i in 0 until minOf(pending.length, FIRST_WORD_CUT)) {
            val c = pending[i]
            if (c !in ENDS && c !in COMMAS) continue
            if (!breakHere(i, final)) continue
            if (c in ENDS) return i
            val phrase = pending.substring(0, i + 1).trim()
            if (phrase.length >= FIRST_MIN_CHARS && (' ' !in phrase || words(phrase) >= FIRST_MIN_WORDS)) return i
        }
        return -1
    }

    /** No break came in the first [FIRST_WORD_CUT] characters: the last space within them with [FIRST_CUT_WORDS] words before it and no
     *  open link, bracket, bold or code mark (never inside a word). 0: none (text without spaces waits for a mark). */
    private fun wordCut(): Int {
        for (i in minOf(FIRST_WORD_CUT, pending.length - 1) downTo 1) {
            if (!pending[i].isWhitespace()) continue
            val before = pending.substring(0, i)
            if (words(before) >= FIRST_CUT_WORDS && closed(before)) return i
        }
        return 0
    }

    /** Where a piece longer than [soft] is cut: the last clause break before [soft] that leaves [MIN_CLAUSE] characters, else the first
     *  one after it; only before [until] (the sentence end, or the end of the text so far). -1: none. */
    private fun clauseBreak(soft: Int, until: Int, final: Boolean): Int {
        var best = -1
        for (i in MIN_CLAUSE until minOf(until, pending.length)) {
            if (pending[i] !in COMMAS || !breakHere(i, final)) continue
            if (i < soft) best = i else return if (best >= 0) best else i
        }
        return best
    }

    private fun words(s: CharSequence): Int = s.split(' ', '\t').count { it.isNotEmpty() }

    /** No `[`, `(`, `**` or `` ` `` left open in [s] (a cut there would break Markdown before it is made plain). */
    private fun closed(s: String): Boolean =
        s.count { it == '[' } == s.count { it == ']' } && s.count { it == '(' } == s.count { it == ')' } &&
            s.split("**").size % 2 == 1 && s.count { it == '`' } % 2 == 0

    /** "U.S.", "A.": a single Latin letter before the period is an abbreviation. A Korean or other one-letter word ("네.") ends. */
    private fun singleLetterBefore(i: Int): Boolean {
        if (i < 1 || !pending[i - 1].isLetter() || pending[i - 1].code > 0x7F) return false
        return i < 2 || !pending[i - 2].isLetter()
    }

    private fun emit(out: MutableList<String>, raw: String) {
        val text = raw.trim()
        var piece = SpokenText.from(text)
        // SpokenText ends a paragraph with a period; a piece cut in the middle of a sentence must not get one (a pause there is wrong).
        if (piece.endsWith(".") && !text.endsWith(".")) piece = piece.dropLast(1)
        if (piece.isBlank()) return
        out.add(piece)
        spoken += piece.length
        pieces++
    }

    private companion object {
        const val ENDS = ".!?…。！？"
        const val CJK_ENDS = "。！？"
        const val COMMAS = ",、，;:"
        const val CJK_COMMAS = "、，"
        const val FIRST_MIN_CHARS = 8     // the opening phrase: at least this many characters…
        const val FIRST_MIN_WORDS = 2     // …and words (text with spaces)
        const val FIRST_WORD_CUT = 32     // no break by then: cut at a word
        const val FIRST_CUT_WORDS = 3     // with at least this many words before it
        const val SECOND_SOFT = 50        // the piece after the opening is cut at a clause break past this
        const val LATER_SOFT = 120        // later sentences are cut at a clause break past this
        const val MIN_CLAUSE = 20         // a clause cut keeps at least this many characters before it
    }
}
