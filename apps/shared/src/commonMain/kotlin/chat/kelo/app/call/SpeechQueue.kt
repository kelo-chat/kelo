package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.voice.LevelMeter
import chat.kelo.app.voice.Wav
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/** Speaks reply sentences in order, prefetching up to [CallRules.PREFETCH] ahead (brief §4.8). */
class SpeechQueue(
    private val scope: CoroutineScope,
    private val audio: CallAudio,
    private val server: suspend (String, String) -> ByteArray?, // (text, lang) → WAV or null
    private val device: () -> SpeechEngine?,
    private val serverLang: (String) -> String?,
    private val appLang: () -> String,
    private val onLevel: (Float) -> Unit,
    private val onEvent: (Event) -> Unit,
) {
    enum class Event { FIRST_PLAY, FIRST_AUDIO, IDLE, DEVICE_SPOKE }

    private sealed interface Playable {
        data class ServerAudio(val text: String, val pcm: ByteArray, val sampleRate: Int) : Playable
        data class DeviceEngine(val text: String, val lang: String) : Playable
    }

    private val pendingTexts = mutableListOf<String>()
    private val readyChannel = Channel<Playable>(Channel.UNLIMITED)

    private var workerJob: Job? = null
    private var prefetchJob: Job? = null

    private var _playing = false
    val playing: Boolean get() = _playing

    private var firstAudioFired = false
    private var firstPlayFired = false
    private var deviceSpokeFired = false
    private val lock = Any()

    fun add(piece: String) {
        val trimmed = piece.trim()
        if (trimmed.isEmpty()) return
        synchronized(lock) {
            pendingTexts.add(trimmed)
            _playing = true
        }
        ensureJobs()
    }

    fun stop() {
        synchronized(lock) {
            pendingTexts.clear()
            _playing = false
        }
        prefetchJob?.cancel()
        prefetchJob = null
        workerJob?.cancel()
        workerJob = null
        // Drain channel
        while (readyChannel.tryReceive().isSuccess) {}
        audio.stopPlayback()
        device()?.stop()
    }

    private fun ensureJobs() {
        if (prefetchJob == null || prefetchJob?.isCompleted == true) {
            prefetchJob = scope.launch { runPrefetch() }
        }
        if (workerJob == null || workerJob?.isCompleted == true) {
            workerJob = scope.launch { runPlayback() }
        }
    }

    private suspend fun runPrefetch() {
        try {
            while (true) {
                val nextText: String? = synchronized(lock) {
                    if (pendingTexts.isNotEmpty()) pendingTexts.removeAt(0) else null
                }
                if (nextText == null) break

                val lang = SpeechLang.of(nextText, appLang())
                val sLang = serverLang(lang)
                val playable: Playable = if (sLang != null) {
                    val wav = try {
                        server(nextText, sLang)
                    } catch (_: Exception) {
                        null
                    }
                    val decoded = if (wav != null) Wav.decode(wav) else null
                    if (decoded != null) {
                        Playable.ServerAudio(nextText, decoded.first, decoded.second)
                    } else {
                        Playable.DeviceEngine(nextText, lang)
                    }
                } else {
                    Playable.DeviceEngine(nextText, lang)
                }

                if (!firstAudioFired) {
                    firstAudioFired = true
                    onEvent(Event.FIRST_AUDIO)
                }
                readyChannel.send(playable)
            }
        } catch (_: CancellationException) {
            // Cancelled
        }
    }

    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    private suspend fun runPlayback() {
        try {
            while (true) {
                val isEmpty = synchronized(lock) { pendingTexts.isEmpty() }
                if (isEmpty && readyChannel.isEmpty) {
                    synchronized(lock) { _playing = false }
                    firstAudioFired = false
                    firstPlayFired = false
                    onEvent(Event.IDLE)
                    break
                }

                val item = readyChannel.receive()
                if (!firstPlayFired) {
                    firstPlayFired = true
                    onEvent(Event.FIRST_PLAY)
                }

                when (item) {
                    is Playable.ServerAudio -> {
                        val meter = LevelMeter(item.sampleRate)
                        audio.play(item.pcm, item.sampleRate) { lvl ->
                            val levels = meter.add(ByteArray(640)) // LevelMeter over playback
                            onLevel(lvl)
                        }
                    }
                    is Playable.DeviceEngine -> {
                        if (!deviceSpokeFired) {
                            deviceSpokeFired = true
                            onEvent(Event.DEVICE_SPOKE)
                        }
                        onLevel(0f)
                        suspendCancellableCoroutine { cont ->
                            val dev = device()
                            if (dev == null) {
                                cont.resume(Unit)
                            } else {
                                dev.speak(item.text, item.lang) {
                                    if (cont.isActive) cont.resume(Unit)
                                }
                            }
                        }
                    }
                }
            }
        } catch (_: CancellationException) {
            // Cancelled
        } finally {
            synchronized(lock) { _playing = false }
        }
    }
}
