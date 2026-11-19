package chat.kelo.app.call.platform

/** The platform audio operations for an in-app call (brief §4.7). */
interface CallAudio {
    /** True when the platform cancels Kelo's voice from the microphone (allows barge-in). */
    val echoCancel: Boolean

    /** Phones true, desktop false. */
    val hasSpeakerSwitch: Boolean

    /** Whether the loud speaker is on (phones only). */
    var speakerOn: Boolean

    /** Starts recording 16 kHz mono PCM16; returns false if no mic or permission denied. */
    fun startMic(onChunk: (ByteArray) -> Unit): Boolean

    /** Stops microphone recording. */
    fun stopMic()

    /** Plays mono PCM16 at [sampleRate], reporting window levels for the waveform; returns when played or stopped. */
    suspend fun play(pcm16le: ByteArray, sampleRate: Int, onLevel: (Float) -> Unit)

    /** Aborts current playback immediately. */
    fun stopPlayback()

    /** Soft chime ringing sound (C16); respects silent/ringer mode. */
    fun chime(on: Boolean)

    /** Releases audio focus and device resources. */
    fun release()

    /** Callback invoked when audio focus is lost or session is interrupted by another app or phone call. */
    var onInterrupted: (() -> Unit)?
}
