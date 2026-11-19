package chat.kelo.app.my

import chat.kelo.app.ui.text.*

import chat.kelo.app.res.*

import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewModelScope
import chat.kelo.app.LocalAppServices
import chat.kelo.app.agent.DayGroups
import chat.kelo.app.agent.clockText
import chat.kelo.app.agent.dayClockText
import chat.kelo.app.agent.dayLabel
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.Sessions
import chat.kelo.app.ui.components.EmptyNote
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SectionLabel
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.platform.rememberViewModel
import chat.kelo.app.ui.theme.Kelo
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import org.jetbrains.compose.resources.StringResource

/*
 * "What Kelo looked at": the hourly look's passes, what it read and what it did with each item (GET /api/proactive/research).
 * Models ignore fields the server adds later; the rules are pure so they are tested without a screen.
 */

/** A thing the look covers now: a section (kind "section", id = its code) or a connected service or phone item. [hourly]: read every hour. */
@Serializable
data class SituationLooked(val id: String = "", val name: String = "", val kind: String = "", val hourly: Boolean = false)

/**
 * A group of [SituationList.looks] (newer servers): when the things in [covers] are looked at. [whenCode] hourly | send | briefing |
 * needed; [times] "HH:MM" (send: the send times of the contact window now or next; briefing: its time).
 */
@Serializable
data class SituationLook(
    @SerialName("when") val whenCode: String = "",
    val times: List<String> = emptyList(),
    val covers: List<SituationLooked> = emptyList(),
)

/** What one pass read from one source: [state] new | changed | same | not_due | off | failed | timeout | skipped. */
@Serializable
data class SituationSource(val id: String = "", val name: String = "", val kind: String = "", val state: String = "", val entries: Int = 0)

@Serializable
data class SituationCounts(val proposed: Int = 0, val selected: Int = 0, val told: Int = 0, val dropped: Int = 0)

/** A pass: [status] running | no_change | nothing | proposed | selected | delivered | failed | skipped. */
@Serializable
data class SituationPass(
    val id: Long = 0,
    val at: Double = 0.0,
    val status: String = "",
    val counts: SituationCounts = SituationCounts(),
    val sources: List<SituationSource> = emptyList(),
)

/** GET /api/proactive/research (one page of passes). */
@Serializable
data class SituationList(
    val research: Boolean = false,
    @SerialName("keep_days") val keepDays: Int = 90,
    @SerialName("looked_at") val lookedAt: List<SituationLooked> = emptyList(),
    /** When each thing is looked at (newer servers); null from an older server: the page shows [lookedAt] as before. */
    val looks: List<SituationLook>? = null,
    val passes: List<SituationPass> = emptyList(),
    @SerialName("next_before") val nextBefore: Long? = null,
)

/** One status change of an item in a pass: [status] proposed | updated | selected | told | dropped | expired | waiting. */
@Serializable
data class SituationEvent(
    @SerialName("item_id") val itemId: Long = 0,
    val title: String = "",
    val status: String = "",
    val why: String = "",
    val priority: String = "",
    val at: Double = 0.0,
)

/** GET /api/proactive/research/passes/{id} */
@Serializable
data class SituationDetail(
    val pass: SituationPass = SituationPass(),
    val sources: List<SituationSource> = emptyList(),
    val events: List<SituationEvent> = emptyList(),
)

/** The mapping from the server's codes to what the screen says. Pure: no strings, no clock except where a zone is passed. */
object SituationRules {
    /** The sections in the order the look reads them: they always come first in the list. */
    val SECTIONS = listOf("conversation", "mail", "calendar", "goals", "schedules", "memory")

    enum class Outcome { NO_CHANGE, NOTHING, PROPOSED, SELECTED, DELIVERED, RUNNING, FAILED, OTHER }

    /** A pass's status code as an outcome; a code this app does not know is [Outcome.OTHER] (shown as a generic line). */
    fun outcome(status: String): Outcome = when (status.trim()) {
        "no_change" -> Outcome.NO_CHANGE
        "nothing" -> Outcome.NOTHING
        "proposed" -> Outcome.PROPOSED
        "selected" -> Outcome.SELECTED
        "delivered" -> Outcome.DELIVERED
        "running" -> Outcome.RUNNING
        "failed", "skipped" -> Outcome.FAILED
        else -> Outcome.OTHER
    }

    enum class Bucket { CHANGED, SAME, FAILED, OFF, OTHER }

    /** A source's state as the detail groups it: what changed, the same as before, could not be read, turned off. */
    fun bucket(state: String): Bucket = when (state.trim()) {
        "new", "changed" -> Bucket.CHANGED
        "same", "not_due" -> Bucket.SAME
        "failed", "timeout", "skipped" -> Bucket.FAILED
        "off" -> Bucket.OFF
        else -> Bucket.OTHER
    }

    enum class EventKind { PROPOSED, UPDATED, SELECTED_TODAY, SELECTED_REGULAR, TOLD, DROPPED, EXPIRED, WAITING, OTHER }

    /** An item's status change as the timeline shows it. A priority this app does not know is a regular choice. */
    fun eventKind(status: String, priority: String): EventKind = when (status.trim()) {
        "proposed" -> EventKind.PROPOSED
        "updated" -> EventKind.UPDATED
        "selected" -> if (priority.trim() == "today") EventKind.SELECTED_TODAY else EventKind.SELECTED_REGULAR
        "told" -> EventKind.TOLD
        "dropped" -> EventKind.DROPPED
        "expired" -> EventKind.EXPIRED
        "waiting" -> EventKind.WAITING
        else -> EventKind.OTHER
    }

    /** Whether a source is a section (its name comes from the app, by code). */
    fun isSection(source: SituationSource): Boolean = source.kind.trim() == "section"

    /** The hourly covers, sections first in their own order, then the rest in the server's order. */
    fun hourly(looked: List<SituationLooked>): List<SituationLooked> =
        looked.filter { it.hourly }.sortedBy { sectionRank(it) }

    /** The covers Kelo reads only when a question needs them. */
    fun onDemand(looked: List<SituationLooked>): List<SituationLooked> = looked.filter { !it.hourly }

    /** Index in [SECTIONS] for a section, after every section for anything else (a stable sort keeps the server's order). */
    fun sectionRank(looked: SituationLooked): Int =
        if (looked.kind.trim() == "section") SECTIONS.indexOf(looked.id.trim()).let { if (it < 0) SECTIONS.size else it } else SECTIONS.size

    enum class LookKind { HOURLY, SEND, BRIEFING, NEEDED, OTHER }

    /** A group's code as a kind; a code this app does not know is [LookKind.OTHER] (left out). */
    fun lookKind(code: String): LookKind = when (code.trim()) {
        "hourly" -> LookKind.HOURLY
        "send" -> LookKind.SEND
        "briefing" -> LookKind.BRIEFING
        "needed" -> LookKind.NEEDED
        else -> LookKind.OTHER
    }

    /** The groups the page shows, in the server's order: a kind this app knows, with something in it. */
    fun shownLooks(looks: List<SituationLook>): List<SituationLook> =
        looks.filter { lookKind(it.whenCode) != LookKind.OTHER && it.covers.isNotEmpty() }

    /** A group's times that are times ("HH:MM"), in the server's order. */
    fun lookTimes(look: SituationLook): List<String> = look.times.filter { NotifySettingsRules.parseTime(it) != null }

    /** The local day of an epoch-seconds time, or null when there is no time. */
    fun dayOf(seconds: Double, zone: ZoneId = ZoneId.systemDefault()): LocalDate? =
        if (seconds <= 0) null else Instant.ofEpochMilli((seconds * 1000).toLong()).atZone(zone).toLocalDate()

    /** The passes grouped by local day, newest day first, each pass in the server's order. */
    fun groupPasses(passes: List<SituationPass>, today: LocalDate, zone: ZoneId = ZoneId.systemDefault()): List<DayGroups.Group<SituationPass>> =
        DayGroups.group(passes, today) { dayOf(it.at, zone) }

    /** One item's events in the order they happened, with the item's title (the first non-empty one). */
    data class EventGroup(val itemId: Long, val title: String, val events: List<SituationEvent>)

    fun groupEvents(events: List<SituationEvent>): List<EventGroup> {
        val order = LinkedHashMap<Long, MutableList<SituationEvent>>()
        events.forEach { order.getOrPut(it.itemId) { mutableListOf() } += it }
        return order.map { (id, list) -> EventGroup(id, list.firstOrNull { it.title.isNotBlank() }?.title?.trim().orEmpty(), list) }
    }

    /** The sources of a detail in the order the groups are shown, each in the server's order. */
    fun bucketed(sources: List<SituationSource>): Map<Bucket, List<SituationSource>> =
        Bucket.entries.associateWith { b -> sources.filter { bucket(it.state) == b } }
}

/** What the list page shows: [list] is null while loading; [missing] is a server without the route (404). */
data class SituationUiState(
    val list: SituationList? = null,
    val missing: Boolean = false,
    val error: String = "",
    val loadingMore: Boolean = false,
)

/** Loads the newest passes and pages back with `before` (read-only). */
class SituationViewModel(private val sessions: Sessions) : ViewModel() {
    private val _state = MutableStateFlow(SituationUiState())
    val state: StateFlow<SituationUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@launch
            try {
                val list: SituationList = api.get("/api/proactive/research?limit=24")
                _state.update { it.copy(list = list) }
            } catch (e: ApiError) {
                if (e.status == 404) _state.update { it.copy(missing = true) } else _state.update { it.copy(error = e.detail) }
            }
        }
    }

    fun loadMore() {
        val before = _state.value.list?.nextBefore ?: return
        if (_state.value.loadingMore) return
        _state.update { it.copy(loadingMore = true) }
        viewModelScope.launch {
            val api = runCatching { sessions.requireApi() }.getOrNull()
            if (api == null) { _state.update { it.copy(loadingMore = false) }; return@launch }
            try {
                val more: SituationList = api.get("/api/proactive/research?limit=24&before=$before")
                _state.update { s ->
                    val list = s.list ?: return@update s.copy(loadingMore = false)
                    s.copy(list = list.copy(passes = list.passes + more.passes, nextBefore = more.nextBefore), loadingMore = false)
                }
            } catch (e: ApiError) {
                _state.update { it.copy(loadingMore = false, error = e.detail) }
            }
        }
    }
}

/** What the detail page shows: [detail] is null while loading. */
data class SituationDetailUiState(val detail: SituationDetail? = null, val missing: Boolean = false, val error: String = "")

/** Loads one pass with what happened to its items (read-only). */
class SituationDetailViewModel(private val sessions: Sessions, private val passId: Long) : ViewModel() {
    private val _state = MutableStateFlow(SituationDetailUiState())
    val state: StateFlow<SituationDetailUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            val api = runCatching { sessions.requireApi() }.getOrNull() ?: return@launch
            try {
                val d: SituationDetail = api.get("/api/proactive/research/passes/$passId")
                _state.update { it.copy(detail = d) }
            } catch (e: ApiError) {
                if (e.status == 404) _state.update { it.copy(missing = true) } else _state.update { it.copy(error = e.detail) }
            }
        }
    }
}

/** The row under the heads-up block: opens "What Kelo looked at" (shown only when the server has the route). */
@Composable
internal fun SituationRow(onClick: () -> Unit) {
    val c = Kelo.colors
    Row(Modifier.clickable(onClick = onClick), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(string(Res.string.situation_row), style = MaterialTheme.typography.bodySmall, color = c.accent)
        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.accent, modifier = Modifier.size(14.dp))
    }
}

/** The page: when Kelo looks at what, then its passes by day. Tapping a pass opens its detail. */
@Composable
fun SituationPage(onBack: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = rememberViewModel { SituationViewModel(sessions) }
    val ui by vm.state.collectAsStateWithLifecycle()
    var openPass by rememberSaveable { mutableStateOf(0L) }
    if (openPass != 0L) { SituationDetailPage(openPass, onBack = { openPass = 0L }); return }
    SubPage(string(Res.string.situation_title), onBack) {
        val list = ui.list
        when {
            ui.error.isNotEmpty() && list == null -> ErrorText(ui.error, small = true)
            ui.missing -> MutedText(string(Res.string.notify_page_unavailable), small = true)
            list == null -> SkeletonRows(3)
            else -> {
                val looks = list.looks
                if (looks != null) {
                    SituationRules.shownLooks(looks).forEach { LookGroup(it) }
                } else {   // an older server: what it covers, as before
                    val hourly = SituationRules.hourly(list.lookedAt)
                    val onDemand = SituationRules.onDemand(list.lookedAt)
                    if (hourly.isNotEmpty()) {
                        SectionLabel(string(Res.string.situation_hourly_head))
                        NameList(hourly.map { lookedName(it) })
                    }
                    if (onDemand.isNotEmpty()) {
                        SectionLabel(string(Res.string.situation_read_when_needed_head))
                        MutedText(string(Res.string.situation_read_when_needed_about), small = true, modifier = Modifier.padding(horizontal = 16.dp))
                        NameList(onDemand.map { lookedName(it) })
                    }
                }
                if (list.passes.isEmpty()) {
                    EmptyNote(string(Res.string.situation_empty))
                } else {
                    val groups = SituationRules.groupPasses(list.passes, LocalDate.now())
                    groups.forEach { g ->
                        SectionLabel(dayLabel(g.label))
                        Column(
                            Modifier.fillMaxWidth().padding(horizontal = 16.dp).clip(RoundedCornerShape(12.dp))
                                .border(1.dp, Kelo.colors.line, RoundedCornerShape(12.dp)),
                        ) {
                            g.items.forEachIndexed { i, pass ->
                                if (i > 0) HorizontalDivider(color = Kelo.colors.line)
                                PassRow(pass) { openPass = pass.id }
                            }
                        }
                    }
                }
                MutedText(string(Res.string.situation_keep_days, list.keepDays), small = true, modifier = Modifier.padding(horizontal = 16.dp))
                if (list.nextBefore != null) {
                    Row(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp).clickable(enabled = !ui.loadingMore) { vm.loadMore() },
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(string(Res.string.situation_load_more), style = MaterialTheme.typography.bodyMedium, color = Kelo.colors.accent)
                    }
                }
            }
        }
    }
}

/** The detail of one pass: what changed, what stayed the same, what could not be read, what was turned off, and what Kelo did. */
@Composable
fun SituationDetailPage(passId: Long, onBack: () -> Unit) {
    val sessions = LocalAppServices.current.sessions
    val vm = rememberViewModel(key = passId) { SituationDetailViewModel(sessions, passId) }
    val ui by vm.state.collectAsStateWithLifecycle()
    val detail = ui.detail
    val title = if (detail != null && detail.pass.at > 0) dayClockText(detail.pass.at) else string(Res.string.situation_title)
    SubPage(title, onBack) {
        when {
            ui.error.isNotEmpty() && detail == null -> ErrorText(ui.error, small = true)
            ui.missing -> MutedText(string(Res.string.notify_page_unavailable), small = true)
            detail == null -> SkeletonRows(3)
            else -> {
                val buckets = SituationRules.bucketed(detail.sources)
                val changed = buckets[SituationRules.Bucket.CHANGED].orEmpty()
                val same = buckets[SituationRules.Bucket.SAME].orEmpty()
                val failed = buckets[SituationRules.Bucket.FAILED].orEmpty()
                val off = buckets[SituationRules.Bucket.OFF].orEmpty()
                val other = buckets[SituationRules.Bucket.OTHER].orEmpty()
                if (changed.isNotEmpty()) {
                    SectionLabel(string(Res.string.situation_detail_changed))
                    SourceList(changed)
                }
                if (same.isNotEmpty()) {
                    MutedText(string(Res.string.situation_detail_same, same.size), small = true, modifier = Modifier.padding(horizontal = 16.dp))
                }
                if (failed.isNotEmpty()) {
                    SectionLabel(string(Res.string.situation_detail_failed))
                    SourceList(failed)
                }
                if (off.isNotEmpty()) {
                    SectionLabel(string(Res.string.situation_detail_off))
                    SourceList(off)
                }
                if (other.isNotEmpty()) {
                    SectionLabel(string(Res.string.situation_state_other))
                    SourceList(other)
                }
                SectionLabel(string(Res.string.situation_detail_done))
                val groups = SituationRules.groupEvents(detail.events)
                if (groups.isEmpty()) {
                    MutedText(string(Res.string.situation_detail_none), small = true, modifier = Modifier.padding(horizontal = 16.dp))
                } else {
                    Column(
                        Modifier.fillMaxWidth().padding(horizontal = 16.dp).clip(RoundedCornerShape(12.dp))
                            .border(1.dp, Kelo.colors.line, RoundedCornerShape(12.dp)),
                    ) {
                        groups.forEachIndexed { i, g ->
                            if (i > 0) HorizontalDivider(color = Kelo.colors.line)
                            EventGroupRow(g)
                        }
                    }
                }
            }
        }
    }
}

/** A cover's name: a section's name from the app's words, anything else as the server named it. */
@Composable
private fun lookedName(looked: SituationLooked): String =
    if (looked.kind.trim() == "section") sectionName(looked.id) else looked.name.ifBlank { string(Res.string.situation_section_other) }

@Composable
private fun sourceName(source: SituationSource): String =
    if (SituationRules.isSection(source)) sectionName(source.id) else source.name.ifBlank { string(Res.string.situation_section_other) }

/** The words of a section code (an unknown code is "Other"): the look's own sections and the briefing's. */
internal fun sectionRes(code: String): StringResource = when (code.trim()) {
    "conversation" -> Res.string.situation_section_conversation
    "mail" -> Res.string.situation_section_mail
    "calendar" -> Res.string.situation_section_calendar
    "goals" -> Res.string.situation_section_goals
    "schedules" -> Res.string.situation_section_schedules
    "memory" -> Res.string.situation_section_memory
    "corrections" -> Res.string.situation_section_corrections
    "held" -> Res.string.deliveries_held_title
    "weather" -> Res.string.briefing_weather
    "missed" -> Res.string.briefing_missed
    else -> Res.string.situation_section_other
}

@Composable
private fun sectionName(code: String): String = string(sectionRes(code))

/** One list of names in a bordered group (the covers of the hourly look, or the sources of a detail group). */
@Composable
internal fun NameList(names: List<String>) {
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, Kelo.colors.line, RoundedCornerShape(12.dp)),
    ) {
        names.forEachIndexed { i, name ->
            if (i > 0) HorizontalDivider(color = Kelo.colors.line)
            Text(name, style = MaterialTheme.typography.bodyLarge, color = Kelo.colors.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp))
        }
    }
}

/** One group of the page: its heading, its times and a note where it has them, then what is looked at (iPhone LookGroup). */
@Composable
internal fun LookGroup(look: SituationLook) {
    val times = SituationRules.lookTimes(look).map { timeWords(it) }.joinToString(" · ")
    val pad = Modifier.padding(horizontal = 16.dp)
    when (SituationRules.lookKind(look.whenCode)) {
        SituationRules.LookKind.HOURLY -> SectionLabel(string(Res.string.situation_hourly_head))
        SituationRules.LookKind.SEND -> {
            SectionLabel(string(Res.string.situation_send_head))
            if (times.isNotEmpty()) MutedText(string(Res.string.heads_send_times, times), small = true, modifier = pad)
            MutedText(string(Res.string.situation_send_about), small = true, modifier = pad)
        }
        SituationRules.LookKind.BRIEFING -> {
            SectionLabel(string(Res.string.situation_briefing_head))
            if (times.isNotEmpty()) MutedText(string(Res.string.situation_briefing_time, times), small = true, modifier = pad)
        }
        SituationRules.LookKind.NEEDED -> {
            SectionLabel(string(Res.string.situation_read_when_needed_head))
            MutedText(string(Res.string.situation_read_when_needed_about), small = true, modifier = pad)
        }
        SituationRules.LookKind.OTHER -> return
    }
    NameList(look.covers.map { lookedName(it) })
}

@Composable
internal fun SourceList(sources: List<SituationSource>) {
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp).clip(RoundedCornerShape(12.dp)).border(1.dp, Kelo.colors.line, RoundedCornerShape(12.dp)),
    ) {
        sources.forEachIndexed { i, s ->
            if (i > 0) HorizontalDivider(color = Kelo.colors.line)
            Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(sourceName(s), style = MaterialTheme.typography.bodyLarge, color = Kelo.colors.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
                Text(stateText(s.state), style = MaterialTheme.typography.bodySmall, color = Kelo.colors.muted)
            }
        }
    }
}

@Composable
internal fun PassRow(pass: SituationPass, onClick: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(clockText(pass.at), style = MaterialTheme.typography.bodyMedium, color = c.muted)
        Text(outcomeText(pass), style = MaterialTheme.typography.bodyLarge, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f))
        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
    }
}

@Composable
private fun outcomeText(pass: SituationPass): String = when (SituationRules.outcome(pass.status)) {
    SituationRules.Outcome.NO_CHANGE -> string(Res.string.situation_outcome_no_change)
    SituationRules.Outcome.NOTHING -> string(Res.string.situation_outcome_nothing)
    SituationRules.Outcome.PROPOSED -> string(Res.string.situation_outcome_proposed, pass.counts.proposed)
    SituationRules.Outcome.SELECTED -> string(Res.string.situation_outcome_selected, pass.counts.selected)
    SituationRules.Outcome.DELIVERED -> string(Res.string.situation_outcome_delivered)
    SituationRules.Outcome.RUNNING -> string(Res.string.situation_outcome_running)
    SituationRules.Outcome.FAILED -> string(Res.string.situation_outcome_failed)
    SituationRules.Outcome.OTHER -> string(Res.string.situation_outcome_other)
}

@Composable
private fun stateText(state: String): String = string(
    when (SituationRules.bucket(state)) {
        SituationRules.Bucket.CHANGED -> if (state.trim() == "new") Res.string.situation_state_new else Res.string.situation_state_changed
        SituationRules.Bucket.SAME -> Res.string.situation_state_same
        SituationRules.Bucket.FAILED -> when (state.trim()) {
            "timeout" -> Res.string.situation_state_timeout
            "skipped" -> Res.string.situation_state_skipped
            else -> Res.string.situation_state_failed
        }
        SituationRules.Bucket.OFF -> Res.string.situation_state_off
        SituationRules.Bucket.OTHER -> Res.string.situation_state_other
    },
)

@Composable
internal fun EventGroupRow(group: SituationRules.EventGroup) {
    val c = Kelo.colors
    Column(Modifier.fillMaxWidth().padding(horizontal = 14.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(group.title.ifBlank { string(Res.string.situation_section_other) }, style = MaterialTheme.typography.bodyLarge, color = c.ink,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
        group.events.forEach { e ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(clockText(e.at), style = MaterialTheme.typography.bodySmall, color = c.muted)
                Text(eventText(e), style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
        }
    }
}

@Composable
private fun eventText(e: SituationEvent): String = when (SituationRules.eventKind(e.status, e.priority)) {
    SituationRules.EventKind.PROPOSED -> string(Res.string.situation_event_proposed)
    SituationRules.EventKind.UPDATED, SituationRules.EventKind.OTHER -> string(Res.string.situation_event_updated)
    SituationRules.EventKind.SELECTED_TODAY -> string(Res.string.situation_event_selected_today)
    SituationRules.EventKind.SELECTED_REGULAR -> string(Res.string.situation_event_selected_regular)
    SituationRules.EventKind.TOLD -> string(Res.string.situation_event_told)
    SituationRules.EventKind.DROPPED -> if (e.why.isBlank() || e.why.trim() == "expired") string(Res.string.situation_event_dropped_none)
        else string(Res.string.situation_event_dropped, e.why.trim())
    SituationRules.EventKind.EXPIRED -> string(Res.string.situation_event_expired)
    SituationRules.EventKind.WAITING -> string(Res.string.situation_event_waiting)
}
