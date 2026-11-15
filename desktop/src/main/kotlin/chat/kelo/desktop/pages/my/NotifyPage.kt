package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberTimePickerState
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
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.core.ApiError
import chat.kelo.app.followup.FollowupCalls
import chat.kelo.app.followup.FollowupRules
import chat.kelo.app.followup.FollowupSettings
import chat.kelo.app.my.ContactHoursRules
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

// The models and rules of android/kelo .../my/NotifyPage.kt (that file is an Android screen). Keep in step with it.

@Serializable
data class ProactivePass(val at: Double? = null, val status: String = "", val reason: String = "", val sent: Int = 0)

/** GET /api/proactive: messages the agent may send by itself (off by default). */
@Serializable
data class ProactiveSettings(
    val enabled: Boolean = false,
    @SerialName("wake_start") val wakeStart: String = "07:00",
    @SerialName("wake_end") val wakeEnd: String = "22:00",
    @SerialName("every_minutes") val everyMinutes: Int = 120,
    @SerialName("daily_regular") val dailyRegular: Int = 2,
    @SerialName("daily_total") val dailyTotal: Int = 3,
    @SerialName("daily_now") val dailyNow: Int = 3,
    val today: Map<String, Int> = emptyMap(),
    @SerialName("last_pass") val lastPass: ProactivePass? = null,
    val awake: Boolean = false,
    /** Follow the hours the owner really uses Kelo without asking (off by default). */
    @SerialName("auto_hours") val autoHours: Boolean = false,
    /** default | manual | learned: "learned" when the hours came from a suggestion (마이 → 기록) or from [autoHours]. */
    @SerialName("hours_source") val hoursSource: String = "default",
    /** Weekend and holiday hours in effect ("default": they follow the weekdays); an older server sends none */
    @SerialName("weekend_start") val weekendStart: String = "",
    @SerialName("weekend_end") val weekendEnd: String = "",
    @SerialName("weekend_source") val weekendSource: String = "default",
    @SerialName("hours_suggestion") val suggestion: chat.kelo.app.my.HoursSuggestion? = null,
    @SerialName("weekend_suggestion") val weekendSuggestion: chat.kelo.app.my.HoursSuggestion? = null,
    val learned: Map<String, chat.kelo.app.my.LearnedWindow?> = emptyMap(),
    @SerialName("holiday_country") val holidayCountry: String? = null,
) {
    /** The hours part, for [ContactHoursRules]. */
    fun hours(): chat.kelo.app.my.ContactHours = chat.kelo.app.my.ContactHours(wakeStart, wakeEnd, autoHours, hoursSource, suggestion, weekendStart, weekendEnd,
        weekendSource, weekendSuggestion, learned, holidayCountry = holidayCountry)
}

/** GET /api/checklist: the things to keep an eye on, looked over with each heads-up pass while those are on. */
@Serializable
data class ChecklistSettings(val text: String = "", val items: Int = 0, val list: List<String> = emptyList())

object NotifySettingsRules {
    const val MAX_ITEMS = 30
    const val MAX_ITEM = 300
    const val MAX_REGULAR = 10
    const val MAX_TOTAL = 20
    const val MAX_NOW = 10

    fun parseTime(text: String): Pair<Int, Int>? {
        val m = Regex("^(\\d{1,2}):(\\d{2})$").find(text.trim()) ?: return null
        val h = m.groupValues[1].toInt()
        val mi = m.groupValues[2].toInt()
        return if (h in 0..23 && mi in 0..59) h to mi else null
    }

    /** How often the 새 소식 알림 pass looks, as (in hours, the number): whole hours as hours, else minutes. */
    fun every(minutes: Int): Pair<Boolean, Int> = if (minutes >= 60 && minutes % 60 == 0) true to minutes / 60 else false to minutes.coerceAtLeast(1)

    fun formatTime(hour: Int, minute: Int): String = String.format(java.util.Locale.ROOT, "%02d:%02d", hour, minute)

    fun perDay(n: Int, regular: Int, now: Int): Triple<Int, Int, Int> {
        val t = n.coerceIn(1, MAX_TOTAL)
        return Triple(regular.coerceIn(0, minOf(t, MAX_REGULAR)), t, now.coerceIn(0, minOf(t, MAX_NOW)))
    }

    fun added(list: List<String>, item: String): List<String> {
        val clean = item.replace(Regex("\\s+"), " ").trim().take(MAX_ITEM)
        return if (clean.isEmpty() || clean in list || list.size >= MAX_ITEMS) list else list + clean
    }
}

/**
 * 마이 → 알림: this device's notifications (the desktop's own switch; a phone's push permission does not apply here), asking again
 * (follow-ups), and "Kelo tells you first" with its hours and how often, plus the optional things-to-keep-an-eye-on list.
 */
@Composable
fun NotifyPage(app: AppContainer, onBack: () -> Unit) {
    val sessions = app.sessions
    val scope = rememberCoroutineScope()
    var proactive by remember { mutableStateOf<ProactiveSettings?>(null) }
    var checklist by remember { mutableStateOf<ChecklistSettings?>(null) }
    var followups by remember { mutableStateOf<FollowupSettings?>(null) }
    var followupsMissing by remember { mutableStateOf(false) }
    var proactiveMissing by remember { mutableStateOf(false) }
    var checklistMissing by remember { mutableStateOf(false) }
    var briefing by remember { mutableStateOf<chat.kelo.app.my.BriefingSettings?>(null) }
    var briefingMissing by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        try { proactive = api.get("/api/proactive") } catch (e: ApiError) { if (e.status == 404) proactiveMissing = true else error = e.detail }
        try { checklist = api.get("/api/checklist") } catch (e: ApiError) { if (e.status == 404) checklistMissing = true else error = e.detail }
        // an older server has no morning briefing: its section is left out
        try { briefing = api.get("/api/briefing") } catch (e: ApiError) { briefingMissing = true; if (e.status != 404) error = e.detail }
        try {
            followups = FollowupCalls(api).settings()
            followupsMissing = followups == null
        } catch (e: ApiError) { error = e.detail }
    }
    fun putFollowups(body: JsonObject) = scope.launch {
        try { followups = FollowupCalls(sessions.requireApi()).putSettings(body) } catch (e: ApiError) { error = e.detail }
    }
    fun putProactive(body: JsonObject) = scope.launch {
        try { proactive = sessions.requireApi().put("/api/proactive", body) } catch (e: ApiError) { error = e.detail }
    }
    /** A learned window applied, or a suggestion answered: the page reads the hours again */
    fun postHours(path: String) = scope.launch {
        try {
            val api = sessions.requireApi()
            api.post<kotlinx.serialization.json.JsonElement>(path)
            proactive = api.get("/api/proactive")
        } catch (e: ApiError) { error = e.detail }
    }
    fun putChecklist(body: JsonObject) = scope.launch {
        try { checklist = sessions.requireApi().put("/api/checklist", body) } catch (e: ApiError) { error = e.detail }
    }

    SubPage(stringResource(R.string.notify_my_row), onBack) {
        SectionLabel(stringResource(R.string.desktop_section))
        val notify by app.settings.notifications.collectAsState()
        ToggleLine(stringResource(R.string.desktop_notifications), stringResource(R.string.desktop_notifications_hint), notify, app.settings::setNotifications)
        if (error.isNotEmpty()) ErrorText(error, small = true)

        SectionLabel(stringResource(R.string.followup_settings_title))
        val f = followups
        when {
            followupsMissing -> MutedText(stringResource(R.string.notify_page_unavailable), small = true)
            f == null -> MutedText(stringResource(R.string.loading), small = true)
            else -> FollowupBlock(f, onPut = { putFollowups(it) })
        }

        SectionLabel(stringResource(R.string.hours_section))
        val hp = proactive
        when {
            proactiveMissing -> MutedText(stringResource(R.string.notify_page_unavailable), small = true)
            hp == null -> MutedText(stringResource(R.string.loading), small = true)
            else -> HoursBlock(hp, onPut = { putProactive(it) }, onPost = { postHours(it) })
        }

        if (!briefingMissing) {
            SectionLabel(stringResource(R.string.briefing_title))
            val b = briefing
            if (b == null) MutedText(stringResource(R.string.loading), small = true) else BriefingBlock(app, b, onChanged = { briefing = it })
        }

        SectionLabel(stringResource(R.string.heads_section))
        val p = proactive
        when {
            proactiveMissing -> MutedText(stringResource(R.string.notify_page_unavailable), small = true)
            p == null -> MutedText(stringResource(R.string.loading), small = true)
            else -> HeadsUpBlock(p, onPut = { putProactive(it) })
        }
        if (p?.enabled == true && !checklistMissing) {
            val k = checklist
            if (k == null) MutedText(stringResource(R.string.loading), small = true)
            else WatchList(k.list, onChange = { items -> putChecklist(buildJsonObject { put("items", JsonArray(items.map { JsonPrimitive(it) })) }) })
        }

        // which calendars and mailboxes the briefing and the heads-ups read (one choice for both), whether the briefing is on or not
        BriefingSourcesBlock(app, header = { SectionLabel(stringResource(R.string.briefing_sources_title)) })
    }
}

@Composable
internal fun ToggleLine(title: String, body: String, on: Boolean, onToggle: (Boolean) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(body, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = on, onCheckedChange = onToggle, modifier = Modifier.semantics { contentDescription = title },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
}

/**
 * 새 소식 알림 (proactive.py): every few hours (the server's own every_minutes) it looks for new things the owner should know; on or
 * off, and when on at most how many times a day (the hours it may write in: [HoursBlock]). The about text says how often, and that
 * what waits for the owner is asked again by the follow-ups (다시 묻기) whatever this is set to.
 */
@Composable
private fun HeadsUpBlock(p: ProactiveSettings, onPut: (JsonObject) -> Unit) {
    val c = Kelo.colors
    val every = NotifySettingsRules.every(p.everyMinutes).let { (hours, n) ->
        if (hours) pluralStringResource(R.plurals.agent_repeat_every_hours, n, n) else pluralStringResource(R.plurals.work_every_minutes, n, n)
    }
    val about = stringResource(R.string.heads_about, every, stringResource(R.string.followup_settings_title))
    ToggleLine(stringResource(R.string.heads_switch), about, p.enabled) { on -> onPut(buildJsonObject { put("enabled", on) }) }
    if (!p.enabled) return
    Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        CapStepper(stringResource(R.string.heads_per_day), p.dailyTotal, pluralStringResource(R.plurals.heads_times, p.dailyTotal, p.dailyTotal), min = 1) { v ->
            val (r, t, n) = NotifySettingsRules.perDay(v, p.dailyRegular, p.dailyNow)
            onPut(buildJsonObject { put("daily_regular", r); put("daily_total", t); put("daily_now", n) })
        }
        val sent = p.today.values.sum()
        if (sent > 0) Text(pluralStringResource(R.plurals.heads_sent_today, sent, sent), style = MaterialTheme.typography.bodySmall, color = c.muted)
    }
}

/**
 * 알림 시간 (android/kelo .../my/NotifyPage.kt HoursBlock): when Kelo may write, one row for weekdays and one for weekends and public
 * holidays (an older server: weekdays only), each with its times, where they came from, the window learned from the owner's use
 * when it differs ("배운 시간 … 적용"), and an open suggestion for that day type. Hours set here by hand are never changed by Kelo;
 * "adjust automatically" applies learned windows to the hours that are not hand-set.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
private fun HoursBlock(p: ProactiveSettings, onPut: (JsonObject) -> Unit, onPost: (String) -> Unit) {
    val c = Kelo.colors
    val h = p.hours()
    var picking by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf<Pair<String, Boolean>?>(null) }
    val kinds = if (h.holidayCountry != null) listOf(ContactHoursRules.WEEKDAY, ContactHoursRules.WEEKEND) else listOf(ContactHoursRules.WEEKDAY)
    Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        MutedText(stringResource(R.string.hours_about), small = true)
        kinds.forEach { kind ->
            val (start, end, source) = ContactHoursRules.window(h, kind)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (kinds.size > 1) {
                    Text(stringResource(if (kind == ContactHoursRules.WEEKEND) R.string.hours_weekend else R.string.hours_weekday),
                        style = MaterialTheme.typography.labelLarge, color = c.muted)
                }
                FlowRow(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    SecondaryButton(stringResource(R.string.proactive_from, timeWords(start)), onClick = { picking = ContactHoursRules.field(kind, true) })
                    val until = if (ContactHoursRules.pastMidnight(start, end)) R.string.proactive_until_next_day else R.string.proactive_until
                    SecondaryButton(stringResource(until, timeWords(end)), onClick = { picking = ContactHoursRules.field(kind, false) })
                }
                when {
                    kind == ContactHoursRules.WEEKEND && source == "default" ->
                        Text(stringResource(R.string.hours_weekend_follows), style = MaterialTheme.typography.bodySmall, color = c.muted)
                    ContactHoursRules.learned(source) -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Icon(KeloIcons.Check, contentDescription = null, tint = c.accent, modifier = Modifier.size(16.dp))
                        Text(stringResource(R.string.hours_learned), style = MaterialTheme.typography.bodySmall, color = c.muted)
                    }
                    source == "manual" -> Text(stringResource(R.string.hours_manual), style = MaterialTheme.typography.bodySmall, color = c.muted)
                }
                if (kind == ContactHoursRules.WEEKEND && source == "manual") {
                    TextButton(onClick = { onPut(buildJsonObject { put("weekend_start", ""); put("weekend_end", "") }) },
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp)) {
                        Text(stringResource(R.string.hours_weekend_reset), style = MaterialTheme.typography.bodySmall, color = c.accent)
                    }
                }
                ContactHoursRules.learnedFor(h, kind)?.let { l ->
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(stringResource(R.string.hours_learned_value, hoursSpanText(l.wakeStart, l.wakeEnd)), style = MaterialTheme.typography.bodySmall,
                            color = c.ink, modifier = Modifier.weight(1f))
                        SecondaryButton(stringResource(R.string.hours_learned_apply), onClick = { onPost(ContactHoursRules.applyPath(kind)) })
                    }
                }
                val sug = (if (kind == ContactHoursRules.WEEKEND) h.weekendSuggestion?.copy(dayType = kind) else h.suggestion)?.takeIf { ContactHoursRules.shown(it) }
                sug?.let { s ->
                    HoursSuggestionCard(s, done?.takeIf { it.first == s.id }?.second) { accept ->
                        done = s.id to accept
                        onPost(if (accept) ContactHoursRules.acceptPath(s.id) else ContactHoursRules.keepPath(s.id))
                    }
                }
            }
        }
    }
    ToggleLine(stringResource(R.string.hours_auto_switch), stringResource(R.string.hours_auto_about_manual), p.autoHours) { on ->
        onPut(buildJsonObject { put("auto_hours", on) })
    }
    picking?.let { field ->
        val cur = when (field) {
            "wake_start" -> h.wakeStart
            "wake_end" -> h.wakeEnd
            "weekend_start" -> ContactHoursRules.window(h, ContactHoursRules.WEEKEND).first
            else -> ContactHoursRules.window(h, ContactHoursRules.WEEKEND).second
        }
        val start = NotifySettingsRules.parseTime(cur) ?: (7 to 0)
        val t = rememberTimePickerState(start.first, start.second, is24Hour = uses24Hour(appLocale()))
        AlertDialog(
            onDismissRequest = { picking = null }, containerColor = c.card,
            text = { TimePicker(t) },
            confirmButton = {
                TextButton(onClick = { onPut(buildJsonObject { put(field, NotifySettingsRules.formatTime(t.hour, t.minute)) }); picking = null }) {
                    Text(stringResource(R.string.ok), color = c.accent)
                }
            },
            dismissButton = { TextButton(onClick = { picking = null }) { Text(stringResource(R.string.cancel), color = c.ink) } },
        )
    }
}

/** Whether the language's clock is 24-hour (its short time pattern has no am/pm). */
internal fun uses24Hour(locale: java.util.Locale): Boolean =
    java.time.format.DateTimeFormatterBuilder.getLocalizedDateTimePattern(null, java.time.format.FormatStyle.SHORT, java.time.chrono.IsoChronology.INSTANCE, locale)
        .let { 'a' !in it && 'B' !in it }

@Composable
private fun WatchList(items: List<String>, onChange: (List<String>) -> Unit) {
    val c = Kelo.colors
    var draft by remember { mutableStateOf("") }
    Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.heads_list_title), style = MaterialTheme.typography.labelLarge, color = c.muted)
        MutedText(stringResource(R.string.heads_list_about), small = true)
        if (items.isNotEmpty()) {
            Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).border(1.dp, c.line, RoundedCornerShape(12.dp))) {
                items.forEachIndexed { i, item ->
                    if (i > 0) HorizontalDivider(color = c.line)
                    Row(Modifier.fillMaxWidth().padding(start = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(item, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f).padding(vertical = 10.dp))
                        IconButton(onClick = { onChange(items - item) }) {
                            Icon(KeloIcons.Close, contentDescription = stringResource(R.string.heads_list_remove, item), tint = c.muted)
                        }
                    }
                }
            }
        }
        if (items.size < NotifySettingsRules.MAX_ITEMS) {
            chat.kelo.desktop.ui.FieldWithAction(
                field = { m -> KeloTextField(draft, { draft = it }, stringResource(R.string.heads_list_hint), modifier = m, maxLength = NotifySettingsRules.MAX_ITEM) },
                action = {
                    SecondaryButton(stringResource(R.string.heads_list_add), enabled = draft.isNotBlank(), onClick = {
                        val next = NotifySettingsRules.added(items, draft)
                        if (next != items) onChange(next)
                        draft = ""
                    })
                },
            )
        }
    }
}

@Composable
private fun FollowupBlock(f: FollowupSettings, onPut: (JsonObject) -> Unit) {
    val rules = FollowupRules
    ToggleLine(stringResource(R.string.followup_settings_switch), stringResource(R.string.followup_settings_about), f.enabled) { on ->
        onPut(buildJsonObject { put("enabled", on) })
    }
    if (!f.enabled) return
    Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.followup_settings_first), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
        Pick(rules.choices(rules.FIRST_CHOICES, f.firstMinutes).map { it to laterText(it) }, f.firstMinutes) { first ->
            val (a, b) = rules.delays(first, f.secondMinutes)
            onPut(buildJsonObject { put("first_delay_min", a); if (b != f.secondMinutes) put("second_delay_min", b) })
        }
        Text(stringResource(R.string.followup_settings_second), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
        Pick(rules.choices(rules.SECOND_CHOICES, f.secondMinutes).filter { it >= f.firstMinutes }.map { it to laterText(it) }, f.secondMinutes) { second ->
            onPut(buildJsonObject { put("second_delay_min", rules.delays(f.firstMinutes, second).second) })
        }
        MutedText(stringResource(R.string.followup_settings_hold_note), small = true)
    }
}

/** A field that opens a short list to pick from. */
@Composable
private fun Pick(options: List<Pair<Int, String>>, selected: Int, onPick: (Int) -> Unit) {
    val c = Kelo.colors
    var open by remember { mutableStateOf(false) }
    Box(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 44.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, c.line, RoundedCornerShape(12.dp))
                .background(c.card).clickable(role = Role.DropdownList) { open = true }.padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(options.firstOrNull { it.first == selected }?.second.orEmpty(), color = c.ink, modifier = Modifier.weight(1f))
            Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(18.dp).rotate(if (androidx.compose.ui.platform.LocalLayoutDirection.current == androidx.compose.ui.unit.LayoutDirection.Rtl) -90f else 90f))
        }
        DropdownMenu(open, onDismissRequest = { open = false }, modifier = Modifier.widthIn(min = 200.dp).background(c.card)) {
            options.forEach { (v, label) ->
                DropdownMenuItem(text = { Text(label, color = c.ink, fontWeight = if (v == selected) FontWeight.SemiBold else FontWeight.Normal) },
                    onClick = { open = false; onPick(v) })
            }
        }
    }
}

@Composable
internal fun timeWords(text: String): String = NotifySettingsRules.parseTime(text)?.let { (h, m) -> clockText(h, m) } ?: text

@Composable
private fun CapStepper(label: String, value: Int, valueText: String, min: Int = 0, max: Int = NotifySettingsRules.MAX_TOTAL, onChange: (Int) -> Unit) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
        IconButton(onClick = { onChange(value - 1) }, enabled = value > min) { Icon(KeloIcons.Minus, contentDescription = stringResource(R.string.proactive_less), tint = c.ink) }
        Text(valueText, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, color = c.ink)
        IconButton(onClick = { onChange(value + 1) }, enabled = value < max) { Icon(KeloIcons.Plus, contentDescription = stringResource(R.string.proactive_more), tint = c.ink) }
    }
}
