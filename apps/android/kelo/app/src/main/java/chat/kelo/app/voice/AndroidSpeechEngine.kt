package chat.kelo.app.voice

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.chat.SpokenText
import java.util.Locale

/**
 * The phone's own text-to-speech (android.speech.tts.TextToSpeech) for "Read aloud". It binds to the speech service only when a
 * text is first read. Long texts go in pieces ([SpokenText.chunks]); [onDone] runs on the main thread after the last piece, or when
 * the engine fails; [stop] and a new [speak] cut the old text off without calling its [onDone].
 */
class AndroidSpeechEngine(private val context: Context) : SpeechEngine {
    private val main = Handler(Looper.getMainLooper())
    private var tts: TextToSpeech? = null
    private var ready = false
    private var waiting: (() -> Unit)? = null
    private var turn = 0

    private fun withEngine(job: (TextToSpeech) -> Unit, failed: () -> Unit) {
        val existing = tts
        if (existing != null && ready) { job(existing); return }
        waiting = { tts?.takeIf { ready }?.let(job) ?: failed() }
        if (existing != null) return   // still starting: the waiting job runs when it is ready
        var created: TextToSpeech? = null
        created = TextToSpeech(context.applicationContext) { status ->
            main.post {
                if (status == TextToSpeech.SUCCESS && tts === created) {
                    ready = true
                    val job2 = waiting; waiting = null; job2?.invoke()
                } else {
                    runCatching { created?.shutdown() }
                    if (tts === created) tts = null
                    ready = false
                    val job2 = waiting; waiting = null; job2?.invoke()
                }
            }
        }
        tts = created
    }

    override fun speak(text: String, language: String, onDone: () -> Unit) {
        val finished = onDone
        val mine = ++turn
        val pieces = SpokenText.chunks(text)
        if (pieces.isEmpty()) { finished(); return }
        withEngine(
            job = { engine ->
                if (mine != turn) return@withEngine
                val wanted = Locale.forLanguageTag(language)
                val set = runCatching { engine.setLanguage(wanted) }.getOrDefault(TextToSpeech.LANG_NOT_SUPPORTED)
                // no voice for that language on this phone: the phone's own voice reads it rather than nothing
                if (set == TextToSpeech.LANG_MISSING_DATA || set == TextToSpeech.LANG_NOT_SUPPORTED) runCatching { engine.setLanguage(Locale.getDefault()) }
                val last = "kelo-$mine-${pieces.lastIndex}"
                engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {}
                    override fun onDone(utteranceId: String?) { if (utteranceId == last) main.post { if (mine == turn) finished() } }
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String?) { main.post { if (mine == turn) finished() } }
                    override fun onError(utteranceId: String?, errorCode: Int) { main.post { if (mine == turn) finished() } }
                })
                pieces.forEachIndexed { i, piece ->
                    val result = engine.speak(piece, if (i == 0) TextToSpeech.QUEUE_FLUSH else TextToSpeech.QUEUE_ADD, null, "kelo-$mine-$i")
                    if (result != TextToSpeech.SUCCESS && i == 0) { main.post { if (mine == turn) finished() }; return@withEngine }
                }
            },
            failed = { if (mine == turn) finished() },
        )
    }

    override fun stop() {
        turn++
        waiting = null
        runCatching { tts?.stop() }
    }

    /** Lets go of the speech service (the app root is leaving). */
    fun shutdown() {
        turn++
        waiting = null
        runCatching { tts?.stop() }
        runCatching { tts?.shutdown() }
        tts = null
        ready = false
    }
}
