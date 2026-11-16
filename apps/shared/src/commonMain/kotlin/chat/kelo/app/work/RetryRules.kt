package chat.kelo.app.work

import androidx.compose.runtime.Composable
import chat.kelo.app.agent.clockText
import chat.kelo.app.agent.dayClockText
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.string
import java.time.Instant
import java.time.ZoneId

/** When a job waiting to try again tries next: today, tomorrow, or a later day (its date is shown). */
object RetryRules {
    enum class Day { TODAY, TOMORROW, LATER }

    /** [wakeAt] and [nowSeconds] are epoch seconds; the day is the local day in [zone]. */
    fun day(wakeAt: Double, nowSeconds: Double, zone: ZoneId): Day {
        val wake = Instant.ofEpochMilli((wakeAt * 1000).toLong()).atZone(zone).toLocalDate()
        val today = Instant.ofEpochMilli((nowSeconds * 1000).toLong()).atZone(zone).toLocalDate()
        return when (wake) {
            today -> Day.TODAY
            today.plusDays(1) -> Day.TOMORROW
            else -> Day.LATER
        }
    }
}

/** "다시 시도 예정 · 오후 3:40", "다시 시도 예정 · 내일 오전 9:10" (a later day: with its date); the bare words when the time is not known. */
@Composable
fun retryText(wakeAt: Double?): String {
    if (wakeAt == null) return string(Res.string.work_job_waiting)
    val now = System.currentTimeMillis() / 1000.0
    return when (RetryRules.day(wakeAt, now, ZoneId.systemDefault())) {
        RetryRules.Day.TODAY -> string(Res.string.work_job_retry_at, clockText(wakeAt))
        RetryRules.Day.TOMORROW -> string(Res.string.work_job_retry_tomorrow, clockText(wakeAt))
        RetryRules.Day.LATER -> string(Res.string.work_job_retry_at, dayClockText(wakeAt))
    }
}
