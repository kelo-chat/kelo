package chat.kelo.desktop.pages.my

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.DayGroups
import chat.kelo.app.agent.JournalEntry
import chat.kelo.app.agent.JournalGap
import chat.kelo.app.agent.JournalMonth
import chat.kelo.app.agent.LearnedRule
import chat.kelo.app.agent.Lookback
import chat.kelo.app.agent.LookbackRules
import chat.kelo.app.agent.LookbackRun
import chat.kelo.app.agent.MemoryArea
import chat.kelo.app.agent.MemoryChanges
import chat.kelo.app.agent.MemoryPass
import chat.kelo.app.agent.MemoryPassRules
import chat.kelo.app.agent.RunWhen
import chat.kelo.app.agent.SkillProposal
import chat.kelo.app.agent.SkillProposals
import chat.kelo.app.agent.SkillRules
import chat.kelo.app.core.ApiError
import chat.kelo.app.my.ContactHours
import chat.kelo.app.my.ContactHoursRules
import chat.kelo.app.my.HoursSuggestion
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.chat.MarkdownView
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.work.SkillCalls
import chat.kelo.desktop.pages.work.SkillProposalsBlock
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.SectionLabel
import chat.kelo.desktop.ui.SubPage
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.YearMonth
import java.time.temporal.TemporalAdjusters

// The journal's rules of android/kelo .../agent/JournalParts.kt and agent/DigestRules.kt (those files hold Android UI too, or need it).

/** The journal's days, newest first: each with its entries, and whether it still waits to be written (or could not be). */
object JournalDays {
    data class Day(val date: String, val entries: List<JournalEntry>, val gap: JournalGap?)

    fun days(entries: List<JournalEntry>, gaps: List<JournalGap>): List<Day> {
        val byDate = entries.groupBy { it.date }
        val gapBy = gaps.associateBy { it.date }
        return (byDate.keys + gapBy.keys).distinct().sortedDescending().map { d -> Day(d, byDate[d].orEmpty(), gapBy[d]) }
    }
}

/** One week of the journal, as the 기록 page shows it. */
data class WeekDigest(val start: LocalDate, val entries: List<JournalEntry> = emptyList(), val gaps: List<JournalGap> = emptyList()) {
    val end: LocalDate get() = start.plusDays(6)
    val days: List<JournalDays.Day> get() = JournalDays.days(entries, gaps)
}

/** Weeks run Monday to Sunday; the journal is fetched by month, so a week may need two. */
object DigestRules {
    fun startOf(date: LocalDate): LocalDate = date.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY))
    fun months(start: LocalDate): List<YearMonth> = listOf(YearMonth.from(start), YearMonth.from(start.plusDays(6))).distinct()
    fun week(start: LocalDate, entries: List<JournalEntry>, gaps: List<JournalGap>): WeekDigest {
        val from = start.toString()
        val to = start.plusDays(6).toString()
        fun inside(date: String) = date.take(10) in from..to
        return WeekDigest(start, entries.filter { inside(it.date) }.distinctBy { it.id }.sortedByDescending { it.date }, gaps.filter { inside(it.date) }.distinctBy { it.date })
    }
    fun canGoNext(start: LocalDate, today: LocalDate): Boolean = start < startOf(today)
    fun perDay(d: WeekDigest): List<Int> = (0..6).map { i -> val day = d.start.plusDays(i.toLong()).toString(); d.entries.count { it.date.take(10) == day } }
}

/**
 * 마이 → 기록 (android/kelo .../my/RecordsPage.kt): one week at a time. A strip of the seven days, then each day with the journal
 * entries the agent wrote and that night's look-back note; below, the nightly look-back's switch and what it learned.
 */
@Composable
fun RecordsPage(app: AppContainer, onBack: () -> Unit) {
    val c = Kelo.colors
    val scope = rememberCoroutineScope()
    val strings = LocalStrings.current
    val today = remember { LocalDate.now() }
    var start by remember { mutableStateOf(DigestRules.startOf(today)) }
    var week by remember { mutableStateOf<WeekDigest?>(null) }
    var weekFailed by remember { mutableStateOf(false) }
    var look by remember { mutableStateOf<Lookback?>(null) }
    var lookFailed by remember { mutableStateOf(false) }
    var rebuilding by remember { mutableStateOf(setOf<String>()) }
    var expanded by remember { mutableStateOf(setOf<Long>()) }
    var askRebuild by remember { mutableStateOf<String?>(null) }
    var askDelete by remember { mutableStateOf<JournalEntry?>(null) }
    var message by remember { mutableStateOf("") }
    var showMemory by remember { mutableStateOf(false) }
    var memChanges by remember { mutableStateOf<MemoryChanges?>(null) }
    var memFailed by remember { mutableStateOf(false) }
    var undoing by remember { mutableStateOf(false) }
    // learned message hours waiting for an answer (GET /api/proactive "hours_suggestion"); [hoursDone] = the choice just made
    var hours by remember { mutableStateOf<HoursSuggestion?>(null) }
    var hoursDone by remember { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(Unit) {
        // the weekday suggestion first, else the weekend one (ContactHoursRules.open, as on the phone)
        hours = ContactHoursRules.open(runCatching { app.sessions.requireApi().get<ContactHours>("/api/proactive") }.getOrNull())
    }
    LaunchedEffect(hoursDone) {
        if (hoursDone != null) {
            kotlinx.coroutines.delay(ContactHoursRules.DONE_MILLIS)
            hours = null
            hoursDone = null
        }
    }
    fun answerHours(h: HoursSuggestion, accept: Boolean) {
        hoursDone = accept
        scope.launch {
            try {
                app.sessions.requireApi().post<ContactHours>(if (accept) ContactHoursRules.acceptPath(h.id) else ContactHoursRules.keepPath(h.id))
            } catch (e: ApiError) {
                // 404: already answered elsewhere or replaced; the next visit shows what is open now
                if (e.status != 404) { hoursDone = null; message = e.detail }
            }
        }
    }

    suspend fun loadWeek(s: LocalDate) {
        try {
            val api = app.sessions.requireApi()
            val months = DigestRules.months(s).map { m -> api.get<JournalMonth>("/api/journal?month=$m") }
            week = DigestRules.week(s, months.flatMap { it.items }, months.flatMap { it.pending })
            weekFailed = false
        } catch (e: ApiError) { weekFailed = true }
    }
    suspend fun loadLookback() {
        try {
            look = app.sessions.requireApi().get<Lookback>("/api/reflection"); lookFailed = false
        } catch (e: ApiError) {
            if (e.status == 404) look = Lookback(available = false) else lookFailed = true
        }
    }
    LaunchedEffect(start) { loadWeek(start) }
    LaunchedEffect(Unit) { loadLookback() }
    // skills Kelo proposed or drafted, waiting for the owner's approval (GET /api/skills/proposals); asked again while one is drafted
    var skills by remember { mutableStateOf<SkillProposals?>(null) }
    suspend fun loadSkills() { try { skills = SkillCalls.load(app.sessions.requireApi()) } catch (e: ApiError) { /* keeps what is shown */ } }
    val drafting = skills?.drafting?.isNotEmpty() == true
    LaunchedEffect(Unit) { loadSkills() }
    LaunchedEffect(drafting) {
        // while a draft is being written, look again now and then (as the Android app does), so it shows up without a reload
        repeat(SkillRules.POLL_TIMES) {
            if (skills?.drafting.isNullOrEmpty()) return@LaunchedEffect
            kotlinx.coroutines.delay(SkillRules.POLL_MILLIS)
            loadSkills()
        }
    }
    fun answerSkill(p: SkillProposal, approve: Boolean, done: (Boolean) -> Unit) {
        scope.launch {
            try {
                SkillCalls.answer(app.sessions.requireApi(), p, approve)
                done(true)
                kotlinx.coroutines.delay(SkillRules.DONE_MILLIS)
                skills = skills?.let { SkillRules.without(it, p.id) }
            } catch (e: ApiError) {
                // 404: already answered elsewhere; the list loads again either way
                done(false)
                if (e.status != 404) message = e.detail
                loadSkills()
            }
        }
    }

    SubPage(stringResource(R.string.my_records_title), onBack) {
        WeekBar(start, DigestRules.canGoNext(start, today), onPrev = { start = start.minusWeeks(1) }, onNext = { start = start.plusWeeks(1) })
        if (message.isNotEmpty()) MutedText(message, small = true)
        val d = week?.takeIf { it.start == start }
        if (d == null) {
            if (weekFailed) ErrorText(stringResource(R.string.connect_failed), small = true) else MutedText(stringResource(R.string.loading), small = true)
        } else {
            DayStrip(start, DigestRules.perDay(d), today)
            val notes = look?.notes.orEmpty().associateBy { it.date.take(10) }
            val dates = (d.days.map { it.date.take(10) } + notes.keys.filter { it in start.toString()..d.end.toString() }).distinct().sortedDescending()
            Text(pluralStringResource(R.plurals.my_records_week_count, d.entries.size, d.entries.size), style = MaterialTheme.typography.bodySmall, color = c.muted,
                modifier = Modifier.padding(horizontal = 4.dp))
            if (dates.isEmpty()) EmptyNote(stringResource(R.string.my_records_week_empty))
            dates.forEach { date ->
                val day = d.days.firstOrNull { it.date.take(10) == date }
                val parsed = DayGroups.parseDay(date)
                Row(Modifier.fillMaxWidth().padding(top = 6.dp, start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(parsed?.let { fullDayText(it) } ?: date, style = MaterialTheme.typography.labelLarge, color = c.accent, modifier = Modifier.weight(1f))
                    if (date in rebuilding) CircularProgressIndicator(Modifier.size(16.dp), color = c.muted, strokeWidth = 2.dp)
                    else if (parsed != null && parsed < today) LinkButton(stringResource(R.string.agent_journal_rebuild), onClick = { askRebuild = date })
                }
                GroupCard {
                    day?.entries.orEmpty().forEachIndexed { i, e ->
                        if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 16.dp))
                        EntryRow(e, open = e.id in expanded, onToggle = { expanded = if (e.id in expanded) expanded - e.id else expanded + e.id }, onDelete = { askDelete = e })
                    }
                    day?.gap?.let { g ->
                        Text(
                            stringResource(if (g.status == "failed") R.string.agent_journal_failed else R.string.agent_journal_pending) +
                                (if (g.status == "failed" && g.reason.isNotBlank()) " · " + g.reason else ""),
                            style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.padding(16.dp),
                        )
                    }
                    notes[date]?.let { n ->
                        if (day?.entries?.isNotEmpty() == true || day?.gap != null) HorizontalDivider(color = c.line)
                        Column(Modifier.fillMaxWidth().background(c.bubble.copy(alpha = 0.5f)).padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                            Text(stringResource(R.string.my_records_night_note), style = MaterialTheme.typography.labelLarge, color = c.muted)
                            MarkdownView(n.text)
                        }
                    }
                }
            }
        }

        SectionLabel(stringResource(R.string.lookback_title))
        val data = look
        when {
            data == null -> if (lookFailed) ErrorText(stringResource(R.string.connect_failed), small = true) else MutedText(stringResource(R.string.loading), small = true)
            !data.available -> {
                MutedText(stringResource(R.string.lookback_unavailable), small = true)
                hours?.let { h -> HoursSuggestionCard(h, hoursDone) { accept -> answerHours(h, accept) } }
            }
            else -> {
                GroupCard {
                    LookbackSwitchRow(data.enabled) { on ->
                        val before = data
                        look = before.copy(enabled = on)
                        scope.launch {
                            try { look = app.sessions.requireApi().put<Lookback>("/api/reflection/enabled", buildJsonObject { put("enabled", on) }) }
                            catch (e: ApiError) { look = before; message = e.detail }
                        }
                    }
                    LastRunLine(data.lastRun, Modifier.padding(start = 50.dp, end = 14.dp,
                        bottom = if (chat.kelo.app.agent.MemoryPassRules.shown(data.memory?.lastPass)) 0.dp else 12.dp))
                    MemoryPassLine(data.memory?.lastPass, Modifier.padding(start = 50.dp, end = 14.dp, bottom = 6.dp)) {
                        showMemory = true; memChanges = null; memFailed = false
                        scope.launch {
                            try { memChanges = app.sessions.requireApi().get<MemoryChanges>("/api/reflection/memory") } catch (e: ApiError) { memFailed = true }
                        }
                    }
                }
                MutedText(stringResource(R.string.lookback_about), small = true)
                // the review list: learned message hours first (a row of their own), then the suggested and kept rules
                hours?.let { h -> HoursSuggestionCard(h, hoursDone) { accept -> answerHours(h, accept) } }
                LearnedRulesBlock(data.rules) { id, status ->
                    val before = data
                    look = before.copy(rules = LookbackRules.withStatus(before.rules, id, status))
                    scope.launch {
                        try {
                            val rule = app.sessions.requireApi().patch<LearnedRule>("/api/reflection/rules/$id", buildJsonObject { put("status", status) })
                            look = look?.let { l -> l.copy(rules = LookbackRules.replace(l.rules, rule)) }
                        } catch (e: ApiError) {
                            look = before
                            if (e.status == 404) loadLookback() else message = e.detail
                        }
                    }
                }
            }
        }
        // the same review area as the learned rules
        SkillProposalsBlock(skills, onAnswer = ::answerSkill)
    }
    if (showMemory) {
        MemoryChangesDialog(memChanges, memFailed, undoing, onClose = { showMemory = false }, onUndo = {
            undoing = true
            scope.launch {
                try {
                    app.sessions.requireApi().post<JsonElement>("/api/reflection/memory/undo")
                    message = strings.get(R.string.memory_pass_undone)
                    showMemory = false
                } catch (e: ApiError) {
                    message = if (e.status == 404) strings.get(R.string.memory_pass_undo_gone) else e.detail
                    if (e.status == 404) showMemory = false
                } finally {
                    undoing = false
                    loadLookback()
                }
            }
        })
    }
    askRebuild?.let { date ->
        val label = DayGroups.parseDay(date)?.let { fullDayText(it) } ?: date
        ConfirmDialog(stringResource(R.string.agent_journal_rebuild_confirm, label), stringResource(R.string.agent_journal_rebuild),
            onDismiss = { askRebuild = null }, danger = false, onYes = {
                rebuilding = rebuilding + date
                scope.launch {
                    try {
                        app.sessions.requireApi().post<JsonElement>("/api/journal/rebuild?date=$date")
                        message = strings.get(R.string.agent_journal_rebuilt)
                    } catch (e: ApiError) {
                        message = if (e.status == 409) strings.get(R.string.agent_journal_openai) else e.detail
                    } finally {
                        rebuilding = rebuilding - date
                        loadWeek(start)
                    }
                }
            })
    }
    askDelete?.let { e ->
        ConfirmDialog(stringResource(R.string.agent_journal_delete_confirm, e.title), stringResource(R.string.delete), onDismiss = { askDelete = null }, onYes = {
            week = week?.let { w -> w.copy(entries = w.entries.filter { it.id != e.id }) }
            scope.launch {
                try {
                    app.sessions.requireApi().delete<JsonElement>("/api/journal/${e.id}")
                    message = strings.get(R.string.agent_journal_deleted)
                } catch (ex: ApiError) {
                    if (ex.status != 404) { message = ex.detail; loadWeek(start) }
                }
            }
        })
    }
}

@Composable
private fun GroupCard(content: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp))) { content() }
}

@Composable
private fun WeekBar(start: LocalDate, canNext: Boolean, onPrev: () -> Unit, onNext: () -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onPrev) { Icon(KeloIcons.ChevronLeft, contentDescription = stringResource(R.string.my_records_prev_week), tint = c.ink) }
        Text(stringResource(R.string.my_records_week_range, dayText(start), dayText(start.plusDays(6))), style = MaterialTheme.typography.titleMedium, color = c.ink,
            textAlign = TextAlign.Center, modifier = Modifier.weight(1f))
        IconButton(onClick = onNext, enabled = canNext) {
            Icon(KeloIcons.ChevronRight, contentDescription = stringResource(R.string.my_records_next_week), tint = if (canNext) c.ink else c.disabledInk)
        }
    }
}

@Composable
private fun DayStrip(start: LocalDate, counts: List<Int>, today: LocalDate) {
    val c = Kelo.colors
    val most = (counts.maxOrNull() ?: 0).coerceAtLeast(1)
    Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.Bottom) {
        counts.forEachIndexed { i, n ->
            val day = start.plusDays(i.toLong())
            Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.weight(1f)) {
                Box(Modifier.height(36.dp), contentAlignment = Alignment.BottomCenter) {
                    Box(Modifier.width(14.dp).height((6 + 30 * n / most).dp).clip(RoundedCornerShape(4.dp)).background(if (n > 0) c.accent else c.line))
                }
                Text(day.dayOfWeek.getDisplayName(java.time.format.TextStyle.NARROW, appLocale()), style = MaterialTheme.typography.labelSmall,
                    color = if (day == today) c.accent else c.muted, fontWeight = if (day == today) FontWeight.Bold else FontWeight.Normal)
            }
        }
    }
}

@Composable
private fun EntryRow(e: JournalEntry, open: Boolean, onToggle: () -> Unit, onDelete: () -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().clickable(onClick = onToggle).padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 12.dp), verticalAlignment = Alignment.Top) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(e.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, color = c.ink)
            if (e.summary.isNotBlank()) Text(e.summary, style = MaterialTheme.typography.bodyMedium, color = c.ink.copy(alpha = 0.85f),
                maxLines = if (open) Int.MAX_VALUE else 2, overflow = TextOverflow.Ellipsis)
        }
        IconButton(onClick = onDelete) { Icon(KeloIcons.Trash, contentDescription = stringResource(R.string.agent_journal_delete), tint = c.muted, modifier = Modifier.size(20.dp)) }
    }
}

@Composable
private fun LookbackSwitchRow(on: Boolean, onToggle: (Boolean) -> Unit) {
    val c = Kelo.colors
    val desc = stringResource(R.string.lookback_title)
    Row(Modifier.fillMaxWidth().padding(14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        Icon(KeloIcons.Book, contentDescription = null, tint = c.ink, modifier = Modifier.size(22.dp))
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(desc, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink)
            Text(stringResource(R.string.lookback_when), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(checked = on, onCheckedChange = onToggle, modifier = Modifier.semantics { contentDescription = desc },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent))
    }
}

/** The last run in one plain sentence from the server's structured fields (android agent/LookbackParts.kt); never its log text. */
@Composable
private fun LastRunLine(run: LookbackRun?, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    val line = run?.let(LookbackRules::runLine) ?: return
    val at = run.at
    if (line.withWhen && at == null) return
    val whenText = at?.let {
        when (LookbackRules.runWhen(it, java.time.ZonedDateTime.now())) {
            RunWhen.LAST_NIGHT -> stringResource(R.string.lookback_at_last_night)
            RunWhen.TODAY -> stringResource(R.string.lookback_at_today)
            RunWhen.DAY -> stringResource(R.string.lookback_at_day,
                dayText(java.time.Instant.ofEpochMilli((it * 1000).toLong()).atZone(java.time.ZoneId.systemDefault()).toLocalDate()))
        }
    }.orEmpty()
    var text = when {
        line.why != null -> stringResource(line.text, whenText, stringResource(line.why!!))
        line.withWhen -> stringResource(line.text, whenText)
        else -> stringResource(line.text)
    }
    line.newRules?.let { n ->
        text += " · " + if (n > 0) pluralStringResource(R.plurals.lookback_line_new, n, n) else stringResource(R.string.lookback_line_none)
    }
    Text(text.replaceFirstChar { it.titlecase() }, style = MaterialTheme.typography.bodySmall, color = if (line.failed) c.danger else c.muted, modifier = modifier)
}

@Composable
private fun LearnedRulesBlock(rules: List<LearnedRule>, onStatus: (Long, String) -> Unit) {
    val c = Kelo.colors
    var decided by remember { mutableStateOf(mapOf<Long, String>()) }
    LaunchedEffect(decided) {
        if (decided.isNotEmpty()) {
            kotlinx.coroutines.delay(LookbackRules.UNDO_MILLIS)
            decided = emptyMap()
        }
    }
    val groups = remember(rules, decided) { LookbackRules.group(rules, decided.keys) }
    var open by remember { mutableStateOf(setOf<Long>()) }
    var showRejected by remember { mutableStateOf(false) }
    fun toggle(id: Long) { open = if (id in open) open - id else open + id }
    if (groups.empty) {
        GroupCard { MutedText(stringResource(R.string.lookback_rules_empty), small = true, modifier = Modifier.padding(14.dp)) }
        return
    }
    if (groups.proposed.isNotEmpty()) {
        RulesHeading(pluralStringResource(R.plurals.lookback_rules_proposed, groups.proposed.size, groups.proposed.size), c.accent)
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            groups.proposed.forEach { r ->
                val shape = RoundedCornerShape(12.dp)
                val choice = decided[r.id]
                Column(
                    Modifier.fillMaxWidth().clip(shape).background(c.accent.copy(alpha = 0.08f)).border(1.dp, c.accent.copy(alpha = 0.45f), shape)
                        .padding(start = 14.dp, end = 14.dp, top = 12.dp, bottom = 10.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(r.rule, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink)
                    Basis(r)
                    if (choice == null) {
                        RuleMeta(r)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                            SecondaryButton(stringResource(R.string.lookback_rule_discard), modifier = Modifier.weight(1f), onClick = {
                                decided = decided + (r.id to LearnedRule.REJECTED); onStatus(r.id, LearnedRule.REJECTED)
                            })
                            PrimaryButton(stringResource(R.string.lookback_rule_keep), modifier = Modifier.weight(1f), onClick = {
                                decided = decided + (r.id to LearnedRule.ACCEPTED); onStatus(r.id, LearnedRule.ACCEPTED)
                            })
                        }
                    } else {
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Icon(if (choice == LearnedRule.ACCEPTED) KeloIcons.Check else KeloIcons.Minus, contentDescription = null,
                                tint = if (choice == LearnedRule.ACCEPTED) c.accent else c.muted, modifier = Modifier.size(18.dp))
                            Text(stringResource(if (choice == LearnedRule.ACCEPTED) R.string.lookback_rule_kept else R.string.lookback_rule_discarded),
                                style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium, color = c.ink, modifier = Modifier.weight(1f))
                            LinkButton(stringResource(R.string.undo), onClick = { decided = decided - r.id; onStatus(r.id, LearnedRule.PROPOSED) })
                        }
                    }
                }
            }
        }
    }
    if (groups.accepted.isNotEmpty()) {
        RulesHeading(stringResource(R.string.lookback_rules_accepted), c.muted)
        GroupCard {
            groups.accepted.forEachIndexed { i, r ->
                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 14.dp))
                RuleRow(r, open = r.id in open, onToggle = { toggle(r.id) }) {
                    LinkButton(stringResource(R.string.lookback_rule_stop), onClick = { onStatus(r.id, LearnedRule.REJECTED) })
                }
            }
        }
    }
    if (groups.rejected.isNotEmpty()) {
        Row(
            Modifier.fillMaxWidth().padding(top = 4.dp).clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button) { showRejected = !showRejected }
                .padding(horizontal = 4.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(pluralStringResource(R.plurals.lookback_rules_rejected, groups.rejected.size, groups.rejected.size), style = MaterialTheme.typography.bodySmall,
                color = c.muted, modifier = Modifier.weight(1f))
            Icon(if (showRejected) KeloIcons.Minus else KeloIcons.Plus, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
        }
        if (showRejected) GroupCard {
            groups.rejected.forEachIndexed { i, r ->
                if (i > 0) HorizontalDivider(color = c.line, modifier = Modifier.padding(horizontal = 14.dp))
                RuleRow(r, open = r.id in open, onToggle = { toggle(r.id) }, muted = true) {
                    LinkButton(stringResource(R.string.lookback_rule_restore), onClick = { onStatus(r.id, LearnedRule.ACCEPTED) })
                }
            }
        }
    }
}

@Composable
private fun RulesHeading(text: String, color: Color) {
    Text(text, style = MaterialTheme.typography.labelLarge, color = color, modifier = Modifier.padding(start = 4.dp, top = 10.dp, bottom = 2.dp))
}

/** A kept or discarded rule: its date; a suggestion replies already follow: "이미 답에 반영 중". */
@Composable
private fun RuleMeta(r: LearnedRule) {
    val text = if (r.status == LearnedRule.PROPOSED) {
        if (LookbackRules.inUse(r)) stringResource(R.string.lookback_rule_in_use) else null
    } else DayGroups.parseDay(r.date)?.let { dayText(it) }
    if (text != null) Text(text, style = MaterialTheme.typography.labelSmall, color = Kelo.colors.muted)
}

/** One line of why it was suggested: "근거: 11월 8일 (일) · 캘린더 일정 등록 허락을 3번 거절했어요". */
@Composable
private fun Basis(r: LearnedRule) {
    val body = listOfNotNull(DayGroups.parseDay(r.date)?.let { dayText(it) }, r.evidence.trim().ifBlank { null }).joinToString(" · ")
    if (body.isNotEmpty()) Text(stringResource(R.string.lookback_rule_basis, body), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
}

@Composable
private fun Evidence(r: LearnedRule) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(c.bubble.copy(alpha = 0.6f)).padding(10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(stringResource(R.string.lookback_rule_why), style = MaterialTheme.typography.labelSmall, color = c.muted)
        Text(r.evidence.ifBlank { stringResource(R.string.lookback_rule_no_evidence) }, style = MaterialTheme.typography.bodySmall, color = c.ink)
    }
}

@Composable
private fun RuleRow(r: LearnedRule, open: Boolean, onToggle: () -> Unit, muted: Boolean = false, action: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(
        Modifier.fillMaxWidth().clickable(role = Role.Button, onClick = onToggle).padding(start = 14.dp, end = 14.dp, top = 12.dp, bottom = if (open) 6.dp else 10.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(r.rule, style = MaterialTheme.typography.bodyMedium, color = if (muted) c.muted else c.ink)
        RuleMeta(r)
        if (open) { Evidence(r); action() }
    }
}

/** The last memory pass in one line ("기억 정리: 3개 추가, 2개 정리") with a link to what changed (android agent/LookbackParts.kt). */
@Composable
private fun MemoryPassLine(pass: MemoryPass?, modifier: Modifier = Modifier, onOpen: () -> Unit) {
    val c = Kelo.colors
    if (pass == null || !MemoryPassRules.shown(pass)) return
    val text = if (pass.undone) stringResource(R.string.memory_pass_undone) else {
        val added = if (pass.added > 0) pluralStringResource(R.plurals.memory_pass_added, pass.added, pass.added) else null
        val tidied = if (pass.tidied > 0) pluralStringResource(R.plurals.memory_pass_tidied, pass.tidied, pass.tidied) else null
        stringResource(R.string.memory_pass_line,
            if (added != null && tidied != null) stringResource(R.string.memory_pass_both, added, tidied) else added ?: tidied.orEmpty())
    }
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(text, style = MaterialTheme.typography.bodySmall, color = c.muted, modifier = Modifier.weight(1f))
        if (!pass.undone) LinkButton(stringResource(R.string.memory_pass_view), onClick = onOpen)
    }
}

@Composable
private fun memoryAreaName(area: MemoryArea): String = stringResource(
    when (area) {
        MemoryArea.ABOUT_YOU -> R.string.memory_area_about_you
        MemoryArea.TO_REMEMBER -> R.string.memory_area_to_remember
        MemoryArea.PEOPLE -> R.string.memory_area_people
        MemoryArea.WORKING_NOTES -> R.string.memory_area_working_notes
    },
)

/** What the last memory pass changed, by area ("+" new or rewritten, "−" taken out), with its undo. */
@Composable
private fun MemoryChangesDialog(data: MemoryChanges?, failed: Boolean, undoing: Boolean, onClose: () -> Unit, onUndo: () -> Unit) {
    val c = Kelo.colors
    var ask by remember { mutableStateOf(false) }
    androidx.compose.material3.AlertDialog(
        onDismissRequest = onClose,
        containerColor = c.card,
        title = { Text(stringResource(R.string.memory_pass_changes_title), color = c.ink, style = MaterialTheme.typography.titleMedium) },
        text = {
            Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                when {
                    data == null -> if (failed) ErrorText(stringResource(R.string.connect_failed), small = true) else MutedText(stringResource(R.string.loading), small = true)
                    else -> {
                        val groups = MemoryPassRules.group(data.changes)
                        if (groups.isEmpty()) MutedText(stringResource(R.string.memory_pass_no_lines), small = true)
                        groups.forEach { g ->
                            Text(memoryAreaName(g.area), style = MaterialTheme.typography.labelLarge, color = c.muted, modifier = Modifier.padding(top = 4.dp))
                            g.added.forEach { ChangedLine(it, added = true) }
                            g.removed.forEach { ChangedLine(it, added = false) }
                        }
                    }
                }
            }
        },
        confirmButton = { androidx.compose.material3.TextButton(onClick = onClose) { Text(stringResource(R.string.close), color = c.accent) } },
        dismissButton = if (data?.lastPass?.canUndo == true) {
            { androidx.compose.material3.TextButton(onClick = { ask = true }, enabled = !undoing) { Text(stringResource(R.string.memory_pass_undo), color = c.ink) } }
        } else null,
    )
    if (ask) ConfirmDialog(stringResource(R.string.memory_pass_undo_confirm), stringResource(R.string.memory_pass_undo), onYes = onUndo,
        onDismiss = { ask = false }, danger = false)
}

@Composable
private fun ChangedLine(text: String, added: Boolean) {
    val c = Kelo.colors
    val desc = stringResource(if (added) R.string.memory_pass_added_line else R.string.memory_pass_removed_line, text)
    Row(Modifier.fillMaxWidth().semantics(mergeDescendants = true) { contentDescription = desc }, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(if (added) "+" else "\u2212", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Bold,
            color = if (added) c.accent else c.muted, modifier = Modifier.width(12.dp))
        Text(text, style = MaterialTheme.typography.bodySmall, color = if (added) c.ink else c.muted, modifier = Modifier.weight(1f),
            textDecoration = if (added) null else androidx.compose.ui.text.style.TextDecoration.LineThrough)
    }
}
