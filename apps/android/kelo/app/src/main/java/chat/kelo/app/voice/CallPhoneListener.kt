package chat.kelo.app.voice

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import chat.kelo.app.call.platform.PhoneListener
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Android implementation of [PhoneListener] using [SpeechRecognizer] (brief §4.10, §6.1).
 * Used as fallback ears on phones when server STT is downloading or not ready.
 */
class CallPhoneListener(private val context: Context) : PhoneListener {
    private val mainHandler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private val cancelled = AtomicBoolean(false)

    private val googleService: ComponentName? by lazy {
        runCatching {
            val services = context.packageManager.queryIntentServices(
                Intent(android.speech.RecognitionService.SERVICE_INTERFACE), 0
            )
            RecognizerChoice.GOOGLE_PACKAGES.firstNotNullOfOrNull { pkg ->
                services.firstOrNull { it.serviceInfo.packageName == pkg }?.serviceInfo?.let {
                    ComponentName(it.packageName, it.name)
                }
            }
        }.getOrNull()
    }

    override fun listenOnce(
        language: String,
        onStarted: () -> Unit,
        onLevel: (Float) -> Unit,
        onDone: (String?) -> Unit
    ) {
        mainHandler.post {
            cancelRecognizer()
            cancelled.set(false)

            val r = makeRecognizer()
            recognizer = r

            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
                putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)
                putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
                putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            }

            var startedNotified = false

            r.setRecognitionListener(object : RecognitionListener {
                override fun onReadyForSpeech(params: Bundle?) {}

                override fun onBeginningOfSpeech() {
                    if (!startedNotified) {
                        startedNotified = true
                        onStarted()
                    }
                }

                override fun onRmsChanged(rmsdB: Float) {
                    onLevel(SpeechMerge.level(rmsdB))
                }

                override fun onBufferReceived(buffer: ByteArray?) {}

                override fun onEndOfSpeech() {
                    onLevel(0f)
                }

                override fun onError(error: Int) {
                    onLevel(0f)
                    if (cancelled.get()) return
                    if (error == SpeechRecognizer.ERROR_CLIENT) return

                    if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                        onDone("")
                    } else {
                        onDone(null)
                    }
                }

                override fun onResults(results: Bundle?) {
                    onLevel(0f)
                    if (cancelled.get()) return
                    val words = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.trim() ?: ""
                    onDone(words)
                }

                override fun onPartialResults(partialResults: Bundle?) {}

                override fun onEvent(eventType: Int, params: Bundle?) {}
            })

            try {
                r.startListening(intent)
            } catch (_: Exception) {
                if (!cancelled.get()) onDone(null)
            }
        }
    }

    override fun cancel() {
        cancelled.set(true)
        mainHandler.post { cancelRecognizer() }
    }

    private fun cancelRecognizer() {
        recognizer?.let {
            runCatching { it.cancel() }
            runCatching { it.destroy() }
        }
        recognizer = null
    }

    private fun makeRecognizer(): SpeechRecognizer {
        val onDevice = Build.VERSION.SDK_INT >= 31 &&
            runCatching { SpeechRecognizer.isOnDeviceRecognitionAvailable(context) }.getOrDefault(false)
        val ways = RecognizerChoice.order(Build.VERSION.SDK_INT, onDevice, googleService != null, false)
        for (w in ways) {
            val r = runCatching {
                when (w) {
                    RecognizerChoice.Way.ON_DEVICE -> SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
                    RecognizerChoice.Way.GOOGLE_SERVICE -> SpeechRecognizer.createSpeechRecognizer(context, googleService)
                    RecognizerChoice.Way.DEFAULT -> SpeechRecognizer.createSpeechRecognizer(context)
                }
            }.getOrNull()
            if (r != null) return r
        }
        return SpeechRecognizer.createSpeechRecognizer(context)
    }
}
