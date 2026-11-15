package chat.kelo.app.start

import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.shell.MainShell
import chat.kelo.app.ui.components.CenterMessage
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.PrimaryButton

/**
 * The first-run steps and the signed-in app, in order. There is no language step: the app starts in the phone's language (English
 * when the app lacks it), and My -> Language changes it. [onSettled] is called once the first screen is known (the launch screen
 * waits for it).
 */
@Composable
fun AppRoot(onSettled: () -> Unit = {}) {
    val container = LocalAppContainer.current
    val vm: AppViewModel = viewModel { AppViewModel(container) }
    val api by container.sessions.api.collectAsStateWithLifecycle()
    val profile by vm.profile.collectAsStateWithLifecycle()
    val settled = ProfileRules.settled(api != null, profile)
    SideEffect { if (settled) onSettled() }

    when {
        api == null -> ConnectFlow()
        else -> when (val p = profile) {
            ProfileState.SignedOut, ProfileState.Loading -> CenterMessage(stringResource(R.string.loading))
            ProfileState.Failed -> CenterMessage(stringResource(R.string.connect_failed)) {
                PrimaryButton(stringResource(R.string.retry), onClick = vm::retryProfile)
                LinkButton(stringResource(R.string.connect_other_server), onClick = vm::signOut)
            }
            is ProfileState.Ready ->
                if (p.profile.agentName.isBlank()) OnboardingScreen(initial = p.profile, onDone = vm::setProfile)
                else MainShell(profile = p.profile, onProfile = vm::setProfile, onSignedOut = vm::signOut)
        }
    }
}
