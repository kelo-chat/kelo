package chat.kelo.desktop.chat

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.chat.TimeLabels
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import java.time.Instant
import java.time.ZoneId
import java.time.chrono.IsoChronology
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeFormatterBuilder
import java.time.format.FormatStyle
import java.util.Locale

/**
 * The desktop's [TimeLabels.Patterns], from the JDK's locale patterns (the JDK has no skeletons like Android's
 * getBestDateTimePattern). The medium date when it names the month in words ("MMM d, y"), else the long one (Korean "y년 M월 d일",
 * Japanese "y年M月d日": their medium dates are numbers only); the year-less pattern is the same with the year taken out.
 */
object DesktopTimePatterns {
    fun of(locale: Locale): TimeLabels.Patterns {
        fun pattern(date: FormatStyle?, time: FormatStyle?) =
            DateTimeFormatterBuilder.getLocalizedDateTimePattern(date, time, IsoChronology.INSTANCE, locale)
        val medium = pattern(FormatStyle.MEDIUM, FormatStyle.SHORT)
        val full = if (Regex("""M{3,}""").containsMatchIn(unquoted(medium))) medium else pattern(FormatStyle.LONG, FormatStyle.SHORT)
        return TimeLabels.Patterns(time = pattern(null, FormatStyle.SHORT), monthDayTime = withoutYear(full), fullDateTime = full)
    }

    /** [p] without its year field and the separator that goes with it: "MMM d, y, h:mm a" -> "MMM d, h:mm a", "y년 M월 d일 a h:mm" -> "M월 d일 a h:mm". */
    fun withoutYear(p: String): String {
        val rules = listOf(
            Regex("""^y+[^A-Za-z']*"""),                    // a year first (ko, ja, zh): with what follows it ("년 ", "年")
            Regex("""\s*'de'\s*y+"""),                      // es / pt: "d 'de' MMM 'de' y"
            Regex("""[^A-Za-z']*y+(\s*'[^']*'\.?)?"""),     // elsewhere: with the separator before it, and a word after it (ru "'г'.")
        )
        for (r in rules) {
            val m = r.find(p) ?: continue
            if (r === rules[0] || inPattern(p, m.range.first)) return (p.removeRange(m.range)).trim()
        }
        return p
    }

    private fun unquoted(p: String) = p.replace(Regex("""'[^']*'"""), "")
    /** Whether index [i] of [p] is outside a quoted literal. */
    private fun inPattern(p: String, i: Int) = p.substring(0, i).count { it == '\'' } % 2 == 0
}

/** Turns a message's time into its time line's words with the app's language and this computer's time zone. */
class TimeLabeler(private val locale: Locale, private val zone: ZoneId, private val today: String, private val yesterday: String) {
    private val patterns = DesktopTimePatterns.of(locale)
    private val formats = HashMap<String, DateTimeFormatter>()

    fun label(atMs: Long, nowMs: Long = System.currentTimeMillis()): String =
        TimeLabels.label(atMs, nowMs, zone, patterns, today, yesterday) { pattern, at ->
            formats.getOrPut(pattern) { DateTimeFormatter.ofPattern(pattern, locale).withZone(zone) }.format(Instant.ofEpochMilli(at))
        }
}

@Composable
fun rememberTimeLabeler(): TimeLabeler {
    val strings = LocalStrings.current
    val zone = ZoneId.systemDefault()
    return remember(strings, zone) {
        TimeLabeler(strings.locale, zone, strings.get(R.string.chat_time_today), strings.get(R.string.chat_time_yesterday))
    }
}

/** The small, muted, centred time line above a message that starts a new stretch of the chat (not a bubble; wraps in any language). */
@Composable
fun TimeLine(text: String) {
    Text(
        text, color = Kelo.colors.muted, fontSize = 12.sp, lineHeight = 16.sp, textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 2.dp).wrapContentWidth().widthIn(max = 320.dp).semantics { heading() },
    )
}
