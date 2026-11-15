package chat.kelo.desktop.pages.my

import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.toComposeImageBitmap
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import chat.kelo.app.R
import chat.kelo.app.agent.RelTime
import chat.kelo.app.my.NumberText
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeFormatterBuilder
import java.time.format.FormatStyle
import java.util.Locale
import kotlin.math.max

/* Numbers, sizes and times in the app's language (android/kelo .../my/Format.kt and agent/AgentFormat.kt, same rules). */

@Composable
fun appLocale(): Locale = LocalStrings.current.locale

@Composable
fun fmtNumber(n: Long): String = NumberText.whole(n, appLocale())

/** A size with its unit word around the number the shared rules wrote (android my/NumberText.kt, as the phone). */
@Composable
private fun sizeText(parts: Pair<String, String>): String = when (parts.second) {
    NumberText.GB -> stringResource(R.string.size_gb, parts.first)
    NumberText.MB -> stringResource(R.string.size_mb, parts.first)
    else -> stringResource(R.string.size_kb, parts.first)
}

@Composable
fun fileSize(n: Long): String = sizeText(NumberText.file(n, appLocale()))

@Composable
fun storageSize(n: Long): String = sizeText(NumberText.storage(n, appLocale()))

private fun instant(seconds: Double) = Instant.ofEpochMilli((seconds * 1000).toLong()).atZone(ZoneId.systemDefault())

@Composable
fun shortDay(seconds: Double): String =
    DateTimeFormatter.ofPattern(stringResource(R.string.date_short_pattern), appLocale()).format(instant(seconds))

@Composable
fun fullDate(seconds: Double): String =
    DateTimeFormatter.ofLocalizedDate(FormatStyle.MEDIUM).withLocale(appLocale()).format(instant(seconds))

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

fun cpuCount(n: Double): String = NumberText.cpu(n)

@Composable
fun clockText(hour: Int, minute: Int): String =
    DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(appLocale()).format(LocalTime.of(hour, minute))

/** A date pattern from a skeleton ("MMMdEEE") in the app's language, as Android's getBestDateTimePattern. */
private fun skeleton(locale: Locale, withYear: Boolean): DateTimeFormatter {
    val p = DateTimeFormatterBuilder.getLocalizedDateTimePattern(FormatStyle.MEDIUM, null, java.time.chrono.IsoChronology.INSTANCE, locale)
    // MEDIUM gives month, day (and year); add the weekday the way the phone shows it
    val base = if (withYear) p else p.replace(Regex("[,.\\s/-]*y+[年년.]?\\s*"), " ").trim().trimEnd(',', '.', ' ')
    return DateTimeFormatter.ofPattern("$base (EEE)", locale)
}

@Composable
fun dayText(date: LocalDate): String = runCatching { skeleton(appLocale(), false).format(date) }.getOrDefault(date.toString())

@Composable
fun fullDayText(date: LocalDate): String = runCatching { skeleton(appLocale(), true).format(date) }.getOrDefault(date.toString())

@Composable
fun relText(seconds: Double, nowSeconds: Double = System.currentTimeMillis() / 1000.0): String {
    val r = RelTime.of(seconds, nowSeconds)
    val n = r.amount.toInt()
    return when (r.unit) {
        RelTime.Unit.NOW -> stringResource(if (r.future) R.string.agent_rel_soon else R.string.agent_rel_now)
        RelTime.Unit.MINUTES -> pluralStringResource(if (r.future) R.plurals.agent_rel_minutes_future else R.plurals.agent_rel_minutes_past, n, n)
        RelTime.Unit.HOURS -> pluralStringResource(if (r.future) R.plurals.agent_rel_hours_future else R.plurals.agent_rel_hours_past, n, n)
        RelTime.Unit.DAYS -> pluralStringResource(if (r.future) R.plurals.agent_rel_days_future else R.plurals.agent_rel_days_past, n, n)
    }
}

/** "30분 뒤" / "2시간 뒤" / "1일 뒤" */
@Composable
fun laterText(minutes: Int): String = when {
    minutes >= 1440 && minutes % 1440 == 0 -> pluralStringResource(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440)
    minutes >= 60 && minutes % 60 == 0 -> pluralStringResource(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60)
    else -> pluralStringResource(R.plurals.agent_rel_minutes_future, minutes, minutes)
}

/** [template] with its %1$s in bold (or as code). */
@Composable
fun emphasized(template: String, arg: String, code: Boolean = false): AnnotatedString {
    val c = Kelo.colors
    val at = template.indexOf("%1\$s")
    return buildAnnotatedString {
        if (at < 0) { append(template); return@buildAnnotatedString }
        append(template.substring(0, at))
        val style = if (code) SpanStyle(fontFamily = FontFamily.Monospace, background = c.bubble) else SpanStyle(fontWeight = FontWeight.Bold, color = c.ink)
        withStyle(style) { append(arg) }
        append(template.substring(at + 4))
    }
}

/** "$0.19", "¥12.30" in the app's language, as the phone (it used the computer's language, so a Korean app on an English
 *  Windows wrote money the English way): two decimals, the currency's own symbol. */
@Composable
fun money(v: Double, currency: String): String = NumberText.money(v, currency, appLocale())

/** Decodes a PNG/JPEG/WebP into a picture (null when it is not one). */
fun decodeImage(bytes: ByteArray): androidx.compose.ui.graphics.ImageBitmap? =
    runCatching { org.jetbrains.skia.Image.makeFromEncoded(bytes).toComposeImageBitmap() }.getOrNull()
