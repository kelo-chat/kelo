package chat.kelo.app.call

import kotlin.math.log10
import kotlin.math.sqrt

/** Finds one utterance in a stream of 16 kHz mono PCM16: start after enough speech, end after enough quiet. Energy against an adaptive
 *  noise floor, 20 ms windows. The server's Silero VAD still trims the clip, and an empty transcript is simply ignored.
 *  End after a clear pause: 1 s, longer after pauses inside the utterance (at most 1.8 s); hysteresis keeps soft sounds speech. */
class SpeechGate(
    private val startDb: Float = 12f,         // above the floor to start an utterance
    private val bargeStartDb: Float = 18f,    // the same while Kelo is speaking (echo left after cancelling)
    private val holdDb: Float = 6f,           // above the floor to stay speech inside an utterance (soft syllables, falling endings)
    private val bargeHoldDb: Float = 12f,     // the same while Kelo is speaking
    private val absMinDb: Float = -50f,       // and above this absolute level (dBFS) to start
    private val absHoldDb: Float = -56f,      // and above this to stay speech
    private val startMs: Int = 200,           // speech needed (within the last 300 ms) to start
    private val bargeStartMs: Int = 300,
    private val endMs: Int = 1000,            // quiet that ends an utterance (owner 2026-11-20: 700 ms cut sentences at their pauses)
    private val pauseExtraMs: Int = 500,      // after a pause inside the utterance, the end waits this much longer than that pause
    private val maxEndMs: Int = 1800,         // and never longer than this
    private val innerPauseMs: Int = 300,      // a quiet run this long that speech follows is a pause inside the utterance
    private val resumeMs: Int = 60,           // speech this long in a row ends a quiet run (a click does not)
    private val preRollMs: Int = 300,         // kept from before the start
    private val maxMs: Int = 60_000,          // forced end (a call turn is short; the STT cap is 120 s)
    private val minSpeechMs: Int = 300,       // shorter = dropped (a cough, a click)
) {
    sealed interface Out {
        data object Started : Out
        data class Ended(val pcm16le: ByteArray) : Out {
            override fun equals(other: Any?): Boolean {
                if (this === other) return true
                if (other !is Ended) return false
                return pcm16le.contentEquals(other.pcm16le)
            }
            override fun hashCode(): Int = pcm16le.contentHashCode()
        }
        data object Dropped : Out
    }

    var speaking: Boolean = false             // Kelo is talking now: use the barge thresholds

    private var floor: Float = -60f
    val floorDb: Float get() = floor

    private var remainder = ByteArray(0)

    // Pre-roll: keep recent windows before utterance starts
    private val maxPreRollWindows = preRollMs / 20
    private val preRollWindows = ArrayDeque<ByteArray>()

    // Utterance tracking
    private var inUtterance = false
    private val utteranceChunks = mutableListOf<ByteArray>()
    private var utteranceTotalMs = 0
    private var utteranceSpeechMs = 0
    private var utteranceQuietMs = 0
    private var speechRunMs = 0               // speech in a row inside the utterance
    private var pauseAtRun = 0                // the quiet run that speech just ended
    private var longestPauseMs = 0            // the longest inner pause so far

    // Recent 300 ms history for starting speech (15 windows)
    private val recentWindows = ArrayDeque<Boolean>()

    fun feed(pcm16le: ByteArray): List<Out> {
        val all = if (remainder.isEmpty()) pcm16le else remainder + pcm16le
        val windowBytes = 640 // 20 ms at 16 kHz mono 16-bit
        val numWindows = all.size / windowBytes
        remainder = if (all.size % windowBytes != 0) {
            all.copyOfRange(numWindows * windowBytes, all.size)
        } else {
            ByteArray(0)
        }

        val out = mutableListOf<Out>()
        for (w in 0 until numWindows) {
            val offset = w * windowBytes
            val window = all.copyOfRange(offset, offset + windowBytes)
            processWindow(window, out)
        }
        return out
    }

    private fun processWindow(window: ByteArray, out: MutableList<Out>) {
        val db = calcDb(window)
        val neededDb = if (speaking) bargeStartDb else startDb
        val isSpeech = db >= (floor + neededDb) && db >= absMinDb

        if (!inUtterance) {
            // Noise floor tracks incoming non-utterance audio
            floor = (0.95f * floor + 0.05f * db).coerceIn(-75f, -30f)

            recentWindows.addLast(isSpeech)
            if (recentWindows.size > 15) {
                recentWindows.removeFirst()
            }

            preRollWindows.addLast(window)
            if (preRollWindows.size > maxPreRollWindows) {
                preRollWindows.removeFirst()
            }

            val neededSpeechMs = if (speaking) bargeStartMs else startMs
            val speechCount = recentWindows.count { it }
            if (speechCount * 20 >= neededSpeechMs) {
                inUtterance = true
                out.add(Out.Started)

                utteranceChunks.clear()
                for (chunk in preRollWindows) {
                    utteranceChunks.add(chunk)
                }
                utteranceTotalMs = preRollWindows.size * 20
                utteranceSpeechMs = speechCount * 20
                utteranceQuietMs = 0
                speechRunMs = 0
                pauseAtRun = 0
                longestPauseMs = 0
                recentWindows.clear()
                preRollWindows.clear()
            }
        } else {
            val holds = db >= floor + (if (speaking) bargeHoldDb else holdDb) && db >= absHoldDb
            if (holds) {
                utteranceSpeechMs += 20
                if (speechRunMs == 0) pauseAtRun = utteranceQuietMs   // the quiet run before this speech
                speechRunMs += 20
                if (utteranceQuietMs > 0) {
                    if (speechRunMs >= resumeMs) {                   // real speech again: that quiet run was a pause inside the utterance
                        if (pauseAtRun >= innerPauseMs) longestPauseMs = maxOf(longestPauseMs, pauseAtRun)
                        utteranceQuietMs = 0
                    } else {
                        utteranceQuietMs += 20                       // too short to tell from a click: still quiet
                    }
                }
            } else {
                floor = (0.99f * floor + 0.01f * db).coerceIn(-75f, -30f)   // slowly: a soft word must not lift the floor over itself
                speechRunMs = 0
                utteranceQuietMs += 20
            }

            utteranceChunks.add(window)
            utteranceTotalMs += 20

            if (utteranceQuietMs >= endNeededMs() || utteranceTotalMs >= maxMs) {
                finishUtterance(out)
            }
        }
    }

    /** The quiet that ends this utterance: [endMs], or [pauseExtraMs] more than its longest inner pause, at most [maxEndMs]. */
    private fun endNeededMs(): Int = if (longestPauseMs == 0) endMs else minOf(maxEndMs, maxOf(endMs, longestPauseMs + pauseExtraMs))

    private fun finishUtterance(out: MutableList<Out>) {
        val totalBytes = utteranceChunks.sumOf { it.size }
        val pcm = ByteArray(minOf(totalBytes, maxMs * 32))
        var offset = 0
        for (chunk in utteranceChunks) {
            if (offset >= pcm.size) break
            val toCopy = minOf(chunk.size, pcm.size - offset)
            chunk.copyInto(pcm, offset, 0, toCopy)
            offset += toCopy
        }

        if (utteranceSpeechMs < minSpeechMs) {
            out.add(Out.Dropped)
        } else {
            out.add(Out.Ended(pcm))
        }

        inUtterance = false
        utteranceChunks.clear()
        utteranceTotalMs = 0
        utteranceSpeechMs = 0
        utteranceQuietMs = 0
        speechRunMs = 0
        pauseAtRun = 0
        longestPauseMs = 0
        recentWindows.clear()
        preRollWindows.clear()
    }

    fun reset() {
        floor = -60f
        inUtterance = false
        remainder = ByteArray(0)
        utteranceChunks.clear()
        utteranceTotalMs = 0
        utteranceSpeechMs = 0
        utteranceQuietMs = 0
        speechRunMs = 0
        pauseAtRun = 0
        longestPauseMs = 0
        recentWindows.clear()
        preRollWindows.clear()
    }

    private fun calcDb(window: ByteArray): Float {
        var sumSq = 0.0
        for (i in 0 until 320) {
            val lo = window[i * 2].toInt() and 0xFF
            val hi = window[i * 2 + 1].toInt()
            val sample = ((hi shl 8) or lo).toShort().toDouble()
            sumSq += sample * sample
        }
        val rms = sqrt(sumSq / 320.0)
        if (rms <= 0.0) return -100f
        return (20.0 * log10(rms / 32768.0)).toFloat()
    }
}
