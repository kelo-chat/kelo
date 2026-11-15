package chat.kelo.desktop.pages.my

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.RadioButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.AvatarSaved
import chat.kelo.app.chat.AgentActivityRules
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Languages
import chat.kelo.app.core.Profile
import chat.kelo.app.my.ToneRules
import chat.kelo.app.ui.character.KeloCharacter
import chat.kelo.app.ui.character.KeloMood
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringArrayResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.IconTitle
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.KeyBox
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MenuGroup
import chat.kelo.desktop.ui.MenuRow
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.MyCard
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.Locale

/* 마이's account pages (android/kelo .../my/AccountPages.kt and my/AgentPage.kt): privacy, my name, the language, the agent. */

/** Plain-language answers to "what happens to my data". */
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

/** A form label. */
@Composable
fun FieldLabel(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, fontWeight = FontWeight.Medium, color = Kelo.colors.ink)
}

/** My name and the assistant's name (PUT /api/profile). */
@Composable
fun ProfilePage(app: AppContainer, profile: Profile, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    var user by remember { mutableStateOf(profile.userName) }
    var agent by remember { mutableStateOf(profile.agentName) }
    var saved by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    fun save() {
        err = ""
        scope.launch {
            try {
                app.setProfile(app.sessions.requireApi().put<Profile>("/api/profile", buildJsonObject {
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

/** 앱 → 언어: following the system, or one of the app's languages (each name in its own language). */
@Composable
fun LanguagePage(app: AppContainer, onBack: () -> Unit) {
    val c = Kelo.colors
    val value by app.settings.language.collectAsState()
    fun pick(code: String) { app.settings.setLanguage(code); app.applyLanguage(); app.tellServerLanguage(code) }
    SubPage(stringResource(R.string.my_language), onBack) {
        Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(20.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(20.dp)).padding(horizontal = 16.dp)) {
            val system = Languages.suggested(listOf(Locale.getDefault().toLanguageTag()))
            LanguageRow(stringResource(R.string.language_system), Languages.name(system), value == Languages.SYSTEM) { pick(Languages.SYSTEM) }
            Languages.ALL.forEach { l ->
                HorizontalDivider(color = c.line)
                LanguageRow(l.name, null, value == l.code) { pick(l.code) }
            }
        }
    }
}

@Composable
private fun LanguageRow(name: String, detail: String?, selected: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.RadioButton, onClick = onClick).semantics { this.selected = selected }.padding(vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(name, style = MaterialTheme.typography.bodyLarge, color = c.ink, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal)
            if (detail != null) Text(detail, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        if (selected) Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(20.dp))
    }
}

/** The agent's picture (GET /api/agent/avatar), or Kelo's character when there is none. */
@Composable
fun AgentPicture(image: ImageBitmap?, size: Int) {
    Box(Modifier.size(size.dp).clip(CircleShape).background(Color(0xFFFFF3D6)), contentAlignment = Alignment.Center) {
        if (image != null) Image(image, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.size(size.dp))
        else KeloCharacter(KeloMood.Idle, Modifier.size((size * 0.9f).dp))
    }
}

/** The agent's picture for [version] (0 = none), fetched when the version changes. */
suspend fun loadAvatar(api: KeloApi, version: Long): ImageBitmap? {
    if (version <= 0) return null
    return try { decodeImage(api.download("/api/agent/avatar")) } catch (e: ApiError) { null }
}

/**
 * 마이 → 에이전트: the agent's picture (pick an image file, or go back to Kelo's character), its name, and what it is doing now.
 */
@Composable
fun AgentPage(app: AppContainer, profile: Profile, onBack: () -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val status by app.activity.status.collectAsState()
    var version by remember { mutableStateOf(profile.avatarVersion) }
    var image by remember { mutableStateOf<ImageBitmap?>(null) }
    var rename by remember { mutableStateOf(false) }
    var confirmRemove by remember { mutableStateOf(false) }
    var working by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf("") }
    val failedText = stringResource(R.string.agent_avatar_failed)
    val tooLarge = stringResource(R.string.agent_avatar_too_large)
    val toneFailed = stringResource(R.string.my_agent_tone_failed)
    var savingTone by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(version) { image = runCatching { loadAvatar(app.sessions.requireApi(), version) }.getOrNull() }
    // the agent may have changed the tone in the chat ("반말로 해 줘"): the server's profile as it is now (as on the phone)
    LaunchedEffect(Unit) { runCatching { ToneRules.reload(app.sessions.requireApi()) }.onSuccess(app::setProfile) }

    fun pickTone(tone: String) {
        if (savingTone != null || tone == ToneRules.selected(profile)) return
        savingTone = tone
        note = ""
        scope.launch {
            try {
                app.setProfile(ToneRules.save(app.sessions.requireApi(), tone))
            } catch (e: Exception) {
                note = toneFailed
            } finally {
                savingTone = null
            }
        }
    }

    fun pick() {
        val f = chat.kelo.desktop.chat.pickFiles().firstOrNull() ?: return
        note = ""
        working = true
        scope.launch {
            try {
                if (f.length() > MAX_AVATAR) { note = tooLarge; return@launch }
                val bytes = withContext(Dispatchers.IO) { f.readBytes() }
                val type = runCatching { java.nio.file.Files.probeContentType(f.toPath()) }.getOrNull() ?: "image/jpeg"
                val api = app.sessions.requireApi()
                val req = api.requestBuilder("/api/agent/avatar").put(bytes.toRequestBody(type.toMediaTypeOrNull())).build()
                val answer = KeloApi.readJson(api.send(req).toString(Charsets.UTF_8))
                version = KeloApi.json.decodeFromJsonElement(AvatarSaved.serializer(), answer).avatarVersion
                app.profile.value?.let { app.setProfile(it.copy(avatarVersion = version)) }
            } catch (e: ApiError) {
                note = if (e.status == 413) tooLarge else e.detail.takeIf { !e.isNetwork } ?: failedText
            } catch (e: Exception) {
                note = failedText
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
                    AgentPicture(image, 56)
                    if (working) CircularProgressIndicator(Modifier.size(56.dp), color = c.accent, strokeWidth = 2.dp)
                }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    MutedText(stringResource(if (image != null) R.string.my_agent_picture_hint else R.string.my_agent_picture_default), small = true)
                    if (image != null) LinkButton(stringResource(R.string.agent_avatar_remove), onClick = { confirmRemove = true }, danger = true)
                }
            }
            SecondaryButton(stringResource(R.string.agent_avatar_pick), enabled = !working, onClick = ::pick, modifier = Modifier.fillMaxWidth())
            if (note.isNotEmpty()) ErrorText(note, small = true)
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
            onYes = {
                scope.launch {
                    try {
                        app.sessions.requireApi().delete<JsonElement>("/api/agent/avatar")
                        version = 0
                        app.profile.value?.let { app.setProfile(it.copy(avatarVersion = 0)) }
                    } catch (e: ApiError) { note = failedText }
                }
            },
        )
    }
    if (rename) RenameAgentDialog(app, profile, onClose = { rename = false })
}

/** One tone: a radio, its name and, under it, an example sentence in that tone (as on the phone). */
@Composable
private fun ToneRow(label: String, example: String, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).heightIn(min = 56.dp)
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

private const val MAX_AVATAR = 2_000_000L

@Composable
private fun RenameAgentDialog(app: AppContainer, profile: Profile, onClose: () -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf(profile.agentName) }
    var error by remember { mutableStateOf<String?>(null) }
    var saving by remember { mutableStateOf(false) }
    val failed = stringResource(R.string.connect_failed)
    AlertDialog(
        onDismissRequest = onClose, containerColor = c.card,
        title = { Text(stringResource(R.string.agent_rename_title), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                KeloTextField(name, { name = it }, stringResource(R.string.agent_rename_placeholder), maxLength = 60)
                error?.let { Text(it, color = c.danger, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = {
            TextButton(enabled = name.isNotBlank() && !saving, onClick = {
                saving = true
                scope.launch {
                    try {
                        app.setProfile(app.sessions.requireApi().put<Profile>("/api/profile", buildJsonObject { put("agent_name", name.trim()) }))
                        onClose()
                    } catch (e: ApiError) {
                        error = if (e.isNetwork) failed else e.detail
                    } finally { saving = false }
                }
            }) { Text(stringResource(R.string.save), color = c.accent) }
        },
        dismissButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}
