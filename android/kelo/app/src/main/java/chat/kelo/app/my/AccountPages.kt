package chat.kelo.app.my

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.res.stringArrayResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Profile
import chat.kelo.app.start.LanguageList
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Plain-language answers to "what happens to my data". Everything here is something the app really does. */
@Composable
fun PrivacyPage(onBack: () -> Unit) {
    val titles = stringArrayResource(R.array.privacy_titles)
    val bodies = stringArrayResource(R.array.privacy_bodies)
    SubPage(stringResource(R.string.privacy_title), onBack) {
        MyCard {
            titles.zip(bodies).forEachIndexed { i, (t, d) ->
                KeyBox(first = i == 0) {
                    IconTitle(KeloIcons.Shield, t)
                    MutedText(d, small = true)
                }
            }
        }
    }
}

/** My name and the assistant's name (PUT /api/profile). */
@Composable
fun ProfilePage(profile: Profile, onProfile: (Profile) -> Unit, onBack: () -> Unit) {
    val sessions = LocalAppContainer.current.sessions
    val scope = rememberCoroutineScope()
    var user by rememberSaveable { mutableStateOf(profile.userName) }
    var agent by rememberSaveable { mutableStateOf(profile.agentName) }
    var saved by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }

    fun save() {
        err = ""
        scope.launch {
            try {
                onProfile(sessions.requireApi().put<Profile>("/api/profile", buildJsonObject {
                    put("user_name", user.trim()); put("agent_name", agent.trim())
                }))
                saved = true
            } catch (e: ApiError) {
                err = e.detail
            }
        }
    }
    SubPage(stringResource(R.string.profile_title), onBack) {
        MyCard {
            FieldLabel(stringResource(R.string.profile_user_name))
            KeloTextField(user, { saved = false; user = it }, stringResource(R.string.profile_user_name), maxLength = 60)
            FieldLabel(stringResource(R.string.profile_agent_name))
            KeloTextField(agent, { saved = false; agent = it }, stringResource(R.string.profile_agent_name), maxLength = 60)
            PrimaryButton(stringResource(if (saved) R.string.saved_check else R.string.save), onClick = ::save, enabled = agent.isNotBlank())
            if (err.isNotEmpty()) ErrorText(err, small = true)
        }
    }
}

/** 앱 → 언어: the language registry. A change is kept on this phone and the screen is made again in that language. */
@Composable
fun LanguagePage(value: String, onChange: (String) -> Unit, onBack: () -> Unit) {
    val title = stringResource(R.string.my_language)
    SubPage(title, onBack) {
        LanguageList(value = value, onChange = onChange, label = title)
    }
}

/** A form label (".form label"). */
@Composable
fun FieldLabel(text: String) {
    Text(text, style = androidx.compose.material3.MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
}
