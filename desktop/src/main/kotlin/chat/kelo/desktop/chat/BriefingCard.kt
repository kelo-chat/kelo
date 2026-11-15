package chat.kelo.desktop.chat

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
import androidx.compose.ui.input.pointer.PointerIcon
import androidx.compose.ui.input.pointer.pointerHoverIcon
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.chat.BriefingCard
import chat.kelo.app.chat.BriefingHours
import chat.kelo.app.chat.BriefingRules
import chat.kelo.app.chat.BriefingTap
import chat.kelo.app.my.ContactHoursRules
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.pages.work.clockText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton

// The morning briefing card of android/kelo .../chat/BriefingCard.kt, drawn the same here (its rules are in
// chat.kelo.app.chat.BriefingRules). On a computer an event opens nothing (there is no phone calendar to open); every other item
// opens its place in the app. Keep in step with it.

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
            val clocks = card.events.associate { it.start to clockText(it.start) }   // clockText is composable: written here, not in the lambda
            Section(KeloIcons.Spark, stringResource(R.string.briefing_connections)) {
                notes.forEach { n ->
                    // an event opens nothing on a computer: its chip is shown, not clickable
                    val chips = BriefingRules.chips(card, n.refs, weatherLabel) { clocks[it].orEmpty() }
                    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        Bullet(n.text)
                        if (chips.isNotEmpty()) {
                            FlowRow(Modifier.padding(start = 16.dp), horizontalArrangement = Arrangement.spacedBy(6.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                chips.forEach { ch ->
                                    val tap = ch.tap?.takeIf { it !is BriefingTap.Event }
                                    RefChip(ch.label, tap?.let { t -> { onTap(t) } })
                                }
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
                        modifier = if (w.attributionUrl.isNotBlank()) Modifier.clickable(role = Role.Button) { onLink(w.attributionUrl) }.pointerHoverIcon(PointerIcon.Hand) else Modifier,
                    )
                }
            }
        }
        if (card.events.isNotEmpty()) {
            if (!first) HorizontalDivider(color = c.line)
            first = false
            Section(KeloIcons.Calendar, stringResource(R.string.briefing_today)) {
                card.events.forEach { e ->
                    // a computer has no phone calendar to open at that time: the event is shown, not a link
                    val tap = BriefingRules.tap(e.open)?.takeIf { it !is BriefingTap.Event }
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
            val locale = chat.kelo.desktop.pages.my.appLocale()
            val dayFormat = androidx.compose.runtime.remember(locale) {
                java.time.format.DateTimeFormatter.ofPattern(chat.kelo.desktop.pages.work.SkeletonPattern.of(locale, "MMMd"), locale)
            }
            if (!first) HorizontalDivider(color = c.line)
            first = false
            Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
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

/** A small reference chip; its whole label wraps (never cut), clickable only when the item has a place. */
@Composable
private fun RefChip(label: String, onClick: (() -> Unit)?) {
    val c = Kelo.colors
    val shape = RoundedCornerShape(10.dp)
    Text(
        label, style = MaterialTheme.typography.labelMedium, color = if (onClick != null) c.accent else c.muted,
        modifier = Modifier.clip(shape).border(1.dp, c.line, shape)
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick).pointerHoverIcon(PointerIcon.Hand) else Modifier)
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
            .clickable(role = Role.Button, onClick = onClick).pointerHoverIcon(PointerIcon.Hand).padding(horizontal = 12.dp, vertical = 8.dp),
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
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick).pointerHoverIcon(PointerIcon.Hand) else Modifier)
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
                    done = false; onHours(h, false)
                })
                PrimaryButton(stringResource(R.string.briefing_hours_change), onClick = { done = true; onHours(h, true) }, modifier = Modifier.weight(1f))
            }
        } else {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Icon(if (d) KeloIcons.Check else KeloIcons.Minus, contentDescription = null, tint = if (d) c.accent else c.muted, modifier = Modifier.size(16.dp))
                Text(
                    if (d) stringResource(R.string.hours_suggest_accepted, chat.kelo.desktop.pages.my.hoursSpanText(h.wakeStart, h.wakeEnd))
                    else stringResource(R.string.hours_suggest_kept),
                    style = MaterialTheme.typography.bodySmall, color = c.ink,
                )
            }
        }
    }
}
