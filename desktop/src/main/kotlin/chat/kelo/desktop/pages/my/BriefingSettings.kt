package chat.kelo.desktop.pages.my

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.PointerIcon
import androidx.compose.ui.input.pointer.pointerHoverIcon
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.BriefingSettings
import chat.kelo.app.my.BriefingSettingsRules
import chat.kelo.app.my.BriefingSources
import chat.kelo.app.my.BriefingSourcesRules
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.SelectField
import chat.kelo.desktop.chat.SelectOption
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.work.clockText
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import androidx.compose.ui.text.style.TextOverflow
import chat.kelo.app.ui.components.TokenText
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// 마이 → 알림 → 아침 브리핑 and 읽는 계정, as android/kelo .../my/BriefingSettings.kt and my/BriefingSources.kt draw them (the model and rules are in
// chat.kelo.app.my.BriefingModels). One difference on a computer: it has no location of its own; the weather follows where the
// owner's phone last was (sent when the Kelo app is opened there), and without any location one quiet line says so.

@Composable
private fun providerLabel(id: String): String = when (id) {
    "open_meteo" -> "Open-Meteo"
    "kma" -> stringResource(R.string.briefing_provider_kma)
    "weatherapi" -> "WeatherAPI.com"
    else -> stringResource(R.string.briefing_provider_auto)
}

@Composable
private fun offsetLabel(min: Int): String =
    if (min == 0) stringResource(R.string.briefing_offset_none) else stringResource(R.string.briefing_offset_min, min)

/**
 * The morning briefing's settings: on or off; when (at the start of the day's hours plus a few minutes, earlier on the morning's
 * first app open, or a fixed time); which sections (the weather follows the phone's location); quiet days; the weather service and the owner's
 * own keys; "get one now". Which calendars and mailboxes it reads is its own section ([BriefingSourcesBlock]).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BriefingBlock(app: AppContainer, b: BriefingSettings, onChanged: (BriefingSettings) -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var picking by remember { mutableStateOf(false) }
    var keyFor by remember { mutableStateOf<String?>(null) }
    var sent by remember { mutableStateOf<Boolean?>(null) }
    var error by remember { mutableStateOf("") }
    var runError by remember { mutableStateOf("") }   // "get one now" refused (e.g. at night: when the next one comes), under its button
    fun call(block: suspend () -> BriefingSettings) {
        scope.launch { try { error = ""; onChanged(block()) } catch (e: ApiError) { error = e.detail } }
    }
    fun put(body: JsonObject) = call { app.sessions.requireApi().put("/api/briefing", body) }

    BriefingToggle(stringResource(R.string.briefing_switch), stringResource(R.string.briefing_about), b.enabled) { on -> put(buildJsonObject { put("enabled", on) }) }
    if (error.isNotEmpty()) ErrorText(error, small = true, modifier = Modifier.padding(horizontal = 16.dp))
    if (!b.enabled) return
    Column(Modifier.padding(horizontal = 16.dp).widthIn(max = 520.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.briefing_when), style = MaterialTheme.typography.labelLarge, color = c.muted)
        SelectField(
            listOf(SelectOption("auto", stringResource(R.string.briefing_when_auto)), SelectOption("fixed", stringResource(R.string.briefing_when_fixed))),
            b.timeMode, stringResource(R.string.briefing_when),
            onPick = { v -> put(buildJsonObject { put("time_mode", v) }) },
        )
        if (b.timeMode == "fixed") {
            SecondaryButton(stringResource(R.string.briefing_fixed_at, NotifySettingsRules.parseTime(b.fixedTime)?.let { (h, m) -> clockText(h, m) } ?: b.fixedTime),
                onClick = { picking = true }, modifier = Modifier.fillMaxWidth())
        } else {
            Text(stringResource(R.string.briefing_offset), style = MaterialTheme.typography.bodySmall, color = c.muted)
            SelectField(
                BriefingSettingsRules.offsets(b.offsetMin).map { SelectOption(it.toString(), offsetLabel(it)) },
                b.offsetMin.toString(), stringResource(R.string.briefing_offset),
                onPick = { v -> v.toIntOrNull()?.let { put(buildJsonObject { put("offset_min", it) }) } },
            )
        }
    }
    if (b.timeMode != "fixed") {
        BriefingToggle(stringResource(R.string.briefing_early_open), stringResource(R.string.briefing_early_open_about), b.earlyOpen) { on ->
            put(buildJsonObject { put("early_open", on) })
        }
    }

    // what
    Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 8.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(stringResource(R.string.briefing_sections), style = MaterialTheme.typography.labelLarge, color = c.muted)
        BriefingSettingsRules.SECTIONS.forEach { id ->
            val label = stringResource(when (id) { "weather" -> R.string.briefing_weather; "calendar" -> R.string.briefing_today; else -> R.string.briefing_missed })
            CheckLine(label, BriefingSettingsRules.sectionOn(b, id)) { on -> put(BriefingSettingsRules.sectionBody(id, on)) }
            // a computer has no location of its own: the weather follows the phone's, and shows once a phone sent one
            if (id == "weather" && BriefingSettingsRules.needsLocation(b, granted = true)) {
                Box(Modifier.padding(start = 32.dp, bottom = 4.dp)) { MutedText(stringResource(R.string.desktop_briefing_weather_follows_phone), small = true) }
            }
        }
        CheckLine(stringResource(R.string.briefing_quiet_skip), b.quietDays == "skip") { on -> put(buildJsonObject { put("quiet_days", if (on) "skip" else "short") }) }
    }

    // the weather service
    Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.briefing_provider), style = MaterialTheme.typography.labelLarge, color = c.muted)
        Box(Modifier.widthIn(max = 520.dp)) {
            SelectField(
                BriefingSettingsRules.PROVIDERS.map { SelectOption(it, providerLabel(it)) },
                b.weather.provider, stringResource(R.string.briefing_provider),
                onPick = { v -> put(buildJsonObject { put("provider", v) }) },
            )
        }
        MutedText(stringResource(R.string.briefing_provider_about), small = true)
        BriefingSettingsRules.KEYED.forEach { id ->
            val saved = b.weather.keys[id] == true
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Icon(KeloIcons.Key, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp))
                Column(Modifier.weight(1f)) {
                    Text(providerLabel(id), style = MaterialTheme.typography.bodyMedium, color = c.ink)
                    Text(stringResource(if (saved) R.string.briefing_key_saved else R.string.briefing_key_none), style = MaterialTheme.typography.bodySmall,
                        color = c.muted)
                }
                if (saved) {
                    SecondaryButton(stringResource(R.string.briefing_key_delete), onClick = {
                        call { app.sessions.requireApi().delete("/api/briefing/keys/$id") }
                    })
                } else SecondaryButton(stringResource(R.string.briefing_key_add), onClick = { keyFor = id })
            }
        }
        val credit = b.weather.providers.firstOrNull { it.id == "open_meteo" }?.attribution ?: "Weather data by Open-Meteo.com"
        MutedText(credit, small = true)   // the data's licence (CC BY 4.0) asks for this credit
        SecondaryButton(
            stringResource(if (sent == true) R.string.briefing_sent else R.string.briefing_try),
            enabled = sent == null,
            onClick = {
                sent = false
                runError = ""
                scope.launch {
                    try { app.sessions.requireApi().post<JsonElement>("/api/briefing/run"); sent = true } catch (e: ApiError) { sent = null; runError = e.detail }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
        if (runError.isNotEmpty()) ErrorText(runError, small = true)
    }

    if (picking) {
        val start = NotifySettingsRules.parseTime(b.fixedTime) ?: (7 to 30)
        val t = rememberTimePickerState(start.first, start.second, is24Hour = uses24Hour(appLocale()))
        AlertDialog(
            onDismissRequest = { picking = false }, containerColor = c.card,
            text = { TimePicker(t) },
            confirmButton = {
                TextButton(onClick = { put(buildJsonObject { put("fixed_time", NotifySettingsRules.formatTime(t.hour, t.minute)) }); picking = false }) {
                    Text(stringResource(R.string.ok), color = c.accent)
                }
            },
            dismissButton = { TextButton(onClick = { picking = false }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    }
    keyFor?.let { id ->
        KeyDialog(app, id, providerLabel(id), onDismiss = { keyFor = null }) { got -> keyFor = null; onChanged(got) }
    }
}

@Composable
private fun BriefingToggle(title: String, body: String, on: Boolean, enabled: Boolean = true, onToggle: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(body, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = on, onCheckedChange = onToggle, enabled = enabled, modifier = Modifier.semantics { contentDescription = title },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
}

@Composable
private fun CheckLine(label: String, on: Boolean, onChange: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clickable(role = Role.Checkbox) { onChange(!on) }.pointerHoverIcon(PointerIcon.Hand).padding(vertical = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Checkbox(on, onCheckedChange = null, colors = CheckboxDefaults.colors(checkedColor = c.accent, checkmarkColor = c.onAccent))
        Text(label, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.padding(start = 8.dp).weight(1f))
    }
}

/**
 * "What it reads" (마이 → 알림, its own section at the bottom, shown whether the briefing is on or not: the heads-ups read the same
 * accounts): every connected calendar and mailbox, each with a switch, in two groups; per group "everything except what is off" or
 * "only the ones switched on". An account that could not be read shows one quiet line with what to do. [header]: the section's
 * label, drawn only when there is something to show.
 */
@Composable
fun BriefingSourcesBlock(app: AppContainer, header: @Composable () -> Unit = {}) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var sources by remember { mutableStateOf<BriefingSources?>(null) }
    var checking by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    fun call(block: suspend () -> BriefingSources) {
        scope.launch { try { error = ""; sources = block() } catch (e: ApiError) { error = e.detail } }
    }
    LaunchedEffect(Unit) {
        try { sources = app.sessions.requireApi().get<BriefingSources>("/api/briefing/sources") } catch (_: ApiError) { }
    }
    val s = sources ?: return
    val kinds = BriefingSourcesRules.shown(s)
    if (kinds.isEmpty()) return
    header()
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        MutedText(stringResource(R.string.briefing_sources_about), small = true)
        kinds.forEach { kind ->
            val g = BriefingSourcesRules.group(s, kind)
            val name = stringResource(if (kind == "mail") R.string.briefing_sources_mail else R.string.briefing_sources_calendars)
            Text(name, style = MaterialTheme.typography.titleSmall, color = c.ink, modifier = Modifier.padding(top = 6.dp))
            g.accounts.forEachIndexed { i, a ->
                if (i > 0) HorizontalDivider(color = c.line)
                Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(1.dp)) {
                        // one line each: a long address is cut in its middle, a long name at its end (never wrapped mid-word)
                        Text(TokenText.fit(BriefingSourcesRules.title(a)), style = MaterialTheme.typography.bodyLarge, color = c.ink,
                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                        BriefingSourcesRules.subtitle(a).takeIf { it.isNotBlank() }?.let {
                            Text(it, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                        a.problem?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(top = 2.dp)) }
                    }
                    val title = BriefingSourcesRules.title(a)
                    Switch(
                        checked = a.on, modifier = Modifier.semantics { contentDescription = title },
                        onCheckedChange = { on ->
                            sources = BriefingSourcesRules.flipped(s, kind, a.id, on)
                            call { app.sessions.requireApi().put("/api/briefing/sources", BriefingSourcesRules.toggleBody(kind, a.id, on)) }
                        },
                        colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
                    )
                }
            }
        }
        MutedText(stringResource(R.string.briefing_sources_chat), small = true)
        if (error.isNotEmpty()) ErrorText(error, small = true)
        SecondaryButton(
            stringResource(if (checking) R.string.briefing_sources_checking else R.string.briefing_sources_check),
            enabled = !checking,
            onClick = {
                checking = true
                scope.launch {
                    try { sources = app.sessions.requireApi().post<BriefingSources>("/api/briefing/sources/check") }
                    catch (e: ApiError) { error = e.detail } finally { checking = false }
                }
            },
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

/** The owner's own key for a weather service: pasted, checked by the server with one real request, sealed in the vault. */
@Composable
private fun KeyDialog(app: AppContainer, id: String, name: String, onDismiss: () -> Unit, onSaved: (BriefingSettings) -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var key by remember { mutableStateOf("") }
    var error by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss, containerColor = c.card,
        title = { Text(name, color = c.ink) },
        text = {
            Column(Modifier.widthIn(min = 360.dp, max = 520.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                MutedText(stringResource(if (id == "kma") R.string.briefing_key_help_kma else R.string.briefing_key_help_weatherapi), small = true)
                KeloTextField(key, { key = it; error = "" }, stringResource(R.string.briefing_key_placeholder), password = true, maxLength = 512)
                if (error.isNotEmpty()) ErrorText(error, small = true)
            }
        },
        confirmButton = {
            TextButton(enabled = !busy && BriefingSettingsRules.keyOk(key), onClick = {
                busy = true
                scope.launch {
                    try {
                        onSaved(app.sessions.requireApi().post("/api/briefing/keys/$id", buildJsonObject { put("key", key.trim()) }))
                    } catch (e: ApiError) { error = e.detail } finally { busy = false }
                }
            }) { Text(stringResource(R.string.briefing_key_save), color = c.accent) }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}
