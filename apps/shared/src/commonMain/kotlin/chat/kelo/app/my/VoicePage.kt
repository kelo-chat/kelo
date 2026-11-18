package chat.kelo.app.my

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.RadioButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import chat.kelo.app.core.Languages
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.MyCard
import chat.kelo.app.ui.components.PrimaryButton
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.update.UpdateRules
import chat.kelo.app.voice.ModelView
import chat.kelo.app.voice.VoiceModel
import chat.kelo.app.voice.VoiceView
import chat.kelo.app.voice.voiceDownloadingLine
import chat.kelo.app.voice.voiceProblemLine

/** My → 음성 입력: the speech model on the Kelo server, the models to pick from, and removing the model. No licence text here. */
@Composable
fun VoicePage(voice: VoiceModel, onBack: () -> Unit) {
    val c = Kelo.colors
    val view by voice.view.collectAsState()
    val error by voice.error.collectAsState()
    var confirmRemove by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { voice.refresh() }
    SubPage(string(Res.string.voice_page_title), onBack) {
        MutedText(string(Res.string.voice_page_intro))
        val v: VoiceView? = view
        if (v == null) {
            MutedText(string(Res.string.voice_state_off))
        } else {
            MyCard {
                val current = v.model
                if (current != null) {
                    Text(current.name, color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.titleSmall)
                    MutedText(string(Res.string.voice_model_size, UpdateRules.size(current.sizeBytes), UpdateRules.size(current.memoryBytes)))
                    Text(stateLine(v), color = if (v.state == "failed") c.dangerInk else c.muted, style = androidx.compose.material3.MaterialTheme.typography.bodySmall)
                } else {
                    Text(string(Res.string.voice_state_off), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium)
                    if (v.recommended != null) PrimaryButton(string(Res.string.voice_download_model), { voice.choose(v.recommended.id) }, Modifier.fillMaxWidth())
                }
                if (v.runtime != "ready") MutedText(string(Res.string.voice_runtime_missing))
                if (v.state == "unsupported") MutedText(string(Res.string.voice_phone_note))
            }
            if (v.models.isNotEmpty()) {
                MyCard {
                    Text(string(Res.string.voice_change_model), color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.titleSmall)
                    v.models.forEach { m -> ModelChoice(m, selected = v.model?.id == m.id, enabled = v.state != "downloading") { voice.choose(m.id) } }
                }
            }
            if (v.model != null) {
                SecondaryButton(string(Res.string.voice_remove_model), { confirmRemove = true }, Modifier.fillMaxWidth())
            }
        }
        error?.let { ErrorText(voiceProblemLine(it)) }
    }
    if (confirmRemove) {
        ConfirmDialog(
            text = string(Res.string.voice_remove_confirm),
            yes = string(Res.string.voice_remove_model),
            onYes = { voice.remove() },
            onDismiss = { confirmRemove = false },
        )
    }
}

/** The state line under the model: the percent while it downloads, else "in use" or the phone's recognizer. */
@Composable
private fun stateLine(v: VoiceView): String {
    val p = v.progress
    return when (v.state) {
        "downloading" -> voiceDownloadingLine(p?.done ?: 0, p?.total ?: 0)
        "ready" -> string(Res.string.voice_model_in_use)
        "failed" -> string(Res.string.voice_server_download_failed)
        else -> string(Res.string.voice_state_off)
    }
}

@Composable
private fun ModelChoice(model: ModelView, selected: Boolean, enabled: Boolean, onPick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier
            .fillMaxWidth()
            .clickable(enabled = enabled && !selected, onClick = onPick)
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        RadioButton(selected = selected, onClick = if (enabled && !selected) onPick else null, enabled = enabled)
        Spacer(Modifier.size(8.dp))
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(model.name, color = c.ink, style = androidx.compose.material3.MaterialTheme.typography.bodyMedium)
            MutedText(model.languages.joinToString(", ") { Languages.name(it) }, small = true)
            MutedText(string(Res.string.voice_model_size, UpdateRules.size(model.sizeBytes), UpdateRules.size(model.memoryBytes)), small = true)
        }
    }
}
