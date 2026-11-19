package chat.kelo.app.call

import chat.kelo.app.call.platform.CallAudio
import chat.kelo.app.chat.SpeechEngine
import chat.kelo.app.voice.Wav
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * Speaks reply pieces in order (brief call-mode §4.8). One worker per burst of pieces: it starts the fetch of up to [CallRules.PREFETCH]
 * pieces (the next one included), waits for the next one, starts the following fetch, plays, and repeats. The worker ends (and
 * [Event.IDLE] fires) only when nothing waits AND nothing is being fetched, checked under the same lock [add] takes, so a piece is never
 * left behind (2026-11-19: a one-piece reply whose speak fetch was in flight was never played). [device] is the call's one device voice.
 */
class SpeechQueue(
    private val scope: CoroutineScope,
    private val audio: CallAudio,
    private val server: suspend (String, String) -> ByteArray?, // (text, lang) -> WAV, or null
    private val device: SpeechEngine?,
    private val serverLang: (String) -> String?,
    private val appLang: () -> String,
    private val onLevel: (Float) -> Unit,
    private val onEvent: (Event) -> Unit,
    private val log: (String) -> Unit = {},
    private val clock: () -> Long = { System.currentTimeMillis() },
) {
    /**
     * FIRST_AUDIO: the first piece of a burst is ready; FIRST_PLAY: it starts playing; IDLE: nothing left; DEVICE_SPOKE: the device voice
     * spoke a piece; VOICE_FALLBACK: Kelo's own voice was expected but its audio failed, so the device voice spoke instead; SILENT: a piece
     * could not be spoken at all (no device voice, or it failed). The last three fire once per queue (once per call).
     */
    enum class Event { FIRST_PLAY, FIRST_AUDIO, IDLE, DEVICE_SPOKE, VOICE_FALLBACK, SILENT }

    private sealed interface Playable {
        val n: Int
        class ServerAudio(override val n: Int, val pcm: ByteArray, val sampleRate: Int) : Playable
        class DeviceEngine(override val n: Int, val text: String, val lang: String, val fallback: Boolean) : Playable
    }

    private enum class Next { MORE, IDLE, STOPPED }

    private val lock = Any()
    private val pendingTexts = ArrayDeque<String>()
    private var running = false      // a worker owns the queue: set by add(), cleared by that worker when idle, or by stop() (under lock)
    private var generation = 0       // bumped by stop(): an old worker never touches a newer one's state
    private var pieceNo = 0
    private var workerJob: Job? = null

    private var _playing = false
    val playing: Boolean get() = synchronized(lock) { _playing }

    private var deviceSpokeFired = false
    private var fallbackFired = false
    private var silentFired = false

    fun add(piece: String) {
        val trimmed = piece.trim()
        if (trimmed.isEmpty()) return
        val start: Int? = synchronized(lock) {
            pendingTexts.addLast(trimmed)
            _playing = true
            if (running) null else { running = true; generation }
        }
        if (start != null) workerJob = scope.launch { runWorker(start) }
    }

    fun stop() {
        synchronized(lock) {
            pendingTexts.clear()
            _playing = false
            running = false
            generation++
        }
        workerJob?.cancel()
        workerJob = null
        audio.stopPlayback()
        device?.stop()
    }

    private suspend fun runWorker(gen: Int) = coroutineScope {
        val ahead = ArrayDeque<Deferred<Playable>>()
        var firstAudio = false
        var firstPlay = false
        var leftIdle = false
        fun fill() {
            while (ahead.size < CallRules.PREFETCH) {
                val taken = synchronized(lock) {
                    if (gen != generation) null else pendingTexts.removeFirstOrNull()?.let { (++pieceNo) to it }
                } ?: return
                ahead.addLast(async { fetch(taken.first, taken.second) })
            }
        }
        try {
            while (true) {
                fill()
                val next = ahead.removeFirstOrNull()
                if (next == null) {
                    val what = synchronized(lock) {
                        when {
                            gen != generation -> Next.STOPPED
                            pendingTexts.isNotEmpty() -> Next.MORE
                            else -> { running = false; _playing = false; leftIdle = true; Next.IDLE }
                        }
                    }
                    if (what == Next.MORE) continue
                    if (what == Next.IDLE) { log("queue idle"); onEvent(Event.IDLE) }
                    return@coroutineScope
                }
                val item = next.await()
                if (!firstAudio) { firstAudio = true; onEvent(Event.FIRST_AUDIO) }
                fill()   // fetch ahead while this piece plays
                if (!firstPlay) { firstPlay = true; onEvent(Event.FIRST_PLAY) }
                play(item)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("queue failed: ${CallLog.why(e)}")
        } finally {
            ahead.forEach { it.cancel() }
            // An unexpected end (not idle, not stopped) frees the queue so the next add() starts a worker again.
            if (!leftIdle) synchronized(lock) { if (gen == generation && running) { running = false; _playing = false } }
        }
    }

    private suspend fun fetch(n: Int, text: String): Playable {
        val lang = SpeechLang.of(text, appLang())
        val sLang = serverLang(lang)
        if (sLang == null) {
            log("piece $n voice=device reason=no_server_voice lang=$lang")
            return Playable.DeviceEngine(n, text, lang, fallback = false)
        }
        val t0 = clock()
        val wav = try {
            server(text, sLang)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("piece $n speak failed: ${CallLog.why(e)}")
            null
        }
        val decoded = wav?.let { Wav.decode(it) }
        if (wav == null || decoded == null) {
            if (wav != null) log("piece $n speak audio not playable bytes=${wav.size}")
            else log("piece $n speak gave no audio")
            return Playable.DeviceEngine(n, text, lang, fallback = true)
        }
        log("piece $n fetched voice=server lang=$sLang ms=${clock() - t0} bytes=${wav.size} rate=${decoded.second}")
        return Playable.ServerAudio(n, decoded.first, decoded.second)
    }

    private suspend fun play(item: Playable) {
        try {
            when (item) {
                is Playable.ServerAudio -> {
                    log("piece ${item.n} play voice=server")
                    audio.play(item.pcm, item.sampleRate) { lvl -> onLevel(lvl) }
                }
                is Playable.DeviceEngine -> {
                    if (item.fallback && !fallbackFired) { fallbackFired = true; onEvent(Event.VOICE_FALLBACK) }
                    val dev = device
                    if (dev == null) {
                        log("piece ${item.n} silent: no device voice")
                        silent()
                        return
                    }
                    if (!deviceSpokeFired) { deviceSpokeFired = true; onEvent(Event.DEVICE_SPOKE) }
                    onLevel(0f)
                    log("piece ${item.n} play voice=device")
                    val spoke = suspendCancellableCoroutine<Boolean> { cont ->
                        cont.invokeOnCancellation { dev.stop() }
                        dev.speakChecked(item.text, item.lang) { ok -> if (cont.isActive) cont.resume(ok) }
                    }
                    if (!spoke) {
                        log("piece ${item.n} silent: device voice failed")
                        silent()
                    }
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            log("piece ${item.n} play failed: ${CallLog.why(e)}")
        }
    }

    private fun silent() {
        if (!silentFired) { silentFired = true; onEvent(Event.SILENT) }
    }
}
