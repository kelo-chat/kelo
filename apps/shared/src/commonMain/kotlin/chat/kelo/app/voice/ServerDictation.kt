package chat.kelo.app.voice

import kotlin.math.max
import kotlin.math.min
import kotlin.math.sqrt

/** Which way the mic works right now, from the server's voice view and what this device has (one rule for all three apps). */
object DictationWay {
    enum class Way { SERVER, PHONE, OFFER_THEN_PHONE, OFFER_THEN_NONE, DOWNLOADING_PHONE, DOWNLOADING_NONE, NONE }

    /** view == null (no answer: an older server) → the phone's recognizer; no phone recognizer → nothing. */
    fun pick(view: VoiceView?, phoneRecognizer: Boolean): Way {
        fun phoneOr(): Way = if (phoneRecognizer) Way.PHONE else Way.NONE
        if (view == null) return phoneOr()
        if (view.runtime != "ready") return phoneOr()
        return when (view.state) {
            "ready" -> Way.SERVER
            "offered" -> if (phoneRecognizer) Way.OFFER_THEN_PHONE else Way.OFFER_THEN_NONE
            "downloading" -> if (phoneRecognizer) Way.DOWNLOADING_PHONE else Way.DOWNLOADING_NONE
            else -> phoneOr()     // off (declined), failed, unsupported
        }
    }
}

/**
 * The mic's download ring: while the speech model downloads, the mic icon is a circle that shows progress (the owner's addition
 * 2026-11-18). null = nothing downloads (the mic is drawn); else [Ring.percent] is the progress, or null when the server gives none
 * (an indeterminate spinner).
 */
object VoiceDownloadRing {
    data class Ring(val percent: Int?)

    fun of(view: VoiceView?): Ring? {
        if (view?.state != "downloading") return null
        val p = view.progress ?: return Ring(null)
        if (p.total <= 0L) return Ring(null)
        return Ring(((p.done * 100L) / p.total).toInt().coerceIn(0, 100))
    }
}

/** Recording steps: idle → recording → sending → idle; any → idle on cancel. The screen runs the actions it is told. */
object RecordingSteps {
    const val MAX_MS = 120_000L
    const val COUNTDOWN_FROM_MS = 110_000L

    enum class Phase { IDLE, RECORDING, SENDING }
    enum class Action { NONE, OPEN_MIC, CLOSE_AND_SEND, CLOSE_AND_DROP, INSERT, SHOW }

    sealed interface Event {
        data object Start : Event
        data object Stop : Event
        data object Cancel : Event
        data class Tick(val elapsedMs: Long) : Event
        data class Sent(val text: String) : Event
        data class Failed(val code: String) : Event
    }

    data class Step(val phase: Phase, val action: Action, val text: String = "", val code: String = "")

    fun step(phase: Phase, e: Event): Step = when (e) {
        Event.Start -> if (phase == Phase.IDLE) Step(Phase.RECORDING, Action.OPEN_MIC) else Step(phase, Action.NONE)
        Event.Stop -> if (phase == Phase.RECORDING) Step(Phase.SENDING, Action.CLOSE_AND_SEND) else Step(phase, Action.NONE)
        Event.Cancel -> when (phase) {
            Phase.RECORDING -> Step(Phase.IDLE, Action.CLOSE_AND_DROP)
            Phase.SENDING -> Step(Phase.IDLE, Action.NONE)          // the reply is ignored
            Phase.IDLE -> Step(Phase.IDLE, Action.NONE)
        }
        is Event.Tick -> if (phase == Phase.RECORDING && e.elapsedMs >= MAX_MS) Step(Phase.SENDING, Action.CLOSE_AND_SEND)
            else Step(phase, Action.NONE)
        is Event.Sent -> if (phase == Phase.SENDING || phase == Phase.IDLE) Step(Phase.IDLE, Action.INSERT, text = e.text)
            else Step(phase, Action.NONE)
        is Event.Failed -> if (phase == Phase.SENDING) Step(Phase.IDLE, Action.SHOW, code = e.code) else Step(phase, Action.NONE)
    }
}

/** 16-bit PCM (little endian) → a WAV file: RIFF header of 44 bytes, PCM, mono. */
object Wav {
    fun encode(pcm16le: ByteArray, sampleRate: Int = 16000): ByteArray {
        val out = ByteArray(44 + pcm16le.size)
        fun put32(at: Int, v: Int) { for (i in 0..3) out[at + i] = (v ushr (8 * i)).toByte() }
        fun put16(at: Int, v: Int) { out[at] = v.toByte(); out[at + 1] = (v ushr 8).toByte() }
        "RIFF".forEachIndexed { i, c -> out[i] = c.code.toByte() }
        put32(4, 36 + pcm16le.size)
        "WAVEfmt ".forEachIndexed { i, c -> out[8 + i] = c.code.toByte() }
        put32(16, 16)
        put16(20, 1)                       // PCM
        put16(22, 1)                       // mono
        put32(24, sampleRate)
        put32(28, sampleRate * 2)          // byte rate
        put16(32, 2)                       // block align
        put16(34, 16)                      // bits per sample
        "data".forEachIndexed { i, c -> out[36 + i] = c.code.toByte() }
        put32(40, pcm16le.size)
        pcm16le.copyInto(out, 44)
        return out
    }

    /** 16-bit PCM mono WAV bytes → (pcm16le, sampleRate); null if malformed, stereo, or not 16-bit PCM. */
    fun decode(bytes: ByteArray): Pair<ByteArray, Int>? {
        if (bytes.size < 44) return null
        if (bytes[0] != 'R'.code.toByte() || bytes[1] != 'I'.code.toByte() ||
            bytes[2] != 'F'.code.toByte() || bytes[3] != 'F'.code.toByte() ||
            bytes[8] != 'W'.code.toByte() || bytes[9] != 'A'.code.toByte() ||
            bytes[10] != 'V'.code.toByte() || bytes[11] != 'E'.code.toByte()
        ) return null

        var sampleRate = 0
        var audioFormat = 0
        var numChannels = 0
        var bitsPerSample = 0
        var pcmData: ByteArray? = null

        var offset = 12
        while (offset + 8 <= bytes.size) {
            val chunkId = bytes.decodeToString(offset, offset + 4)
            val chunkSize = (bytes[offset + 4].toInt() and 0xFF) or
                ((bytes[offset + 5].toInt() and 0xFF) shl 8) or
                ((bytes[offset + 6].toInt() and 0xFF) shl 16) or
                ((bytes[offset + 7].toInt() and 0xFF) shl 24)
            offset += 8
            if (chunkSize < 0 || offset + chunkSize > bytes.size) {
                if (chunkId == "data" && chunkSize > 0 && offset <= bytes.size) {
                    pcmData = bytes.copyOfRange(offset, bytes.size)
                }
                break
            }

            if (chunkId == "fmt " && chunkSize >= 16) {
                audioFormat = (bytes[offset].toInt() and 0xFF) or ((bytes[offset + 1].toInt() and 0xFF) shl 8)
                numChannels = (bytes[offset + 2].toInt() and 0xFF) or ((bytes[offset + 3].toInt() and 0xFF) shl 8)
                sampleRate = (bytes[offset + 4].toInt() and 0xFF) or
                    ((bytes[offset + 5].toInt() and 0xFF) shl 8) or
                    ((bytes[offset + 6].toInt() and 0xFF) shl 16) or
                    ((bytes[offset + 7].toInt() and 0xFF) shl 24)
                bitsPerSample = (bytes[offset + 14].toInt() and 0xFF) or ((bytes[offset + 15].toInt() and 0xFF) shl 8)
            } else if (chunkId == "data") {
                pcmData = bytes.copyOfRange(offset, offset + chunkSize)
            }

            offset += chunkSize + (chunkSize and 1)
        }

        if (audioFormat != 1 || numChannels != 1 || bitsPerSample != 16 || sampleRate <= 0 || pcmData == null) {
            return null
        }
        return Pair(pcmData, sampleRate)
    }
}

/** Levels for the sound graph: RMS of a stretch of samples, 0..1 on a square-root curve. */
object SoundBars {
    fun rms(pcm16le: ByteArray, from: Int, to: Int): Float {
        var sum = 0.0
        var n = 0
        var i = max(0, from - (from % 2))
        val end = min(to, pcm16le.size - 1)
        while (i + 1 <= end) {
            val s = ((pcm16le[i].toInt() and 0xff) or (pcm16le[i + 1].toInt() shl 8)).toShort().toDouble() / 32768.0
            sum += s * s
            n++
            i += 2
        }
        if (n == 0) return 0f
        val rms = sqrt(sum / n).toFloat()
        return sqrt(rms.coerceIn(0f, 1f))
    }

}

/** Down-sampling for devices that will not open a 16 kHz line: a box filter over each output sample's input span. */
object Resample {
    fun to16k(pcm16le: ByteArray, fromRate: Int): ByteArray {
        if (fromRate == 16000) return pcm16le
        val inCount = pcm16le.size / 2
        val ratio = fromRate.toDouble() / 16000.0
        val outCount = (inCount / ratio).toInt()
        val out = ByteArray(outCount * 2)
        for (j in 0 until outCount) {
            val start = (j * ratio).toInt()
            val stop = min(inCount, max(start + 1, ((j + 1) * ratio).toInt()))
            var sum = 0.0
            for (k in start until stop) {
                sum += ((pcm16le[2 * k].toInt() and 0xff) or (pcm16le[2 * k + 1].toInt() shl 8)).toShort().toDouble()
            }
            val v = (sum / (stop - start)).toInt().coerceIn(-32768, 32767)
            out[2 * j] = v.toByte()
            out[2 * j + 1] = (v shr 8).toByte()
        }
        return out
    }
}

/** What went wrong with a server recognition, for the one line under the composer. */
enum class VoiceProblem { NOTHING_HEARD, OFFLINE, BUSY, TOO_LONG, NOT_READY, FAILED }

/** httpStatus null = no connection. NOTHING_HEARD is decided by the caller (the text came back empty). */
fun voiceProblem(httpStatus: Int?, code: String?): VoiceProblem = when {
    httpStatus == null -> VoiceProblem.OFFLINE
    httpStatus == 429 -> VoiceProblem.BUSY
    httpStatus == 413 -> VoiceProblem.TOO_LONG
    httpStatus == 409 -> VoiceProblem.NOT_READY
    else -> VoiceProblem.FAILED
}
