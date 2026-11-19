package chat.kelo.app.call.platform

/** The phone's own recognizer as fallback ears when server STT is not ready (brief §4.10, C9). */
interface PhoneListener {
    /**
     * Listens for one utterance in [language] (the owner's language) and ends by itself after a pause (the recognizer's own end of
     * speech). [onLevel] 0..1 for the waveform. [onDone]: the final text ("" when nothing was heard), or null on an error.
     */
    fun listenOnce(language: String, onStarted: () -> Unit, onLevel: (Float) -> Unit, onDone: (String?) -> Unit)

    /** Cancels listening immediately. */
    fun cancel()
}
