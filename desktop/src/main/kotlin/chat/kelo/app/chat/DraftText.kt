package chat.kelo.app.chat

// From android/kelo .../chat/ChatViewModel.kt (that file is an Android view model). Keep in step with it.

/** Shared text joining what is already typed. */
object DraftText {
    fun append(draft: String, text: String): String = when {
        text.isBlank() -> draft
        draft.isBlank() -> text
        else -> draft.trimEnd() + "\n" + text
    }
}
