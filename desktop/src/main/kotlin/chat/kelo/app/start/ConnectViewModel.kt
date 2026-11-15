package chat.kelo.app.start

import chat.kelo.app.core.PairingLinks

// The plain rule of android/kelo .../start/ConnectViewModel.kt (that file is an Android view model), under the same name so the
// shared Android test PairingLinksTest runs here too. The desktop's connect screen (chat.kelo.desktop.ui.PairScreen) uses it.
// Keep in step with it.
class ConnectViewModel private constructor() {
    companion object {
        /** A typed code may be split by single spaces ("ABCD EFGH ..."), as people copy it from notes; the server ignores them. */
        fun codeOf(text: String): String = text.trim().replace(" ", "")

        /** A pairing code the server could accept (PairIn: 16-128 characters, no spaces once [codeOf] removed them). */
        fun looksLikeCode(text: String): Boolean = codeOf(text).let { t ->
            t.length in PairingLinks.CODE_LENGTH && t.none { it.isWhitespace() || it.isISOControl() }
        }
    }
}
