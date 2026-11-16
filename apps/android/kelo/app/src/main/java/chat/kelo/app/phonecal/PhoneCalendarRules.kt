package chat.kelo.app.phonecal

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset

/** One occurrence read from the phone's calendar (CalendarContract.Instances), before it is cleaned for the server. */
data class PhoneEvent(
    val eventId: Long,
    val title: String,
    val beginMs: Long,
    val endMs: Long,
    val allDay: Boolean,
    val location: String = "",
    val calendar: String = "",
    /** CalendarContract.Attendees.ATTENDEE_STATUS_* of the phone's owner (0 = none) */
    val selfStatus: Int = 0,
    /** CalendarContract.Events.STATUS_* (2 = cancelled) */
    val status: Int = 0,
    val attendees: Int = 0,
)

/** What is read and sent: the window, which occurrences are left out, and the snapshot body of PUT /api/phone-calendar. */
object PhoneCalendarRules {
    const val DAYS_BACK = 7L
    const val DAYS_AHEAD = 60L
    private const val DECLINED = 2          // Attendees.ATTENDEE_STATUS_DECLINED
    private const val CANCELLED = 2         // Events.STATUS_CANCELED
    const val MAX_EVENTS = 3000
    /** Coming back to the app sends again only when the last send is older than this (a change or the background job always send). */
    const val RESUME_GAP_MS = 5 * 60 * 1000L

    /** [start, end) in milliseconds: from local midnight 7 days ago to local midnight 60 days ahead. */
    fun window(nowMs: Long, zone: ZoneId): Pair<Long, Long> {
        val today = Instant.ofEpochMilli(nowMs).atZone(zone).toLocalDate()
        return today.minusDays(DAYS_BACK).atStartOfDay(zone).toInstant().toEpochMilli() to
            today.plusDays(DAYS_AHEAD + 1).atStartOfDay(zone).toInstant().toEpochMilli()
    }

    /** Declined and cancelled ones out; all-day events (stored by Android at UTC midnight) turned into the phone's own days. */
    fun clean(rows: List<PhoneEvent>, zone: ZoneId): List<JsonObject> = rows
        .asSequence()
        .filter { it.selfStatus != DECLINED && it.status != CANCELLED }
        .distinctBy { Triple(it.eventId, it.beginMs, it.title) }
        .sortedBy { it.beginMs }
        .take(MAX_EVENTS)
        .map { e ->
            buildJsonObject {
                if (e.eventId > 0) put("id", e.eventId)   // what a change or deletion from Kelo names
                put("title", e.title.take(300))
                put("all_day", e.allDay)
                if (e.allDay) {
                    val first = Instant.ofEpochMilli(e.beginMs).atOffset(ZoneOffset.UTC).toLocalDate()
                    val last = Instant.ofEpochMilli(maxOf(e.beginMs, e.endMs - 1)).atOffset(ZoneOffset.UTC).toLocalDate()
                    put("day", first.toString())
                    put("last_day", last.toString())
                    put("start", seconds(first, zone))
                    put("end", seconds(last.plusDays(1), zone))
                } else {
                    put("start", e.beginMs / 1000.0)
                    put("end", maxOf(e.beginMs, e.endMs) / 1000.0)
                }
                if (e.location.isNotBlank()) put("location", e.location.take(300))
                if (e.calendar.isNotBlank()) put("calendar", e.calendar.take(120))
                if (e.attendees > 0) put("attendees", e.attendees)
            }
        }
        .toList()

    /** [write]: the calendar Kelo may add to and change (id, name), only when the user allowed writing; null = read-only. */
    fun snapshot(events: List<JsonObject>, window: Pair<Long, Long>, zone: ZoneId, deviceName: String, write: Pair<Long, String>? = null): JsonObject = buildJsonObject {
        put("device_name", deviceName.take(40))
        put("can_write", write != null)
        if (write != null) put("write_calendar", buildJsonObject { put("id", write.first); put("name", write.second.take(120)) })
        put("timezone", zone.id)
        put("window_start", window.first / 1000.0)
        put("window_end", window.second / 1000.0)
        put("events", buildJsonArray { events.forEach { add(it) } })
    }

    /** Whether coming back to the app should send again now. */
    fun dueOnResume(lastAt: Long, now: Long): Boolean = lastAt <= 0 || now - lastAt >= RESUME_GAP_MS || now < lastAt

    private fun seconds(day: LocalDate, zone: ZoneId): Double = day.atStartOfDay(zone).toInstant().toEpochMilli() / 1000.0
}
