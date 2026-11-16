package chat.kelo.app.voice

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import java.util.Locale

/**
 * Putting spoken words into a text at the cursor. When listening starts, the text is split at the cursor (a selection is replaced);
 * every partial result then rebuilds "before + words + after", with a space where the words would otherwise touch a word.
 */
object SpeechMerge {
    data class Anchor(val before: String, val after: String)

    data class Merged(val text: String, val cursor: Int)

    fun anchor(text: String, selStart: Int, selEnd: Int): Anchor {
        val a = selStart.coerceIn(0, text.length)
        val b = selEnd.coerceIn(0, text.length)
        return Anchor(text.substring(0, minOf(a, b)), text.substring(maxOf(a, b)))
    }

    fun merge(anchor: Anchor, spoken: String): Merged {
        val words = spoken.trim().replace(Regex("\\s+"), " ")
        if (words.isEmpty()) return Merged(anchor.before + anchor.after, anchor.before.length)
        val lead = if (anchor.before.isNotEmpty() && !anchor.before.last().isWhitespace()) " " else ""
        val trail = if (anchor.after.isNotEmpty() && !anchor.after.first().isWhitespace() && anchor.after.first() !in NO_SPACE_BEFORE) " " else ""
        val head = anchor.before + lead + words
        return Merged(head + trail + anchor.after, head.length)
    }

    private val NO_SPACE_BEFORE = setOf('.', ',', '!', '?', ':', ';', ')', ']', '}', '。', '、', '？', '！')

    /** The recognizer's sound level (about -2 … 10 dB) as 0 … 1 for the ring around the mic. */
    fun level(rmsDb: Float): Float = ((rmsDb + 2f) / 12f).coerceIn(0f, 1f)
}

/** Why listening ended without words, for a short line under the composer. */
enum class SpeechProblem { NO_MATCH, NETWORK, BUSY, OTHER }

/** Which recognizer to use, best first (pure, so it can be tested). */
object RecognizerChoice {
    enum class Way { ON_DEVICE, GOOGLE_SERVICE, DEFAULT }

    /** Google's recognition services, the better first (the Google app, then Speech Services by Google). */
    val GOOGLE_PACKAGES = listOf("com.google.android.googlequicksearchbox", "com.google.android.tts")

    /**
     * The ways to try, in order. [sdk]: Build.VERSION.SDK_INT; [onDevice]: SpeechRecognizer.isOnDeviceRecognitionAvailable;
     * [googleService]: a Google recognition service is installed; [onDeviceFailed]: the on-device model already said no this session.
     */
    fun order(sdk: Int, onDevice: Boolean, googleService: Boolean, onDeviceFailed: Boolean): List<Way> = buildList {
        if (sdk >= 31 && onDevice && !onDeviceFailed) add(Way.ON_DEVICE)
        if (googleService) add(Way.GOOGLE_SERVICE)
        add(Way.DEFAULT)
    }

    /** The recognizer errors that mean "this language is not there" (Android 12+: 12 not supported, 13 unavailable). */
    fun languageMissing(error: Int): Boolean = error == 12 || error == 13
}

/**
 * The phone's own speech recognition (android.speech.SpeechRecognizer) in the phone's language, with partial results. Make it with
 * [rememberSpeechInput]; [start] needs the RECORD_AUDIO permission. It ends by itself after a pause in speaking, or with [stop].
 */
@Stable
class SpeechInput internal constructor(private val context: Context) {
    var listening by mutableStateOf(false)
        private set
    /** 0 … 1, how loud it hears right now */
    var level by mutableFloatStateOf(0f)
        private set
    val available: Boolean = runCatching {
        SpeechRecognizer.isRecognitionAvailable(context) ||
            (android.os.Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context))
    }.getOrDefault(false)

    private var recognizer: SpeechRecognizer? = null
    internal var onWords: (String, Boolean) -> Unit = { _, _ -> }
    internal var onEnd: (SpeechProblem?) -> Unit = {}
    private var heard = false
    private var way: RecognizerChoice.Way? = null
    private var onDeviceFailed = false
    private var switchedThisTime = false
    private val googleService: android.content.ComponentName? = runCatching {
        val services = context.packageManager.queryIntentServices(
            Intent(android.speech.RecognitionService.SERVICE_INTERFACE), 0)
        RecognizerChoice.GOOGLE_PACKAGES.firstNotNullOfOrNull { pkg ->
            services.firstOrNull { it.serviceInfo.packageName == pkg }?.serviceInfo?.let { android.content.ComponentName(it.packageName, it.name) }
        }
    }.getOrNull()

    /** A recognizer of the first way that still applies; remembers which way it is. */
    private fun makeRecognizer(): SpeechRecognizer {
        val onDevice = android.os.Build.VERSION.SDK_INT >= 31 && runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false)
        val ways = RecognizerChoice.order(android.os.Build.VERSION.SDK_INT, onDevice, googleService != null, onDeviceFailed)
        for (w in ways) {
            val r = runCatching {
                when (w) {
                    RecognizerChoice.Way.ON_DEVICE -> SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
                    RecognizerChoice.Way.GOOGLE_SERVICE -> SpeechRecognizer.createSpeechRecognizer(context, googleService)
                    RecognizerChoice.Way.DEFAULT -> SpeechRecognizer.createSpeechRecognizer(context)
                }
            }.getOrNull()
            if (r != null) { way = w; return r }
        }
        way = RecognizerChoice.Way.DEFAULT
        return SpeechRecognizer.createSpeechRecognizer(context)
    }

    /** Android 13+: the phone downloads the on-device model for this language in the background, for next time. */
    private fun askForLanguage(intent: Intent) {
        if (android.os.Build.VERSION.SDK_INT < 33) return
        runCatching { recognizer?.triggerModelDownload(intent) }
    }

    fun start() {
        if (listening || !available) return
        val r = recognizer ?: makeRecognizer().also { recognizer = it }
        heard = false
        switchedThisTime = false
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        r.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) { level = SpeechMerge.level(rmsdB) }
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() { level = 0f }
            override fun onError(error: Int) {
                // the on-device model lacks this language: ask the phone to fetch it, then go on with the next way, once, quietly
                if (way == RecognizerChoice.Way.ON_DEVICE && RecognizerChoice.languageMissing(error) && !switchedThisTime && !heard) {
                    askForLanguage(intent)
                    onDeviceFailed = true
                    switchedThisTime = true
                    runCatching { recognizer?.destroy() }
                    recognizer = null
                    listening = false
                    start()
                    return
                }
                // after words were heard, "no match" at the end is just the end
                val problem = when (error) {
                    SpeechRecognizer.ERROR_NO_MATCH, SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> if (heard) null else SpeechProblem.NO_MATCH
                    SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT, SpeechRecognizer.ERROR_SERVER -> SpeechProblem.NETWORK
                    SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> SpeechProblem.BUSY
                    SpeechRecognizer.ERROR_CLIENT -> null   // our own stop / cancel
                    else -> SpeechProblem.OTHER
                }
                finish(problem)
            }
            override fun onResults(results: Bundle?) {
                words(results)?.let { heard = true; onWords(it, true) }
                finish(null)
            }
            override fun onPartialResults(partialResults: Bundle?) {
                words(partialResults)?.takeIf { it.isNotBlank() }?.let { heard = true; onWords(it, false) }
            }
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        listening = true
        try {
            r.startListening(intent)
        } catch (e: Exception) {
            finish(SpeechProblem.OTHER)
        }
    }

    /** Ends listening; what was heard so far still arrives as the final result. */
    fun stop() {
        if (!listening) return
        runCatching { recognizer?.stopListening() }
    }

    private fun finish(problem: SpeechProblem?) {
        if (!listening) return
        listening = false
        level = 0f
        onEnd(problem)
    }

    internal fun release() {
        if (listening) runCatching { recognizer?.cancel() }
        listening = false
        runCatching { recognizer?.destroy() }
        recognizer = null
        way = null
    }

    private fun words(b: Bundle?): String? = b?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()
}

/** [onWords] gets the words heard so far (final = the recognizer's last word on them); [onEnd] when listening stops. */
@Composable
fun rememberSpeechInput(onWords: (String, Boolean) -> Unit, onEnd: (SpeechProblem?) -> Unit): SpeechInput {
    val context = LocalContext.current
    val words by rememberUpdatedState(onWords)
    val end by rememberUpdatedState(onEnd)
    val input = remember(context) { SpeechInput(context.applicationContext) }
    input.onWords = { w, f -> words(w, f) }
    input.onEnd = { p -> end(p) }
    DisposableEffect(input) { onDispose { input.release() } }
    return input
}
