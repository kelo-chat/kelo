package chat.kelo.app.chat

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf

/**
 * What a reply sounds like when it is read aloud: plain sentences, without the Markdown marks, links, code, emoji or table bars
 * (a pure function so it can be tested), the language it is in, and its split into pieces a speech engine accepts.
 */
object SpokenText {
    /** [markdown] as sentences for a voice: no marks, link addresses, code blocks or emoji; list items and table rows end like sentences. */
    fun from(markdown: String): String = Markdown.spoken(markdown)

    /**
     * The BCP-47 language of [text], from its writing system where that tells (Hangul, kana, Han, Cyrillic, Arabic, Thai, Devanagari,
     * Greek, Hebrew); a text in the Latin alphabet gets [fallback] (the app's language), as the script cannot tell English from French.
     */
    fun language(text: String, fallback: String): String {
        var hangul = 0; var kana = 0; var han = 0; var cyrillic = 0; var arabic = 0; var thai = 0; var devanagari = 0; var latin = 0
        for (ch in text) {
            when (ch.code) {
                in 0xAC00..0xD7A3, in 0x1100..0x11FF, in 0x3130..0x318F -> hangul++
                in 0x3040..0x30FF -> kana++
                in 0x4E00..0x9FFF, in 0x3400..0x4DBF -> han++
                in 0x0400..0x04FF -> cyrillic++
                in 0x0600..0x06FF -> arabic++
                in 0x0E00..0x0E7F -> thai++
                in 0x0900..0x097F -> devanagari++
                in 0x41..0x5A, in 0x61..0x7A, in 0xC0..0x24F -> latin++
            }
        }
        val counts = listOf(
            "ko" to hangul, "ja" to kana, "zh" to han, "ru" to cyrillic, "ar" to arabic, "th" to thai, "hi" to devanagari,
        )
        val best = counts.maxByOrNull { it.second }!!
        if (best.second == 0) return fallback
        // Japanese is written with Han characters too: any kana makes it Japanese; Han beside Hangul is Korean
        if (kana > 0) return "ja"
        if (best.first == "zh" && hangul > 0) return "ko"
        // a word or two in another script inside a Latin text does not change its language
        if (best.second < latin / 2) return fallback
        // Cyrillic: the app's language when it is Ukrainian (the script alone cannot tell)
        if (best.first == "ru" && fallback.substringBefore('-').equals("uk", ignoreCase = true)) return fallback
        if (best.first == "zh" && fallback.substringBefore('-').equals("zh", ignoreCase = true)) return fallback
        return best.first
    }

    /** [text] in pieces of at most [max] characters, cut at a sentence end, else a line end or a space (a speech engine takes only so much at once). */
    fun chunks(text: String, max: Int = 3500): List<String> {
        val t = text.trim()
        if (t.isEmpty()) return emptyList()
        val out = mutableListOf<String>()
        var rest = t
        while (rest.length > max) {
            val window = rest.substring(0, max)
            var cut = window.lastIndexOfAny(charArrayOf('.', '!', '?', '。', '！', '？', '\n'))
            if (cut < max / 2) cut = window.lastIndexOf(' ')
            if (cut < max / 2) cut = max - 1
            out += rest.substring(0, cut + 1).trim()
            rest = rest.substring(cut + 1).trim()
        }
        if (rest.isNotEmpty()) out += rest
        return out.filter { it.isNotEmpty() }
    }
}

/** The language the app is shown in (what the server is told too), for a text whose own language cannot be told. */
fun appLanguageTag(): String =
    chat.kelo.app.core.AcceptLanguage.current.substringBefore(',').substringBefore(';').trim()
        .ifEmpty { java.util.Locale.getDefault().toLanguageTag() }

/** The platform's own text-to-speech. [speak] reads [text] in [language] and calls [onDone] when it ends or fails (not after [stop]). */
interface SpeechEngine {
    fun speak(text: String, language: String, onDone: () -> Unit)
    fun stop()
}

/**
 * "Read aloud" for chat messages: one message speaks at a time; reading a message again stops it; reading another one switches to it.
 * [reading] is the text handed to [toggle] while it is spoken (null otherwise), which the menu compares to show "Stop reading".
 */
class ReadAloud(private val engine: SpeechEngine, private val fallbackLanguage: () -> String) {
    var reading: String? by mutableStateOf(null)
        private set
    private var turn = 0

    fun toggle(text: String) {
        if (reading == text) { stop(); return }
        engine.stop()
        val plain = SpokenText.from(text)
        if (plain.isBlank()) { turn++; reading = null; return }
        val mine = ++turn
        reading = text
        engine.speak(plain, SpokenText.language(plain, fallbackLanguage())) { if (mine == turn) reading = null }
    }

    fun stop() {
        turn++
        reading = null
        engine.stop()
    }
}

/** The platform's speech engine; null (no system text-to-speech) means "Read aloud" is not offered. */
val LocalSpeechEngine = staticCompositionLocalOf<SpeechEngine?> { null }

/** The reader of the chat on screen (null without an engine). */
val LocalReadAloud = staticCompositionLocalOf<ReadAloud?> { null }
