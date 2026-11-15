// Ported from android/kelo .../agent/AgentFormat.kt (keep in step with it).
package chat.kelo.desktop.pages.work

import androidx.compose.runtime.Composable
import chat.kelo.app.R
import chat.kelo.app.agent.*
import chat.kelo.app.work.*
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.Badge
import chat.kelo.desktop.ui.Tag
import androidx.compose.runtime.collectAsState
import java.time.Instant
import java.time.LocalDate
import java.time.LocalTime
import java.time.YearMonth
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle
import java.time.format.TextStyle
import java.util.Locale

/* Dates, times and schedules in the app's language (the words come from strings.xml, the shapes from the locale). */

/**
 * The phone asks Android for the locale's best pattern for a skeleton ("MMMdEEE"); the JVM has no skeletons, so the pattern is
 * built from the locale's medium date (its order and separators): the year taken out for a skeleton without one, the short
 * weekday added for one with it (after the date in Korean, Japanese and Chinese, before it elsewhere).
 */
private fun pattern(locale: Locale, skeleton: String): DateTimeFormatter =
    DateTimeFormatter.ofPattern(SkeletonPattern.of(locale, skeleton), locale)

internal object SkeletonPattern {
    private val CJK = setOf("ko", "ja", "zh")

    fun of(locale: Locale, skeleton: String): String {
        if (skeleton == "yMMMM") return when (locale.language) { "ko" -> "y년 M월"; "ja", "zh" -> "y年M月"; else -> "LLLL y" }
        // the shapes Android gives these languages ("11월 8일 (일)", "11月8日(日)")
        val year = 'y' in skeleton
        val day = 'E' in skeleton
        when (locale.language) {
            "ko" -> return (if (year) "y년 " else "") + "MMM d일" + (if (day) " (EEE)" else "")
            "ja" -> return (if (year) "y年" else "") + "MMMd日" + (if (day) "(EEE)" else "")
            "zh" -> return (if (year) "y年" else "") + "MMMd日" + (if (day) "EEE" else "")
        }
        var p = java.time.format.DateTimeFormatterBuilder.getLocalizedDateTimePattern(FormatStyle.MEDIUM, null, java.time.chrono.IsoChronology.INSTANCE, locale)
        if ('y' !in skeleton) p = withoutYear(p)
        if ('E' in skeleton) p = if (locale.language in CJK) "$p (EEE)" else "EEE, $p"
        return p
    }

    /** "y. M. d." -> "M. d.", "MMM d, y" -> "MMM d", "dd.MM.y" -> "dd.MM", "y년 M월 d일" -> "M월 d일" */
    fun withoutYear(p: String): String {
        val m = Regex("y+(년|年|\\s?г\\.)?").find(p) ?: return p
        var start = m.range.first
        var end = m.range.last + 1
        if (p.substring(0, start).isBlank()) { while (end < p.length && p[end] in " ,./-") end++ }
        else { while (start > 0 && p[start - 1] in " ,./-") start-- }
        return p.removeRange(start, end).trim().ifEmpty { "MMM d" }
    }
}

private fun zoned(seconds: Double) = Instant.ofEpochMilli((seconds * 1000).toLong()).atZone(ZoneId.systemDefault())

/** "오후 3:05" */
@Composable
fun clockText(seconds: Double): String = DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(appLocale()).format(zoned(seconds))

@Composable
fun clockText(hour: Int, minute: Int): String =
    DateTimeFormatter.ofLocalizedTime(FormatStyle.SHORT).withLocale(appLocale()).format(LocalTime.of(hour, minute))

/** "11월 8일 (일)" */
@Composable
fun dayText(date: LocalDate): String = pattern(appLocale(), "MMMdEEE").format(date)

/** "2026년 11월 8일 (일)" */
@Composable
fun fullDayText(date: LocalDate): String = pattern(appLocale(), "yMMMdEEE").format(date)

/** "11월 8일 (일) 오전 9:00" */
@Composable
fun dayClockText(seconds: Double): String {
    val z = zoned(seconds)
    return stringResource(R.string.agent_day_time, dayText(z.toLocalDate()), clockText(seconds))
}

/** "2026년 11월" */
@Composable
fun monthText(month: YearMonth): String = pattern(appLocale(), "yMMMM").format(month.atDay(1))

@Composable
fun dayLabel(label: DayGroups.Label): String = when (label) {
    DayGroups.Label.Today -> stringResource(R.string.agent_today)
    DayGroups.Label.Yesterday -> stringResource(R.string.agent_yesterday)
    is DayGroups.Label.Date -> if (label.date.year == LocalDate.now().year) dayText(label.date) else fullDayText(label.date)
    DayGroups.Label.Unknown -> stringResource(R.string.agent_day_unknown)
}

/** "8시간 뒤" / "3분 전" / "곧" / "방금" */
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

/** A weekday's short name in the app's language ("월"). */
@Composable
fun weekdayShort(cronDay: Int): String = CronBuilder.dayOfWeek(cronDay).getDisplayName(TextStyle.SHORT, appLocale())

/** What a schedule's repeat means, in words ("매일 오전 9:00"); a cron the app has no words for uses the server's sentence (never the expression). */
@Composable
fun repeatText(r: Repeat, serverWords: String = ""): String = when (r) {
    is Repeat.Daily -> stringResource(R.string.agent_repeat_daily_at, clockText(r.hour, r.minute))
    is Repeat.Weekdays -> stringResource(R.string.agent_repeat_weekdays_at, clockText(r.hour, r.minute))
    is Repeat.Weekly -> if (r.days.map { it % 7 }.toSet() == setOf(0, 6)) stringResource(R.string.agent_repeat_weekends_at, clockText(r.hour, r.minute))
    else stringResource(
        R.string.agent_repeat_weekly_at,
        CronBuilder.PICKER_DAYS.filter { it in r.days }.map { weekdayShort(it) }.joinToString(stringResource(R.string.agent_list_separator)),
        clockText(r.hour, r.minute),
    )
    is Repeat.Monthly -> stringResource(R.string.agent_repeat_monthly_at, r.day, clockText(r.hour, r.minute))
    is Repeat.Yearly -> stringResource(R.string.agent_repeat_yearly_at, monthDayText(r.month, r.day), clockText(r.hour, r.minute))
    is Repeat.EveryHours -> if (r.hours <= 1) stringResource(R.string.agent_repeat_hourly_at, r.minute)
        else if (r.minute == 0) pluralStringResource(R.plurals.agent_repeat_every_hours, r.hours, r.hours)
        else stringResource(R.string.agent_repeat_every_hours_at, pluralStringResource(R.plurals.agent_repeat_every_hours, r.hours, r.hours), r.minute)
    is Repeat.Once -> stringResource(R.string.agent_repeat_once_at, fullDayText(r.date), clockText(r.hour, r.minute))
    is Repeat.Interval -> intervalText(r.minutes)
    // never the cron expression itself: the server's sentence, else a plain "repeats on a set rule"
    is Repeat.Custom -> serverWords.takeIf { it.isNotBlank() && !CronBuilder.looksLikeCron(it) } ?: stringResource(R.string.sched_text_rule)
}

/** Whether the app's language writes times on a 24-hour clock (the time pickers follow it, like [clockText]). */
@Composable
fun uses24Hour(): Boolean = java.time.format.DateTimeFormatterBuilder.getLocalizedDateTimePattern(null, FormatStyle.SHORT, java.time.chrono.IsoChronology.INSTANCE, appLocale())
    .let { 'H' in it || 'k' in it }

/** "11월 8일" (no year, no weekday) */
@Composable
fun monthDayText(month: Int, day: Int): String =
    pattern(appLocale(), "MMMd").format(java.time.MonthDay.of(month, day.coerceIn(1, java.time.Month.of(month).maxLength())).atYear(2024))

/** A repeat's words with its first and last day ("매일 오전 9:00, 12월 31일까지"); a one-time run has neither. */
@Composable
fun withDays(words: String, startsOn: LocalDate?, endsOn: LocalDate?, today: LocalDate = LocalDate.now()): String {
    var out = words
    if (startsOn != null && startsOn.isAfter(today)) out = stringResource(R.string.sched_text_from, out, dayOrFullText(startsOn, today))
    if (endsOn != null) out = stringResource(R.string.sched_text_until, out, dayOrFullText(endsOn, today))
    return out
}

@Composable
internal fun dayOrFullText(d: LocalDate, today: LocalDate): String = if (d.year == today.year) dayText(d) else fullDayText(d)

/** How a saved schedule runs, in words: the same in the agenda and on its page. A cron the app has no words for uses the server's. */
@Composable
fun scheduleText(s: Schedule): String {
    val repeat = CronBuilder.fromSchedule(s)
    val words = repeatText(repeat, s.description)
    // the server's own description already says its first and last day
    return if (repeat is Repeat.Custom || repeat is Repeat.Once) words else withDays(words, ScheduleDays.parse(s.startsOn), ScheduleDays.parse(s.endsOn))
}

/** The simple form's choice in words, as the agenda will show it once saved. */
@Composable
fun planText(p: SchedulePlan, now: java.time.LocalDateTime = java.time.LocalDateTime.now()): String {
    val words = repeatText(PlanRules.asRepeat(p))
    if (p.freq == Freq.NONE) return words
    return withDays(words, ScheduleDays.parse(PlanRules.startsOn(p, now)), p.until, now.toLocalDate())
}

/** "30분마다" / "2시간마다" / "하루마다" */
@Composable
fun intervalText(minutes: Int): String = when {
    minutes % 1440 == 0 -> pluralStringResource(R.plurals.work_every_days, minutes / 1440, minutes / 1440)
    minutes % 60 == 0 -> pluralStringResource(R.plurals.agent_repeat_every_hours, minutes / 60, minutes / 60)
    else -> pluralStringResource(R.plurals.work_every_minutes, minutes, minutes)
}
