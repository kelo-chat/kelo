package chat.kelo.desktop.pages.my

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.Profile
import chat.kelo.desktop.chat.ModelSettings
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.DesktopCaches
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.SubPage

/**
 * 마이 → 모델 (android/kelo .../my/ModelPage.kt): which AI service and model answer and how the service is reached (an API key, an
 * address, a sign-in and 로그아웃, the server's CLI), the fallback model (never on the main model's provider), and saving them.
 * Saving goes back to 마이; an error keeps the page open with the reason.
 */
@Composable
fun ModelPage(app: AppContainer, onBack: () -> Unit, cliProvider: String? = null) {
    val profile by app.profile.collectAsState()
    SubPage(stringResource(R.string.model_title), onBack = onBack) {
        ModelSettings(
            app.sessions, profile ?: Profile(), startProvider = null, onProfile = app::setProfile,
            onProvidersChanged = { list -> runCatching { app.sessions.keep(DesktopCaches.PROVIDERS, list) } },
            onDone = onBack, cliProvider = cliProvider, showBackground = true,
            body = { content -> MyCard { content() } },
            footer = { content -> Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) { content() } },
        )
    }
}
