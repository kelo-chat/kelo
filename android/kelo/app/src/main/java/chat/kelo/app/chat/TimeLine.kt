package chat.kelo.app.chat

import android.icu.text.SimpleDateFormat
import android.icu.util.TimeZone
import android.text.format.DateFormat
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.wrapContentWidth
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.ui.theme.Kelo
import java.time.ZoneId
import java.util.Date
import java.util.Locale

/** Turns a message's time into its time line's words ([TimeLabels.label]) with this device's locale and time zone. */
class TimeLabeler(private val locale: Locale, private val zone: ZoneId, private val today: String, private val yesterday: String) {
    private val patterns = TimeLabels.Patterns(
        time = DateFormat.getBestDateTimePattern(locale, "jmm"),
        monthDayTime = DateFormat.getBestDateTimePattern(locale, "MMMdjmm"),
        fullDateTime = DateFormat.getBestDateTimePattern(locale, "yMMMdjmm"),
    )
    private val formats = HashMap<String, SimpleDateFormat>()

    fun label(atMs: Long, nowMs: Long = System.currentTimeMillis()): String =
        TimeLabels.label(atMs, nowMs, zone, patterns, today, yesterday) { pattern, at ->
            formats.getOrPut(pattern) { SimpleDateFormat(pattern, locale).apply { timeZone = TimeZone.getTimeZone(zone.id) } }.format(Date(at))
        }
}

@Composable
fun rememberTimeLabeler(): TimeLabeler {
    val locale = LocalConfiguration.current.locales[0] ?: Locale.getDefault()
    val today = stringResource(R.string.chat_time_today)
    val yesterday = stringResource(R.string.chat_time_yesterday)
    val zone = ZoneId.systemDefault()
    return remember(locale, zone, today, yesterday) { TimeLabeler(locale, zone, today, yesterday) }
}

/** The small, muted, centred time line above a message that starts a new stretch of the chat (not a bubble; wraps in any language). */
@Composable
fun TimeLine(text: String) {
    Text(
        text,
        color = Kelo.colors.muted,
        fontSize = 12.sp,
        lineHeight = 16.sp,
        textAlign = TextAlign.Center,
        modifier = Modifier
            .fillMaxWidth()
            .padding(top = 8.dp, bottom = 2.dp)
            .wrapContentWidth()
            .widthIn(max = 280.dp)
            .semantics { heading() },
    )
}
