package chat.kelo.app.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.my.ConfirmDialog
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** DELETE /api/providers/{name}/login answer. */
@Serializable
data class SignOutResult(
    val ok: Boolean = false,
    val removed: Boolean = false,
    /** A token the server's own settings hold: the app cannot take it away */
    @SerialName("still_signed_in") val stillSignedIn: Boolean = false,
    /** The fallback model was on this provider and is now empty */
    @SerialName("fallback_cleared") val fallbackCleared: Boolean = false,
)

/** When the sign-out action shows, and what an answer means. */
object SignOutRules {
    enum class Outcome { DONE, DONE_FALLBACK_CLEARED, DONE_ENV_LEFT, MAIN_MODEL, FAILED }

    /** Only a provider that is signed in now offers it. */
    fun offered(signedIn: Boolean?): Boolean = signedIn == true

    /** The saved main model runs on [name]: the server refuses, so the note shows instead of the action. */
    fun isMain(name: String, mainProvider: String): Boolean = mainProvider.isNotEmpty() && mainProvider == name

    /** The provider list should be loaded again (the sign-in is gone). */
    fun done(o: SignOutRules.Outcome): Boolean = o != Outcome.FAILED && o != Outcome.MAIN_MODEL

    fun outcome(result: SignOutResult?, error: ApiError?): Outcome = when {
        error != null && error.status == 409 -> Outcome.MAIN_MODEL
        error != null && error.status == 404 -> Outcome.DONE          // already signed out somewhere else: nothing left to remove
        error != null -> Outcome.FAILED
        result?.stillSignedIn == true -> Outcome.DONE_ENV_LEFT
        result?.fallbackCleared == true -> Outcome.DONE_FALLBACK_CLEARED
        else -> Outcome.DONE
    }
}

/**
 * The signed-in account of a provider reached by signing in (Claude, agy, Codex, Qwen, MiniMax) and 로그아웃 with a confirm step.
 * [onResult] runs after the server answered with what it means and the server's reason (for FAILED); the caller shows it with
 * [SignOutMessage] and, when it is done, reloads the list (the row then shows it is not connected and this box goes away).
 */
@Composable
fun SignOutBox(
    name: String,
    displayName: String,
    account: String?,
    mainProvider: String,
    /** For the saved main model: only the account line (the chat sheet), instead of the note why it cannot sign out (the model page) */
    quietWhenMain: Boolean = false,
    onResult: (outcome: SignOutRules.Outcome, result: SignOutResult?, detail: String) -> Unit,
) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var confirm by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    val main = SignOutRules.isMain(name, mainProvider)

    fun signOut() {
        busy = true
        scope.launch {
            var result: SignOutResult? = null
            var error: ApiError? = null
            try {
                result = sessions.requireApi().delete<SignOutResult>("/api/providers/${KeloApi.segment(name)}/login")
            } catch (e: ApiError) {
                error = e
            } finally {
                busy = false
            }
            onResult(SignOutRules.outcome(result, error), result, error?.takeIf { !it.isNetwork }?.detail.orEmpty())
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        MutedText(
            if (account.isNullOrBlank()) stringResource(R.string.model_signin_connected) else stringResource(R.string.model_signin_account, account),
            small = true,
        )
        when {
            busy -> MutedText(stringResource(R.string.model_signout_busy), small = true)
            main && quietWhenMain -> {}
            main -> MutedText(stringResource(R.string.model_signout_main), small = true)
            else -> LinkButton(stringResource(R.string.model_signout), onClick = { confirm = true }, danger = true)
        }
    }
    if (confirm) {
        ConfirmDialog(
            text = stringResource(R.string.model_signout_confirm, displayName), yes = stringResource(R.string.model_signout),
            onYes = ::signOut, onDismiss = { confirm = false },
        )
    }
}

/** The line after an answer; the caller's page keeps it, since the box itself goes away once the provider is signed out. */
@Composable
fun SignOutMessage(outcome: SignOutRules.Outcome?, detail: String = "") {
    when (outcome) {
        null -> {}
        SignOutRules.Outcome.DONE -> MutedText(stringResource(R.string.model_signout_done), small = true)
        SignOutRules.Outcome.DONE_FALLBACK_CLEARED -> MutedText(stringResource(R.string.model_signout_fallback_cleared), small = true)
        SignOutRules.Outcome.DONE_ENV_LEFT -> ErrorText(stringResource(R.string.model_signout_env_left), small = true)
        SignOutRules.Outcome.MAIN_MODEL -> ErrorText(stringResource(R.string.model_signout_main), small = true)
        SignOutRules.Outcome.FAILED -> ErrorText(stringResource(R.string.model_signout_failed, detail).trim(), small = true)
    }
}
