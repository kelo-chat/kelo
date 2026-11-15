package chat.kelo.app.start

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.AppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.PairingLink
import chat.kelo.app.core.PairingLinks
import chat.kelo.app.core.ServerAddress
import chat.kelo.app.core.UiText
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ConnectState(
    /** A pairing link is being used: the whole screen says "이 기기를 연결하는 중…". */
    val pairing: Boolean = false,
    /** Why the last pairing link did not work, shown at the top. */
    val notice: UiText? = null,
    val linkError: UiText? = null,
    val keyBusy: Boolean = false,
    val keyError: UiText? = null,
    /** A valid pairing link waiting for "이 서버에 연결할까요?": nothing is sent before the user sees which server it is. */
    val confirm: PairingLink? = null,
)

/** Signs this device in: with a pairing link (scanned or pasted) or with a server address and the access key. */
class ConnectViewModel(private val container: AppContainer) : ViewModel() {
    private val _state = MutableStateFlow(ConnectState())
    val state: StateFlow<ConnectState> = _state.asStateFlow()

    /** The address this device used before, to pre-fill the key form. */
    val lastServerUrl: String = container.sessions.lastServerUrl().orEmpty()

    /** A scanned or pasted link. Returns false (and shows why) when it is not a Kelo pairing link. */
    fun useLink(raw: String): Boolean {
        val link = PairingLinks.parse(raw)
        if (link == null) {
            _state.update { it.copy(linkError = UiText.Res(R.string.connect_link_invalid)) }
            return false
        }
        if (!ServerAddress.isAllowed(link.serverUrl)) {
            _state.update { it.copy(linkError = UiText.Res(R.string.connect_https_only)) }
            return false
        }
        if (_state.value.pairing) return true
        _state.update { it.copy(confirm = link, notice = null, linkError = null) }
        return true
    }

    fun cancelPair() = _state.update { it.copy(confirm = null) }

    /** The user confirmed the server shown in the dialog. */
    fun confirmPair() {
        val link = _state.value.confirm ?: return
        if (_state.value.pairing) return
        _state.update { it.copy(confirm = null, pairing = true) }
        viewModelScope.launch {
            try {
                container.sessions.signIn(container.pairing.pair(link, container.deviceName()))
                _state.update { it.copy(pairing = false) }
            } catch (e: ApiError) {
                // the server's own words (an expired or used link), "could not reach the server", or a server too old (no KE2E)
                val why = when {
                    e.code == chat.kelo.app.core.PairingClient.CODE_SERVER_OLD -> serverOld()
                    e.isNetwork -> UiText.Res(R.string.connect_failed)
                    else -> UiText.Raw(e.detail)
                }
                _state.update { it.copy(pairing = false, notice = why) }
            }
        }
    }

    fun connectWithKey(address: String, key: String) {
        val server = ServerAddress.normalize(address)
        when {
            server == null -> _state.update { it.copy(keyError = UiText.Res(R.string.connect_server_invalid)) }
            !ServerAddress.isAllowed(server) -> _state.update { it.copy(keyError = UiText.Res(R.string.connect_https_only)) }
            else -> {
                _state.update { it.copy(keyBusy = true, keyError = null) }
                viewModelScope.launch {
                    try {
                        val (session, _) = container.pairing.checkKey(server, key.trim())
                        container.sessions.signIn(session)
                        _state.update { it.copy(keyBusy = false) }
                    } catch (e: ApiError) {
                        if (e.code == chat.kelo.app.core.PairingClient.CODE_SERVER_OLD) {
                            _state.update { it.copy(keyBusy = false, keyError = serverOld()) }
                            return@launch
                        }
                        // not an access key: it may be a sign-in code typed by hand (the server's code, e.g. an app store
                        // reviewer's from the review notes), which signs this device in like a scanned link
                        if (e.isUnauthorized && looksLikeCode(key)) {
                            try {
                                container.sessions.signIn(container.pairing.pair(PairingLink(server, codeOf(key)), container.deviceName()))
                                _state.update { it.copy(keyBusy = false) }
                                return@launch
                            } catch (_: ApiError) {
                                // fall through: say the key did not match
                            }
                        }
                        val why = if (e.isUnauthorized) R.string.connect_key_wrong else R.string.connect_failed
                        _state.update { it.copy(keyBusy = false, keyError = UiText.Res(why)) }
                    }
                }
            }
        }
    }

    fun clearLinkError() = _state.update { it.copy(linkError = null) }

    /** The server offers no end-to-end encryption: it must be updated first (this app never talks in the clear, spec 5.18). */
    private fun serverOld(): UiText =
        UiText.Raw(container.appContext.getString(R.string.e2e_server_old_body, chat.kelo.app.BuildConfig.MIN_SERVER))

    companion object {
        /** A pairing code the server could accept (PairIn: 16-128 characters, no spaces). */
        /** A typed code may be split by single spaces ("ABCD EFGH ..."), as people copy it from notes; the server ignores them. */
        fun codeOf(text: String): String = text.trim().replace(" ", "")

        fun looksLikeCode(text: String): Boolean = codeOf(text).let { t ->
            t.length in PairingLinks.CODE_LENGTH && t.none { it.isWhitespace() || it.isISOControl() }
        }
    }
}
