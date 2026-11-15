package chat.kelo.app.agent

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.my.ConfirmDialog
import chat.kelo.app.my.EmptyNote
import chat.kelo.app.ui.components.ErrorText
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.MotionSpecs
import java.time.Instant
import java.time.LocalDate
import java.time.YearMonth
import java.time.ZoneOffset

/** The journal's days, newest first: each with its entries, and whether it still waits to be written (or could not be). */
object JournalDays {
    data class Day(val date: String, val entries: List<JournalEntry>, val gap: JournalGap?)

    fun days(entries: List<JournalEntry>, gaps: List<JournalGap>): List<Day> {
        val byDate = entries.groupBy { it.date }
        val gapBy = gaps.associateBy { it.date }
        return (byDate.keys + gapBy.keys).distinct().sortedDescending().map { d -> Day(d, byDate[d].orEmpty(), gapBy[d]) }
    }

    /** The list position of each day's header, counting [before] items ahead of the first day. */
    fun positions(days: List<Day>, before: Int): Map<String, Int> {
        val out = HashMap<String, Int>()
        var at = before
        days.forEach { d ->
            out[d.date] = at
            at += 1 + d.entries.size + (if (d.gap != null) 1 else 0)
        }
        return out
    }

    /** The day to scroll to for [target]: that day, else the nearest older one, else the oldest shown. */
    fun nearest(days: List<Day>, target: String): String? =
        days.firstOrNull { it.date <= target }?.date ?: days.lastOrNull()?.date
}

@Composable
internal fun EntryRow(e: JournalEntry, open: Boolean, onToggle: () -> Unit, onDelete: () -> Unit) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onToggle).animateContentSize(MotionSpecs.small()).padding(start = 16.dp, end = 4.dp, top = 12.dp, bottom = 12.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(e.title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold, color = c.ink)
            if (e.summary.isNotBlank()) {
                Text(e.summary, style = MaterialTheme.typography.bodyMedium, color = c.ink.copy(alpha = 0.85f),
                    maxLines = if (open) Int.MAX_VALUE else 2, overflow = TextOverflow.Ellipsis)
            }
        }
        IconButton(onClick = onDelete) { Icon(KeloIcons.Trash, contentDescription = stringResource(R.string.agent_journal_delete), tint = c.muted, modifier = Modifier.size(20.dp)) }
    }
}

