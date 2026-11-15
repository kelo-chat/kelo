package chat.kelo.app.start

import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusDirection
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Profile
import chat.kelo.app.my.FieldLabel
import chat.kelo.app.ui.components.CenterCard
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The default agent name in the setup form (the app's own name). */
private const val DEFAULT_AGENT_NAME = "Kelo"

/**
 * First visit to a server whose profile has no agent name yet: one short setup form with the user's name and the agent's name
 * (filled in with "Kelo"); both can be changed later in 마이.
 */
@Composable
fun OnboardingScreen(initial: Profile, onDone: (Profile) -> Unit) {
    val api = LocalAppContainer.current.sessions
    var userName by rememberSaveable { mutableStateOf(initial.userName) }
    var agentName by rememberSaveable { mutableStateOf(initial.agentName.ifBlank { DEFAULT_AGENT_NAME }) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    val failed = stringResource(R.string.connect_failed)
    val ready = userName.isNotBlank() && agentName.isNotBlank() && !busy

    fun finish() {
        if (!ready) return
        busy = true
        error = null
        scope.launch {
            try {
                onDone(api.requireApi().put<Profile>("/api/profile", buildJsonObject {
                    put("user_name", userName.trim())
                    put("agent_name", agentName.trim())
                }))
            } catch (e: ApiError) {
                error = if (e.isNetwork) failed else e.detail
            } finally {
                busy = false
            }
        }
    }

    CenterCard {
        Text(stringResource(R.string.onboarding_hello), style = MaterialTheme.typography.headlineMedium)
        MutedText(stringResource(R.string.onboarding_intro))
        FieldLabel(stringResource(R.string.onboarding_name_label))
        KeloTextField(
            value = userName, onValueChange = { userName = it }, maxLength = 60,
            placeholder = stringResource(R.string.onboarding_name_placeholder),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            keyboardActions = KeyboardActions(onNext = { focus.moveFocus(FocusDirection.Down) }),
        )
        FieldLabel(stringResource(R.string.onboarding_agent_label))
        KeloTextField(
            value = agentName, onValueChange = { agentName = it }, maxLength = 60,
            placeholder = stringResource(R.string.onboarding_agent_placeholder),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { finish() }),
        )
        MutedText(stringResource(R.string.onboarding_agent_question), small = true)
        error?.let { ErrorText(it, small = true) }
        PrimaryButton(stringResource(R.string.onboarding_start), onClick = ::finish, enabled = ready, modifier = Modifier)
    }
}
