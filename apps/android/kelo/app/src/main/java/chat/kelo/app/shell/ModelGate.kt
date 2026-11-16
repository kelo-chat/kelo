package chat.kelo.app.shell

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.components.Card
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/** Whether the app must first lead the owner to choose a model; kept apart from the UI so it can be tested. */
object ModelGate {
    /** Only when the server says so: an older server that does not report it never gets the card. */
    fun needsModel(profile: Profile): Boolean = profile.modelReady == false
}

/**
 * The first thing a new owner sees on the chat while the server has no model: the setup in the terminal does not ask for one, so
 * the app does. The button opens the model settings (provider, model and its key). It stays until a model is chosen.
 */
@Composable
fun ChooseModelCard(onChoose: () -> Unit, modifier: Modifier = Modifier) {
    Card(modifier.shadow(8.dp, MaterialTheme.shapes.large).semantics { liveRegion = LiveRegionMode.Polite }) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(KeloIcons.Chat, contentDescription = null, tint = Kelo.colors.accent, modifier = Modifier.size(22.dp))
            Text(stringResource(R.string.model_first_title), style = MaterialTheme.typography.titleMedium)
        }
        MutedText(stringResource(R.string.model_first_body))
        PrimaryButton(stringResource(R.string.model_first_button), onClick = onChoose)
    }
}
