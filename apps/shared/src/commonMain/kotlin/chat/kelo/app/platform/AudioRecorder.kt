package chat.kelo.app.platform

/**
 * The phone's (or desktop's) microphone for server voice input: 16 kHz, mono, 16-bit little endian PCM, kept in memory only.
 * [start] returns false when no line opens (no microphone, or the permission is refused); [onChunk] gets each piece as it comes
 * (it runs on the recording thread, so it only stores or counts). [stop] ends the recording and hands back all of it.
 */
interface AudioRecorder {
    val available: Boolean
    fun start(onChunk: (ByteArray) -> Unit): Boolean
    fun stop(): ByteArray
    fun cancel()
}
