package chat.kelo.app.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.SmallPrimary
import chat.kelo.app.agent.clockText
import chat.kelo.app.my.ContactHoursRules
import chat.kelo.app.ui.components.SecondaryButton
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.rememberHaptics
import chat.kelo.app.ui.theme.Kelo

/** Where a tap on a briefing item goes (the chat screen does it: [BriefingRules.tap]). */
sealed interface BriefingTap {
    /** The chat at the follow-up's own message */
    data class ChatAt(val messageId: Long) : BriefingTap
    /** The 작업 panel's list of what waits (a follow-up without a message of its own) */
    data object Waiting : BriefingTap
    /** A background job's task */
    data class Task(val id: Long) : BriefingTap
    /** The goals tab */
    data object Goals : BriefingTap
    /** The phone's calendar at that moment (unix ms) */
    data class Event(val atMs: Long) : BriefingTap
    /** A question about that mail written into the composer (nothing is sent until the owner sends it) */
    data class Mail(val subject: String) : BriefingTap
    /** One of Kelo's suggested next steps, a question to the owner, written into the composer the same way (nothing is sent) */
    data class Suggest(val text: String) : BriefingTap
}

/** A small reference under a "worth noticing" note: [label] names the card item, [tap] is what a tap on that item does (null: not clickable). */
data class BriefingChip(val ref: String, val label: String, val tap: BriefingTap?)

/** The briefing card's small rules, apart from the UI. */
object BriefingRules {
    /** Where an item opens; null: it has no place of its own (a promise noted in memory). */
    fun tap(open: BriefingOpen?): BriefingTap? = when (open?.kind) {
        "followup" -> open.messageId?.let { BriefingTap.ChatAt(it) } ?: BriefingTap.Waiting
        "job" -> open.taskId?.let { BriefingTap.Task(it) } ?: BriefingTap.Waiting
        "goal" -> BriefingTap.Goals
        "event" -> open.start?.let { BriefingTap.Event((it * 1000).toLong()) }
        "mail" -> open.subject?.takeIf { it.isNotBlank() }?.let { BriefingTap.Mail(it) }
        else -> null
    }

    /** The day in short: the headline, else the older one-line text; ONE text, never both (the server copies the headline into the line). */
    fun headline(card: BriefingCard): String? =
        card.headline?.trim()?.takeIf { it.isNotEmpty() } ?: card.line?.trim()?.takeIf { it.isNotEmpty() }

    /** The model's notes / next steps without an empty text; everything else is kept as the server sent it. */
    fun connections(card: BriefingCard): List<BriefingLink> = card.connections.filter { it.text.isNotBlank() }
    fun suggestions(card: BriefingCard): List<BriefingLink> = card.suggestions.filter { it.text.isNotBlank() }

    /** The card's sections, in order, that have something: headline, notes, weather, today, ahead (the coming-up line), missed, steps, hours. */
    fun sections(card: BriefingCard): List<String> = buildList {
        if (headline(card) != null) add("headline")
        if (connections(card).isNotEmpty()) add("notes")
        if (card.weather != null) add("weather")
        if (card.events.isNotEmpty()) add("today")
        if (card.ahead.isNotEmpty()) add("ahead")
        if (card.missed.isNotEmpty()) add("missed")
        if (suggestions(card).isNotEmpty()) add("steps")
        if (hoursShown(card.hours)) add("hours")
    }

    /**
     * The chips under a note: one per ref that exists on the card, in the note's order, each id once. Labelled "Weather" ([weather]) for
     * the weather, "10:00 Design review" for an event ([clock] writes the time), the title for an ahead / missed item. A ref that
     * points at nothing (or an item without a title) is skipped; an item with no place of its own gives a chip with a null tap.
     */
    fun chips(card: BriefingCard, refs: List<String>, weather: String, clock: (Double) -> String): List<BriefingChip> =
        refs.map { it.trim() }.filter { it.isNotEmpty() }.distinct().mapNotNull { ref ->
            val w = card.weather
            if (w != null && w.id.isNotEmpty() && w.id == ref) return@mapNotNull BriefingChip(ref, weather, null)
            card.events.firstOrNull { it.id.isNotEmpty() && it.id == ref }?.let { e ->
                val label = listOf(clock(e.start), e.title.trim()).filter { it.isNotEmpty() }.joinToString(" ")
                return@mapNotNull if (e.title.isBlank()) null else BriefingChip(ref, label, tap(e.open))
            }
            card.ahead.firstOrNull { it.id.isNotEmpty() && it.id == ref }?.let { a ->
                return@mapNotNull if (a.title.isBlank()) null else BriefingChip(ref, a.title.trim(), tap(a.open))
            }
            card.missed.firstOrNull { it.id.isNotEmpty() && it.id == ref }?.let { m ->
                return@mapNotNull if (m.title.isBlank()) null else BriefingChip(ref, m.title.trim(), tap(m.open))
            }
            null
        }

    /** A big event's days in the coming-up line: "11월 12일" or "11월 12일 – 11월 14일", each day written by [day]. */
    fun aheadDays(a: BriefingAhead, day: (java.time.LocalDate) -> String): String {
        val first = runCatching { java.time.LocalDate.parse(a.first) }.getOrNull() ?: return ""
        val last = runCatching { java.time.LocalDate.parse(a.last) }.getOrNull()
        return if (last == null || !last.isAfter(first)) day(first) else day(first) + " – " + day(last)
    }

    /** The coming-up line: "상하이 여행 11월 12일 – 11월 14일 · 출국 KE893 11월 16일". */
    fun aheadLine(card: BriefingCard, day: (java.time.LocalDate) -> String): String =
        card.ahead.joinToString(" · ") { a -> listOf(a.title, aheadDays(a, day)).filter { it.isNotBlank() }.joinToString(" ") }

    /** The label of a missed item's kind (followup, job, approval, login, mail, goal, promise). */
    fun kindLabel(kind: String): Int = when (kind) {
        "job" -> R.string.briefing_kind_job
        "approval" -> R.string.briefing_kind_approval
        "login" -> R.string.briefing_kind_login
        "mail" -> R.string.briefing_kind_mail
        "goal" -> R.string.briefing_kind_goal
        "promise" -> R.string.briefing_kind_promise
        else -> R.string.briefing_kind_followup
    }

    /** The hours line can be shown: an id and a real start time. */
    fun hoursShown(h: BriefingHours?): Boolean = h != null && h.id.isNotBlank() && ContactHoursRules.minutes(h.wakeStart) != null
}

/**
 * The morning briefing in the chat: a header, the one line Kelo wrote (when the model was there), then 날씨 / 오늘 일정 / 놓친 것 with
 * only what has something; every item that has a place opens it ([onTap]); a pending contact-hours suggestion at the bottom
 * ([onHours]: accept or keep). The data source's credit sits under the weather.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun BriefingCardView(card: BriefingCard, onTap: (BriefingTap) -> Unit, onHours: (BriefingHours, Boolean) -> Unit, onLink: (String) -> Unit) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(16.dp)
    Column(
        Modifier.fillMaxWidth().clip(shape).background(c.card).border(1.dp, c.line, shape).padding(horizontal = 14.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Icon(KeloIcons.Spark, contentDescription = null, tint = c.accent, modifier = Modifier.size(18.dp))
            Text(stringResource(R.string.briefing_title), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold,
                color = c.ink, modifier = Modifier.weight(1f).semantics { heading() })
        }
        BriefingRules.headline(card)?.let { Text(it, style = MaterialTheme.typography.bodyLarge, color = c.ink) }
        val notes = BriefingRules.connections(card)
        if (notes.isNotEmpty()) {
            val weatherLabel = stringResource(R.string.briefing_weather)
            val times = card.events.associate { it.start to clockText(it.start) }
            Section(KeloIcons.Spark, stringResource(R.string.briefing_connections)) {
                notes.forEach { n ->
                    val chips = BriefingRules.chips(card, n.refs, weatherLabel) { times[it].orEmpty() }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Bullet(n.text)
                        if (chips.isNotEmpty()) {
                            FlowRow(Modifier.padding(start = 16.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                chips.forEach { ch -> RefChip(ch.label, ch.tap?.let { t -> { onTap(t) } }) }
                            }
                        }
                    }
                }
            }
        }
        var first = notes.isEmpty() && BriefingRules.headline(card) == null
        card.weather?.let { w ->
            first = false
            Section(KeloIcons.Cloud, stringResource(R.string.briefing_weather)) {
                val place = card.place?.name.orEmpty()
                val head = listOf(place, w.headline).filter { it.isNotBlank() }.joinToString(" · ")
                if (head.isNotBlank()) Text(head, style = MaterialTheme.typography.bodyMedium, color = c.ink)
                w.notes.forEach { n -> Bullet(n.text) }
                if (w.attribution.isNotBlank()) {
                    Text(
                        w.attribution, style = MaterialTheme.typography.labelSmall, color = c.muted,
                        modifier = if (w.attributionUrl.isNotBlank()) Modifier.clickable(role = Role.Button) { onLink(w.attributionUrl) } else Modifier,
                    )
                }
            }
        }
        if (card.events.isNotEmpty()) {
            if (!first) HorizontalDivider(color = c.line)
            first = false
            Section(KeloIcons.Calendar, stringResource(R.string.briefing_today)) {
                card.events.forEach { e ->
                    val tap = BriefingRules.tap(e.open)
                    val time = clockText(e.start)
                    Item(
                        title = e.title, lead = time,
                        detail = e.where.takeIf { it.isNotBlank() }?.let { stringResource(R.string.briefing_event_where, it) },
                        source = e.source.takeIf { it.isNotBlank() },
                        onClick = tap?.let { t -> { onTap(t) } },
                    )
                }
            }
        }
        if (card.ahead.isNotEmpty()) {
            // big events this week, as one short line (the server found them: a trip, a flight, a stay, several days)
            val locale = androidx.compose.ui.platform.LocalConfiguration.current.locales[0]
            val dayFormat = androidx.compose.runtime.remember(locale) {
                java.time.format.DateTimeFormatter.ofPattern(android.text.format.DateFormat.getBestDateTimePattern(locale, "MMMd"), locale)
            }
            if (!first) HorizontalDivider(color = c.line)
            first = false
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp),
                modifier = Modifier.semantics(mergeDescendants = true) {}) {
                Icon(KeloIcons.Clock, contentDescription = null, tint = c.muted, modifier = Modifier.padding(top = 2.dp).size(16.dp))
                Text(
                    androidx.compose.ui.text.buildAnnotatedString {
                        pushStyle(androidx.compose.ui.text.SpanStyle(fontWeight = FontWeight.SemiBold))
                        append(stringResource(R.string.briefing_coming_up))
                        pop()
                        append("  ")
                        append(BriefingRules.aheadLine(card) { it.format(dayFormat) })
                    },
                    style = MaterialTheme.typography.bodyMedium, color = c.ink,
                )
            }
        }
        if (card.missed.isNotEmpty()) {
            if (!first) HorizontalDivider(color = c.line)
            Section(KeloIcons.Bell, stringResource(R.string.briefing_missed)) {
                card.missed.forEach { m ->
                    val tap = BriefingRules.tap(m.open)
                    Item(
                        title = m.title, lead = null, kind = stringResource(BriefingRules.kindLabel(m.kind)),
                        detail = m.detail.takeIf { it.isNotBlank() }, source = m.source.takeIf { it.isNotBlank() },
                        onClick = tap?.let { t -> { onTap(t) } },
                    )
                }
            }
        }
        val steps = BriefingRules.suggestions(card)
        if (steps.isNotEmpty()) {
            if (!first) HorizontalDivider(color = c.line)
            first = false
            Section(KeloIcons.Spark, stringResource(R.string.briefing_steps)) {
                steps.forEach { st -> StepButton(st.text) { onTap(BriefingTap.Suggest(st.text)) } }
            }
        }
        card.hours?.takeIf { BriefingRules.hoursShown(it) }?.let { h -> HoursLine(h, onHours) }
    }
}

@Composable
private fun Section(icon: ImageVector, title: String, content: @Composable () -> Unit) {
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Icon(icon, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
            Text(title, style = MaterialTheme.typography.labelLarge, color = c.muted, modifier = Modifier.semantics { heading() })
        }
        content()
    }
}

@Composable
private fun Bullet(text: String) {
    val c = Kelo.colors
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("•", style = MaterialTheme.typography.bodyMedium, color = c.accent)
        Text(text, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
    }
}

/** A small reference chip; its whole label wraps (never cut), tappable only when the item has a place. */
@Composable
private fun RefChip(label: String, onClick: (() -> Unit)?) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(10.dp)
    Text(
        label, style = MaterialTheme.typography.labelMedium, color = if (onClick != null) c.accent else c.muted,
        modifier = Modifier.clip(shape).border(1.dp, c.line, shape)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(horizontal = 10.dp, vertical = 5.dp),
    )
}

/** A suggested next step: the full question, wrapping over as many lines as it needs. */
@Composable
private fun StepButton(text: String, onClick: () -> Unit) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(12.dp)
    Row(
        Modifier.fillMaxWidth().clip(shape).background(c.accent.copy(alpha = 0.08f)).border(1.dp, c.line, shape)
            .clickable(role = Role.Button, onClick = onClick).padding(horizontal = 12.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(text, style = MaterialTheme.typography.bodyMedium, color = c.ink, modifier = Modifier.weight(1f))
        Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
    }
}

/** One event or missed item: [lead] (a time) or [kind] (a small label) before the title; tappable when it has a place. */
@Composable
private fun Item(title: String, lead: String?, detail: String?, onClick: (() -> Unit)?, kind: String? = null, source: String? = null) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(10.dp)
    Row(
        Modifier.fillMaxWidth().clip(shape)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
            kind?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = c.accent) }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                lead?.let { Text(it, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium, color = c.ink) }
                Text(title, style = MaterialTheme.typography.bodyMedium, color = c.ink, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f))
            }
            detail?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 2, overflow = TextOverflow.Ellipsis) }
            source?.let { SourceTag(it) }
        }
        if (onClick != null) Icon(KeloIcons.ChevronRight, contentDescription = null, tint = c.muted, modifier = Modifier.size(16.dp))
    }
}

/** Which account an event or mail is from: a small label under it, shown only when more than one account is read. */
@Composable
private fun SourceTag(label: String) {
    val c = Kelo.colors
    Text(
        label, style = MaterialTheme.typography.labelSmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.padding(top = 2.dp).clip(RoundedCornerShape(6.dp)).background(c.line.copy(alpha = 0.45f)).padding(horizontal = 6.dp, vertical = 1.dp),
    )
}

/** "요즘 주말엔 보통 오전 10:00쯤 일어나세요. …" with 바꾸기 / 그대로; after an answer, one line saying what was chosen. */
@Composable
private fun HoursLine(h: BriefingHours, onHours: (BriefingHours, Boolean) -> Unit) {
    val c = Kelo.colors
    val haptics = rememberHaptics()
    var done by remember(h.id) { mutableStateOf<Boolean?>(null) }
    val start = ContactHoursRules.minutes(h.wakeStart)!!.let { clockText(it / 60, it % 60) }
    val weekend = h.dayType == ContactHoursRules.WEEKEND
    HorizontalDivider(color = c.line)
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text(
            stringResource(if (weekend) R.string.briefing_hours_weekend else R.string.briefing_hours_weekday, start),
            style = MaterialTheme.typography.bodyMedium, color = c.ink,
        )
        val d = done
        if (d == null) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                SecondaryButton(stringResource(R.string.briefing_hours_keep), modifier = Modifier.weight(1f), onClick = {
                    haptics.reject(); done = false; onHours(h, false)
                })
                SmallPrimary(stringResource(R.string.briefing_hours_change), Modifier.weight(1f)) { haptics.confirm(); done = true; onHours(h, true) }
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(if (d) KeloIcons.Check else KeloIcons.Minus, contentDescription = null, tint = if (d) c.accent else c.muted, modifier = Modifier.size(16.dp))
                Text(
                    if (d) stringResource(R.string.hours_suggest_accepted, chat.kelo.app.my.hoursSpanText(h.wakeStart, h.wakeEnd))
                    else stringResource(R.string.hours_suggest_kept),
                    style = MaterialTheme.typography.bodySmall, color = c.ink,
                )
            }
        }
    }
}
