package chat.kelo.app.my

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.agent.AgentAvatar
import chat.kelo.app.agent.AgentPicture
import chat.kelo.app.agent.RenameDialog
import chat.kelo.app.chat.AgentActivityRules
import chat.kelo.app.chat.AttachmentReader
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Profile
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 마이 → 에이전트: the agent's picture (pick one from the phone, or go back to Kelo's character), its name and its tone (친근 / 공손, each
 * with an example sentence; [ToneRules]) as settings rows, and what it is doing right now. The profile is read again when the page
 * opens, so a tone the agent changed in the chat shows as chosen.
 */
@Composable
fun AgentPage(profile: Profile, onProfile: (Profile) -> Unit, onBack: () -> Unit) {
    val c = Kelo.colors
    val container = LocalAppContainer.current
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val haptics = rememberHaptics()
    val image by container.agentAvatar.image.collectAsStateWithLifecycle()
    val status by container.agentActivity.status.collectAsStateWithLifecycle()
    var rename by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }
    var working by remember { mutableStateOf(false) }
    val failedText = stringResource(R.string.agent_avatar_failed)
    val tooLarge = stringResource(R.string.agent_avatar_too_large)
    val toneFailed = stringResource(R.string.my_agent_tone_failed)
    var savingTone by remember { mutableStateOf<String?>(null) }
    fun toast(text: String) = android.widget.Toast.makeText(context, text, android.widget.Toast.LENGTH_SHORT).show()
    // the agent may have changed the tone in the chat ("반말로 해 줘"): the server's profile as it is now
    LaunchedEffect(Unit) { runCatching { ToneRules.reload(container.sessions.requireApi()) }.onSuccess(onProfile) }
    fun pickTone(tone: String) {
        if (savingTone != null || tone == ToneRules.selected(profile)) return
        savingTone = tone
        scope.launch {
            try {
                onProfile(ToneRules.save(container.sessions.requireApi(), tone))
                haptics.confirm()
            } catch (e: Exception) {
                toast(toneFailed)
            } finally {
                savingTone = null
            }
        }
    }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
        if (uri == null) return@rememberLauncherForActivityResult
        working = true
        scope.launch {
            try {
                val api = container.sessions.requireApi()
                val p = withContext(Dispatchers.IO) { AttachmentReader.photo(context.contentResolver, uri, AgentAvatar.MAX_BYTES) }
                container.agentAvatar.upload(api, p.bytes ?: throw AttachmentReader.Unreadable(), p.type ?: "image/jpeg")
                haptics.confirm()
            } catch (e: AttachmentReader.TooLarge) {
                toast(tooLarge)
            } catch (e: ApiError) {
                toast(if (e.status == 413) tooLarge else e.detail.takeIf { !e.isNetwork } ?: failedText)
            } catch (e: Exception) {
                toast(failedText)
            } finally {
                working = false
            }
        }
    }

    SubPage(stringResource(R.string.my_agent_title), onBack) {
        SectionLabel(stringResource(R.string.my_agent_picture))
        MyCard {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Box(contentAlignment = Alignment.Center) {
                    AgentPicture(profile.agentName, image, 56)
                    if (working) CircularProgressIndicator(Modifier.size(56.dp), color = c.accent, strokeWidth = 2.dp)
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    MutedText(stringResource(if (image != null) R.string.my_agent_picture_hint else R.string.my_agent_picture_default), small = true)
                    if (image != null) LinkButton(stringResource(R.string.agent_avatar_remove), onClick = { confirmRemove = true }, danger = true)
                }
            }
            SecondaryButton(
                stringResource(R.string.agent_avatar_pick), enabled = !working,
                onClick = { runCatching { pick.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) } },
                modifier = Modifier.fillMaxWidth(),
            )
        }
        SectionLabel(stringResource(R.string.my_agent_identity))
        MenuGroup {
            row { MenuRow(KeloIcons.Edit, stringResource(R.string.profile_agent_name), { rename = true }, value = profile.agentName) }
        }
        SectionLabel(stringResource(R.string.my_agent_tone))
        val shownTone = savingTone ?: ToneRules.selected(profile)
        Column(Modifier.fillMaxWidth().selectableGroup()) {
            ToneRow(stringResource(R.string.my_agent_tone_friendly), stringResource(R.string.my_agent_tone_friendly_example),
                selected = shownTone == ToneRules.FRIENDLY, enabled = savingTone == null) { pickTone(ToneRules.FRIENDLY) }
            ToneRow(stringResource(R.string.my_agent_tone_polite), stringResource(R.string.my_agent_tone_polite_example),
                selected = shownTone == ToneRules.POLITE, enabled = savingTone == null) { pickTone(ToneRules.POLITE) }
        }
        SectionLabel(stringResource(R.string.my_agent_now))
        MyCard {
            val line = status?.let { AgentActivityRules.line(it) }
            Text(line ?: stringResource(R.string.agent_idle), style = MaterialTheme.typography.bodyMedium, color = if (line != null) c.ink else c.muted)
        }
    }
    if (confirmRemove) {
        ConfirmDialog(
            text = stringResource(R.string.agent_avatar_remove_confirm), yes = stringResource(R.string.agent_avatar_remove_yes),
            onDismiss = { confirmRemove = false },
            onYes = { scope.launch { runCatching { container.agentAvatar.remove(container.sessions.requireApi()) }.onFailure { toast(failedText) } } },
        )
    }
    if (rename) RenameDialog(profile, onProfile, onClose = { rename = false })
}

/** One tone: a radio, its name and, under it, an example sentence in that tone (wraps in any language). */
@Composable
private fun ToneRow(label: String, example: String, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().heightIn(min = 56.dp)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        RadioButton(selected = selected, onClick = null, colors = RadioButtonDefaults.colors(selectedColor = c.accent, unselectedColor = c.muted))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(label, style = MaterialTheme.typography.bodyLarge, color = c.ink, fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal)
            Text(example, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
    }
}
