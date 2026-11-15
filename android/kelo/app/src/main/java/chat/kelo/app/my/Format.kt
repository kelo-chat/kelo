package chat.kelo.app.my

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import chat.kelo.app.R
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.util.Locale
import kotlin.math.max

/* Numbers, sizes and times in the app's language. */

@Composable
@ReadOnlyComposable
fun appLocale(): Locale = LocalConfiguration.current.locales[0] ?: Locale.getDefault()

/** 1234567 -> "1,234,567" */
@Composable
@ReadOnlyComposable
fun fmtNumber(n: Long): String = NumberText.whole(n, appLocale())

/** A size with its unit word (size_gb / size_mb / size_kb) around the number [NumberText] wrote. */
@Composable
private fun sizeText(parts: Pair<String, String>): String = when (parts.second) {
    NumberText.GB -> stringResource(R.string.size_gb, parts.first)
    NumberText.MB -> stringResource(R.string.size_mb, parts.first)
    else -> stringResource(R.string.size_kb, parts.first)
}

/** DocumentsPage kb(): "1.2MB" from 1 MB, else at least "1KB". */
@Composable
fun fileSize(n: Long): String = sizeText(NumberText.file(n, appLocale()))

/** GB with one decimal, MB with one decimal under 10 MB, else KB ([NumberText.storage]). */
@Composable
fun storageSize(n: Long): String = sizeText(NumberText.storage(n, appLocale()))

private fun instant(seconds: Double) = Instant.ofEpochMilli((seconds * 1000).toLong()).atZone(ZoneId.systemDefault())

/** "11/6" (pattern from strings: date_short_pattern). */
@Composable
fun shortDay(seconds: Double): String =
    DateTimeFormatter.ofPattern(stringResource(R.string.date_short_pattern), appLocale()).format(instant(seconds))

/** "11/6 06:00" (pattern from strings: date_time_pattern). */
@Composable
fun dayTime(millis: Long): String =
    DateTimeFormatter.ofPattern(stringResource(R.string.date_time_pattern), appLocale())
        .format(Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault()))

/** The full date in the app's language ("2026. 11. 7."). */
@Composable
fun fullDate(seconds: Double): String =
    DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(appLocale()).format(instant(seconds))

/** "11/6 06:00", or "아직 없어요". */
@Composable
fun whenText(seconds: Double?): String =
    if (seconds == null || seconds == 0.0) stringResource(R.string.vault_never) else dayTime((seconds * 1000).toLong())

/** How long ago, in words. */
@Composable
fun agoText(seconds: Double?, nowMillis: Long = System.currentTimeMillis()): String {
    if (seconds == null || seconds == 0.0) return stringResource(R.string.devices_never_used)
    val s = max(0.0, nowMillis / 1000.0 - seconds)
    return when {
        s < 120 -> stringResource(R.string.ago_now)
        s < 7200 -> stringResource(R.string.ago_minutes, (s / 60).toLong())
        s < 172800 -> stringResource(R.string.ago_hours, (s / 3600).toLong())
        else -> stringResource(R.string.ago_days, (s / 86400).toLong())
    }
}

/** A CPU count: "2" for 2.0, "1.5" otherwise. */
fun cpuCount(n: Double): String = NumberText.cpu(n)
