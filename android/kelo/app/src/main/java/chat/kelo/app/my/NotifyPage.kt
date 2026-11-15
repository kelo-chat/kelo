package chat.kelo.app.my

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.pluralStringResource
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.dp
import chat.kelo.app.LocalAppContainer
import chat.kelo.app.R
import chat.kelo.app.agent.clockText
import chat.kelo.app.chat.SelectField
import chat.kelo.app.chat.SelectOption
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.KeloTextField
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** The last pass of the proactive messages. status: done | nothing_new | skipped | failed */
@Serializable
data class ProactivePass(val at: Double? = null, val status: String = "", val reason: String = "", val sent: Int = 0)

/** GET /api/proactive: messages the agent may send by itself (off by default). [today] counts by urgency (보통 / 오늘 / 지금). */
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
    @SerialName("hours_suggestion") val suggestion: HoursSuggestion? = null,
    @SerialName("weekend_suggestion") val weekendSuggestion: HoursSuggestion? = null,
    val learned: Map<String, LearnedWindow?> = emptyMap(),
    @SerialName("holiday_country") val holidayCountry: String? = null,
) {
    /** The hours part, for [ContactHoursRules]. */
    fun hours(): ContactHours = ContactHours(wakeStart, wakeEnd, autoHours, hoursSource, suggestion, weekendStart, weekendEnd, weekendSource,
        weekendSuggestion, learned, holidayCountry = holidayCountry)
}

/** GET /api/checklist: the things to keep an eye on (CHECKLIST.md), looked over with each heads-up pass while those are on. */
@Serializable
data class ChecklistSettings(
    val text: String = "",
    val items: Int = 0,
    val list: List<String> = emptyList(),
)

/** The settings' small rules, apart from the UI. */
object NotifySettingsRules {
    const val MAX_ITEMS = 30
    const val MAX_ITEM = 300
    const val MAX_REGULAR = 10
    const val MAX_TOTAL = 20
    const val MAX_NOW = 10

    /** "07:30" -> 7 to 30, or null when it is not a time. */
    fun parseTime(text: String): Pair<Int, Int>? {
        val m = Regex("^(\\d{1,2}):(\\d{2})$").find(text.trim()) ?: return null
        val h = m.groupValues[1].toInt()
        val mi = m.groupValues[2].toInt()
        return if (h in 0..23 && mi in 0..59) h to mi else null
    }

    /** How often the 새 소식 알림 pass looks, as (in hours, the number): whole hours as hours, else minutes. */
    fun every(minutes: Int): Pair<Boolean, Int> = if (minutes >= 60 && minutes % 60 == 0) true to minutes / 60 else false to minutes.coerceAtLeast(1)

    fun formatTime(hour: Int, minute: Int): String = String.format(java.util.Locale.ROOT, "%02d:%02d", hour, minute)

    /** A cap after +/-: inside its range. The day's total never goes below the 보통 cap (the server counts 보통 inside it). */
    fun caps(regular: Int, total: Int, now: Int): Triple<Int, Int, Int> {
        val r = regular.coerceIn(0, MAX_REGULAR)
        return Triple(r, total.coerceIn(r, MAX_TOTAL), now.coerceIn(0, MAX_NOW))
    }

    /**
     * The one "at most N a day" the page shows, as the server's three caps: the day's total is N, and neither the regular nor the
     * urgent cap may be above it. Returns (daily_regular, daily_total, daily_now).
     */
    fun perDay(n: Int, regular: Int, now: Int): Triple<Int, Int, Int> {
        val t = n.coerceIn(1, MAX_TOTAL)
        return Triple(regular.coerceIn(0, minOf(t, MAX_REGULAR)), t, now.coerceIn(0, minOf(t, MAX_NOW)))
    }

    /** The list after adding [item] (trimmed, one line; nothing when blank, already there or the list is full). */
    fun added(list: List<String>, item: String): List<String> {
        val clean = item.replace(Regex("\\s+"), " ").trim().take(MAX_ITEM)
        return if (clean.isEmpty() || clean in list || list.size >= MAX_ITEMS) list else list + clean
    }
}

/**
 * 마이 → 알림: this phone's notifications (on or off, in the system's settings), follow-ups, and one "Kelo tells you first" (the
 * agent may write first: on or off, at most N times a day, only between the hours set). Its optional "things to keep an eye on" list
 * (CHECKLIST.md) is looked over in the same pass; it has no switch or pace of its own.
 */
@Composable
fun NotifyPage(onBack: () -> Unit, onDevices: () -> Unit) {
    val container = LocalAppContainer.current
    val sessions = container.sessions
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var proactive by remember { mutableStateOf<ProactiveSettings?>(null) }
    var checklist by remember { mutableStateOf<ChecklistSettings?>(null) }
    var followups by remember { mutableStateOf<chat.kelo.app.followup.FollowupSettings?>(null) }
    var followupsMissing by remember { mutableStateOf(false) }
    var proactiveMissing by remember { mutableStateOf(false) }
    var checklistMissing by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var pushHelp by remember { mutableStateOf(false) }
    fun toast(text: String) = android.widget.Toast.makeText(context, text, android.widget.Toast.LENGTH_SHORT).show()
    LaunchedEffect(Unit) {
        val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        try { proactive = api.get("/api/proactive") } catch (e: ApiError) { if (e.status == 404) proactiveMissing = true else error = e.detail }
        try { checklist = api.get("/api/checklist") } catch (e: ApiError) { if (e.status == 404) checklistMissing = true else error = e.detail }
        try {
            followups = chat.kelo.app.followup.FollowupCalls(api).settings()
            followupsMissing = followups == null
        } catch (e: ApiError) { error = e.detail }
    }
    fun putFollowups(body: JsonObject) {
        scope.launch {
            try { followups = chat.kelo.app.followup.FollowupCalls(sessions.requireApi()).putSettings(body) } catch (e: ApiError) { toast(e.detail) }
        }
    }
    fun putProactive(body: JsonObject) {
        scope.launch {
            try { proactive = sessions.requireApi().put("/api/proactive", body) } catch (e: ApiError) { toast(e.detail) }
        }
    }
    fun putChecklist(body: JsonObject) {
        scope.launch {
            try { checklist = sessions.requireApi().put("/api/checklist", body) } catch (e: ApiError) { toast(e.detail) }
        }
    }

    var briefing by remember { mutableStateOf<BriefingSettings?>(null) }
    var briefingMissing by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@LaunchedEffect
        try { briefing = api.get("/api/briefing") } catch (e: ApiError) { if (e.status == 404) briefingMissing = true }
    }
    fun postHours(path: String) {
        scope.launch {
            try { proactive = sessions.requireApi().post(path) } catch (e: ApiError) { toast(e.detail) }
        }
    }

    SubPage(stringResource(R.string.notify_my_row), onBack) {
        SectionLabel(stringResource(R.string.notify_page_phone))
        MenuGroup {
            row {
                val on = chat.kelo.app.notify.rememberNotificationsOn()
                val enable = chat.kelo.app.notify.rememberNotificationEnabler()
                val base = sessions.api.value?.baseUrl
                val needsDevice = base != null && remember(base) { container.notifyPrefs.pushNeedsDevice(base) }
                MenuRow(
                    KeloIcons.Bell, stringResource(R.string.notify_page_phone_row),
                    {
                        when {
                            on && needsDevice -> pushHelp = true
                            on -> chat.kelo.app.notify.openNotificationSettings(context)
                            else -> enable()
                        }
                    },
                    value = stringResource(
                        when {
                            on && needsDevice -> R.string.notify_state_needs_device
                            on -> R.string.notify_state_on
                            else -> R.string.notify_state_off
                        },
                    ),
                    valueColor = if (on && !needsDevice) null else c.danger,
                )
            }
        }
        if (error.isNotEmpty()) ErrorText(error, small = true)

        SectionLabel(stringResource(R.string.followup_settings_title))
        val f = followups
        when {
            followupsMissing -> MutedText(stringResource(R.string.notify_page_unavailable), small = true)
            f == null -> SkeletonRows(1)
            else -> FollowupBlock(f, onPut = ::putFollowups)
        }

        SectionLabel(stringResource(R.string.hours_section))
        val hp = proactive
        when {
            proactiveMissing -> MutedText(stringResource(R.string.notify_page_unavailable), small = true)
            hp == null -> SkeletonRows(2)
            else -> HoursBlock(hp, onPut = ::putProactive, onPost = ::postHours)
        }

        if (!briefingMissing) {
            SectionLabel(stringResource(R.string.briefing_title))
            val b = briefing
            if (b == null) SkeletonRows(2) else BriefingBlock(b, onChanged = { briefing = it })
        }

        SectionLabel(stringResource(R.string.heads_section))
        val p = proactive
        when {
            proactiveMissing -> MutedText(stringResource(R.string.notify_page_unavailable), small = true)
            p == null -> SkeletonRows(2)
            else -> HeadsUpBlock(p, onPut = ::putProactive)
        }
        if (p?.enabled == true && !checklistMissing) {
            val k = checklist
            if (k == null) SkeletonRows(1)
            else WatchList(k.list, onChange = { items -> putChecklist(buildJsonObject { put("items", JsonArray(items.map { JsonPrimitive(it) })) }) })
        }

        // which calendars and mailboxes the briefing and the heads-ups read (one choice for both; BriefingSources.kt)
        BriefingSourcesBlock(header = { SectionLabel(stringResource(R.string.briefing_sources_title)) })
    }
    if (pushHelp) {
        ConfirmDialog(
            text = stringResource(R.string.notify_needs_device_body), yes = stringResource(R.string.notify_needs_device_go),
            onDismiss = { pushHelp = false }, onYes = onDevices, danger = false,
        )
    }
}

@Composable
private fun ToggleLine(title: String, body: String, on: Boolean, onToggle: (Boolean) -> Unit) {
    val c = Kelo.colors
    val haptics = rememberHaptics()
    Row(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(body, style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = on, onCheckedChange = { haptics.tick(); onToggle(it) }, modifier = Modifier.semantics { contentDescription = title },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
}

/**
 * 새 소식 알림 (proactive.py): every few hours (the server's own every_minutes) it looks for new things the owner should know; on or
 * off, and when on at most how many times a day (the hours it may write in: [HoursBlock]). The about text says how often, and that
 * what waits for the owner is asked again by the follow-ups (다시 묻기) whatever this is set to.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HeadsUpBlock(p: ProactiveSettings, onPut: (JsonObject) -> Unit) {
    val c = Kelo.colors
    val about = stringResource(R.string.heads_about, everyText(p.everyMinutes), stringResource(R.string.followup_settings_title))
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
 * 알림 시간: when Kelo may write, one row for weekdays and one for weekends and public holidays (an older server: weekdays only), each
 * with its times, where they came from, the window learned from the owner's use when it differs ("배운 시간 … 적용"), and an open
 * suggestion for that day type. Hours set here by hand are never changed by Kelo; "adjust automatically" applies learned windows to
 * the hours that are not hand-set.
 */
@OptIn(ExperimentalMaterial3Api::class)
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
                TimesRow {
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
                    androidx.compose.material3.TextButton(onClick = { onPut(buildJsonObject { put("weekend_start", ""); put("weekend_end", "") }) },
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
        val t = rememberTimePickerState(start.first, start.second, is24Hour = chat.kelo.app.agent.uses24Hour())
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

/**
 * The "from" and "until" buttons: two equal halves of one row when both labels fit on one line there, else one under the other,
 * each full width ([ContactHoursRules.timesSideBySide]), so a label never wraps inside its pill (320dp, German, "다음 날 …까지").
 */
@Composable
private fun TimesRow(content: @Composable () -> Unit) {
    Layout(content) { measurables, constraints ->
        val gap = 10.dp.roundToPx()
        val width = constraints.maxWidth
        val needs = measurables.map { it.maxIntrinsicWidth(Constraints.Infinity) }
        val side = measurables.size == 2 && constraints.hasBoundedWidth && ContactHoursRules.timesSideBySide(needs[0], needs[1], width, gap)
        val each = if (side) (width - gap) / 2 else width
        val placeables = measurables.map { it.measure(Constraints.fixedWidth(each.coerceAtLeast(0))) }
        val height = if (side) placeables.maxOf { it.height } else placeables.sumOf { it.height } + gap * (placeables.size - 1).coerceAtLeast(0)
        layout(width, height) {
            var x = 0
            var y = 0
            placeables.forEach { pl ->
                if (side) { pl.placeRelative(x, (height - pl.height) / 2); x += each + gap }
                else { pl.placeRelative(0, y); y += pl.height + gap }
            }
        }
    }
}

/** 챙겨볼 것: optional things Kelo looks at in the same pass (CHECKLIST.md's list lines); add and remove, nothing else to set. */
@Composable
private fun WatchList(items: List<String>, onChange: (List<String>) -> Unit) {
    val c = Kelo.colors
    var draft by rememberSaveable { mutableStateOf("") }
    Column(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(stringResource(R.string.heads_list_title), style = MaterialTheme.typography.labelLarge, color = c.muted)
        MutedText(stringResource(R.string.heads_list_about), small = true)
        if (items.isNotEmpty()) {
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).border(1.dp, c.line, RoundedCornerShape(12.dp)),
            ) {
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
            chat.kelo.app.ui.components.FieldWithAction(
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

/** 다시 묻기: on or off, and when the first and the second ask come. */
@Composable
private fun FollowupBlock(f: chat.kelo.app.followup.FollowupSettings, onPut: (JsonObject) -> Unit) {
    val rules = chat.kelo.app.followup.FollowupRules
    ToggleLine(stringResource(R.string.followup_settings_switch), stringResource(R.string.followup_settings_about), f.enabled) { on ->
        onPut(buildJsonObject { put("enabled", on) })
    }
    if (!f.enabled) return
    Column(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(stringResource(R.string.followup_settings_first), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
        SelectField(
            stringResource(R.string.followup_settings_first),
            rules.choices(rules.FIRST_CHOICES, f.firstMinutes).map { SelectOption(it.toString(), chat.kelo.app.followup.laterText(it)) },
            f.firstMinutes.toString(),
        ) { v ->
            v.toIntOrNull()?.let { first ->
                val (a, b) = rules.delays(first, f.secondMinutes)
                onPut(buildJsonObject { put("first_delay_min", a); if (b != f.secondMinutes) put("second_delay_min", b) })
            }
        }
        Text(stringResource(R.string.followup_settings_second), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
        SelectField(
            stringResource(R.string.followup_settings_second),
            rules.choices(rules.SECOND_CHOICES, f.secondMinutes).filter { it >= f.firstMinutes }
                .map { SelectOption(it.toString(), chat.kelo.app.followup.laterText(it)) },
            f.secondMinutes.toString(),
        ) { v -> v.toIntOrNull()?.let { second -> onPut(buildJsonObject { put("second_delay_min", rules.delays(f.firstMinutes, second).second) }) } }
        MutedText(stringResource(R.string.followup_settings_hold_note), small = true)
    }
}

/** "2시간마다" / "Every 2 hours", or minutes when it is not whole hours. */
@Composable
private fun everyText(minutes: Int): String = NotifySettingsRules.every(minutes).let { (hours, n) ->
    if (hours) pluralStringResource(R.plurals.agent_repeat_every_hours, n, n) else pluralStringResource(R.plurals.work_every_minutes, n, n)
}

@Composable
private fun timeWords(text: String): String = NotifySettingsRules.parseTime(text)?.let { (h, m) -> clockText(h, m) } ?: text

/** A label with − n +. */
@Composable
private fun CapStepper(label: String, value: Int, valueText: String = value.toString(), min: Int = 0, max: Int = NotifySettingsRules.MAX_TOTAL, onChange: (Int) -> Unit) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
        IconButton(onClick = { onChange(value - 1) }, enabled = value > min) { Icon(KeloIcons.Minus, contentDescription = stringResource(R.string.proactive_less), tint = c.ink) }
        Text(valueText, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold, color = c.ink)
        IconButton(onClick = { onChange(value + 1) }, enabled = value < max) { Icon(KeloIcons.Plus, contentDescription = stringResource(R.string.proactive_more), tint = c.ink) }
    }
}
