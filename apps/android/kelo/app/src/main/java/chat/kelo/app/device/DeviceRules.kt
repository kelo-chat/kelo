package chat.kelo.app.device

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/**
 * One row under 이 기기에서. [path]: where its data goes on the user's own server (null: the calendar, which has its own sync).
 * [permissions]: the Android permissions asked when it is turned on (the notification listener and Health Connect have their own
 * screens). [sideloadOnly]: only in the "full" build (BuildConfig.FULL_DEVICE); the sideload APK and the Play bundle lack the
 * permission or service (Play policy, and Play Protect blocks file installs that ask for SMS or notification access).
 */
enum class DeviceFeature(val key: String, val path: String?, val permissions: List<String>, val sideloadOnly: Boolean = false) {
    CALENDAR("calendar", null, listOf("android.permission.READ_CALENDAR")),
    CONTACTS("contacts", "/api/phone/contacts", listOf("android.permission.READ_CONTACTS")),
    MESSAGES("messages", "/api/phone/messages", listOf("android.permission.READ_SMS"), sideloadOnly = true),
    DIALER("dialer", "/api/phone/dialer", emptyList()),
    NOTIFICATIONS("notifications", "/api/phone/notifications", emptyList(), sideloadOnly = true),
    CALL_LOG("call_log", "/api/phone/call-log", listOf("android.permission.READ_CALL_LOG"), sideloadOnly = true),
    HEALTH("health", "/api/phone/health", emptyList());

    /** Whether it can also do something on the phone after an approval (send a text, open the dialer, change the calendar). */
    val doesTasks: Boolean get() = this == MESSAGES || this == DIALER || this == CALENDAR
}

/** A notification as the listener saw it, before the rules decide whether it goes to the server. */
data class SeenNote(
    val packageName: String, val app: String, val title: String, val text: String, val postedAt: Long,
    val ongoing: Boolean = false, val foregroundService: Boolean = false, val groupSummary: Boolean = false, val category: String? = null,
)

data class ContactRow(val name: String, val phones: List<Pair<String, String>>, val emails: List<String>, val org: String = "",
                      val title: String = "", val birthday: String = "")

data class MessageRow(val address: String, val body: String, val atMs: Long, val sent: Boolean, val mms: Boolean = false, val thread: Long = 0)

data class CallRow(val number: String, val name: String, val type: Int, val atMs: Long, val durationS: Long)

/** Raw Health Connect readings, before they become one summary per day. */
data class HealthReadings(
    val steps: Map<LocalDate, Long> = emptyMap(),
    val heart: Map<LocalDate, Triple<Double, Double, Double>> = emptyMap(),   // min, avg, max
    val sleep: List<Pair<Long, Long>> = emptyList(),                          // sessions: start, end (ms)
    val weight: List<Pair<Long, Double>> = emptyList(),                       // time (ms), kg
    val exercise: List<ExerciseRow> = emptyList(),
)

data class ExerciseRow(val type: String, val title: String, val startMs: Long, val endMs: Long)

/** Work the server queued for this phone after the user approved it (POST /api/phone/tasks/claim). */
data class DeviceTask(val id: Long, val kind: String, val fields: JsonObject) {
    fun s(k: String): String = (fields[k] as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
    fun d(k: String): Double? = (fields[k] as? JsonPrimitive)?.doubleOrNull
    fun l(k: String): Long? = (fields[k] as? JsonPrimitive)?.longOrNull ?: d(k)?.toLong()
    fun b(k: String): Boolean = (fields[k] as? JsonPrimitive)?.booleanOrNull ?: false
    fun has(k: String): Boolean = k in fields

    companion object {
        fun parseList(e: kotlinx.serialization.json.JsonElement?): List<DeviceTask> {
            val items = ((e as? JsonObject)?.get("items") as? JsonArray) ?: return emptyList()
            return items.mapNotNull { x ->
                val o = x as? JsonObject ?: return@mapNotNull null
                val id = (o["id"] as? JsonPrimitive)?.longOrNull ?: return@mapNotNull null
                val kind = (o["kind"] as? JsonPrimitive)?.content ?: return@mapNotNull null
                DeviceTask(id, kind, o)
            }
        }
    }
}

object DeviceRules {
    const val CALL_DAYS = 30
    val MESSAGE_WINDOWS = listOf(7, 30, 90)
    const val DEFAULT_MESSAGE_DAYS = 30
    const val HEALTH_DAYS = 30
    const val MAX_CONTACTS = 10_000
    const val MAX_MESSAGES = 5000
    const val MAX_BODY = 1500
    const val MAX_CALLS = 5000
    val HEALTH_TYPES = listOf("steps", "sleep", "heart_rate", "weight", "exercise")
    val DEFAULT_HEALTH = setOf("steps", "sleep")
    /** Categories that are the phone's own business, never forwarded. */
    private val SKIP_CATEGORIES = setOf("service", "sys", "transport", "progress", "status", "navigation", "stopwatch", "alarm")

    /** The rows this build shows: the sideload and Play builds leave out text messages, notifications and the call log. */
    /** [health]: whether this build has Health Connect (BuildConfig.HEALTH; the Play bundle leaves it out for now). */
    fun visible(full: Boolean, health: Boolean = true): List<DeviceFeature> =
        DeviceFeature.entries.filter { (full || !it.sideloadOnly) && (health || it != DeviceFeature.HEALTH) }

    /** Coming back to the app sends again only when the last send is older than this (a change, the job or 지금 보내기 always send). */
    fun gapMs(f: DeviceFeature): Long = when (f) {
        DeviceFeature.CONTACTS -> 6 * 3600_000L
        DeviceFeature.MESSAGES, DeviceFeature.CALL_LOG -> 10 * 60_000L
        DeviceFeature.HEALTH -> 60 * 60_000L
        DeviceFeature.DIALER -> 24 * 3600_000L
        DeviceFeature.CALENDAR, DeviceFeature.NOTIFICATIONS -> 5 * 60_000L
    }

    fun due(f: DeviceFeature, lastAt: Long, now: Long): Boolean = lastAt <= 0 || now < lastAt || now - lastAt >= gapMs(f)

    fun messageDays(saved: Int): Int = if (saved in MESSAGE_WINDOWS) saved else DEFAULT_MESSAGE_DAYS

    // ---- notifications
    fun forward(n: SeenNote, ownPackage: String, excluded: Set<String>): Boolean = when {
        n.packageName == ownPackage || n.packageName in excluded -> false
        n.ongoing || n.foregroundService || n.groupSummary -> false
        n.category != null && n.category in SKIP_CATEGORIES -> false
        n.packageName == "android" || n.packageName == "com.android.systemui" -> false
        n.title.isBlank() && n.text.isBlank() -> false
        else -> true
    }

    fun noteJson(n: SeenNote): JsonObject = buildJsonObject {
        put("app", n.app.take(60)); put("package", n.packageName.take(120)); put("title", n.title.take(200)); put("text", n.text.take(1000))
        put("at", n.postedAt / 1000.0)
    }

    fun notesBody(items: List<SeenNote>, device: String): JsonObject = buildJsonObject {
        put("device_name", device.take(40)); put("items", buildJsonArray { items.take(300).forEach { add(noteJson(it)) } })
    }

    // ---- contacts, messages, calls
    fun contactsBody(rows: List<ContactRow>, device: String): JsonObject = buildJsonObject {
        put("device_name", device.take(40))
        put("items", buildJsonArray {
            rows.asSequence().filter { it.name.isNotBlank() || it.phones.isNotEmpty() || it.emails.isNotEmpty() }.take(MAX_CONTACTS).forEach { c ->
                add(buildJsonObject {
                    put("name", c.name.take(120))
                    put("phones", buildJsonArray { c.phones.distinctBy { digits(it.first) }.take(10).forEach { (n, l) -> add(buildJsonObject { put("number", n); put("label", l) }) } })
                    put("emails", buildJsonArray { c.emails.distinct().take(10).forEach { add(JsonPrimitive(it)) } })
                    if (c.org.isNotBlank()) put("org", c.org.take(120))
                    if (c.title.isNotBlank()) put("title", c.title.take(120))
                    birthday(c.birthday)?.let { put("birthday", it) }
                })
            }
        })
    }

    /** "1990-05-08", "--05-08" (no year), or null for anything else Android may hold. */
    fun birthday(raw: String): String? {
        val s = raw.trim()
        return when {
            Regex("""\d{4}-\d{2}-\d{2}""").matches(s) -> s
            Regex("""--\d{2}-\d{2}""").matches(s) -> s
            Regex("""\d{4}\d{2}\d{2}""").matches(s) -> "${s.take(4)}-${s.substring(4, 6)}-${s.substring(6)}"
            else -> null
        }
    }

    fun messagesBody(rows: List<MessageRow>, days: Int, canSend: Boolean, device: String): JsonObject = buildJsonObject {
        put("device_name", device.take(40)); put("window_days", days); put("can_send", canSend)
        put("items", buildJsonArray {
            rows.sortedByDescending { it.atMs }.take(MAX_MESSAGES).forEach { m ->
                add(buildJsonObject {
                    put("address", m.address.take(60)); put("body", m.body.take(MAX_BODY)); put("at", m.atMs / 1000.0)
                    put("box", if (m.sent) "sent" else "inbox"); put("kind", if (m.mms) "mms" else "sms"); if (m.thread > 0) put("thread", m.thread)
                })
            }
        })
    }

    /** CallLog.Calls.TYPE as the server's words. */
    fun callType(t: Int): String = when (t) {
        1 -> "incoming"; 2 -> "outgoing"; 3 -> "missed"; 4 -> "voicemail"; 5 -> "rejected"; 6 -> "blocked"; else -> "incoming"
    }

    fun callsBody(rows: List<CallRow>, device: String): JsonObject = buildJsonObject {
        put("device_name", device.take(40)); put("window_days", CALL_DAYS)
        put("items", buildJsonArray {
            rows.sortedByDescending { it.atMs }.take(MAX_CALLS).forEach { c ->
                add(buildJsonObject {
                    put("number", c.number.take(40)); if (c.name.isNotBlank()) put("name", c.name.take(120)); put("type", callType(c.type))
                    put("at", c.atMs / 1000.0); put("duration", c.durationS)
                })
            }
        })
    }

    // ---- health
    /** One summary per local day: steps and heart rate as Health Connect added them up, sleep by the day it ended, the day's last weight. */
    fun healthDays(r: HealthReadings, types: Set<String>, zone: ZoneId): List<JsonObject> {
        fun day(ms: Long): LocalDate = Instant.ofEpochMilli(ms).atZone(zone).toLocalDate()
        val days = sortedSetOf<LocalDate>()
        if ("steps" in types) days += r.steps.filterValues { it > 0 }.keys
        if ("heart_rate" in types) days += r.heart.keys
        val sleepBy = if ("sleep" in types) r.sleep.groupBy { day(it.second) } else emptyMap()
        days += sleepBy.keys
        val weightBy = if ("weight" in types) r.weight.groupBy { day(it.first) } else emptyMap()
        days += weightBy.keys
        val exBy = if ("exercise" in types) r.exercise.groupBy { day(it.startMs) } else emptyMap()
        days += exBy.keys
        return days.map { d ->
            buildJsonObject {
                put("day", d.toString())
                if ("steps" in types) r.steps[d]?.takeIf { it > 0 }?.let { put("steps", it) }
                sleepBy[d]?.let { s ->
                    put("sleep_minutes", s.sumOf { (it.second - it.first).coerceAtLeast(0) } / 60_000)
                    val main = s.maxBy { it.second - it.first }
                    put("sleep_start", main.first / 1000.0); put("sleep_end", main.second / 1000.0)
                }
                if ("heart_rate" in types) r.heart[d]?.let { (mn, avg, mx) -> put("heart_min", mn); put("heart_avg", avg); put("heart_max", mx) }
                weightBy[d]?.maxBy { it.first }?.let { put("weight_kg", Math.round(it.second * 10) / 10.0) }
                exBy[d]?.let { list ->
                    put("exercise", buildJsonArray {
                        list.take(30).forEach { e ->
                            add(buildJsonObject {
                                put("type", e.type); if (e.title.isNotBlank()) put("title", e.title.take(80))
                                put("start", e.startMs / 1000.0); put("end", e.endMs / 1000.0); put("minutes", (e.endMs - e.startMs).coerceAtLeast(0) / 60_000)
                            })
                        }
                    })
                }
            }
        }
    }

    fun healthBody(days: List<JsonObject>, types: Set<String>, zone: ZoneId, device: String): JsonObject = buildJsonObject {
        put("device_name", device.take(40)); put("timezone", zone.id)
        put("types", buildJsonArray { HEALTH_TYPES.filter { it in types }.forEach { add(JsonPrimitive(it)) } })
        put("days", buildJsonArray { days.forEach { add(it) } })
    }

    // ---- tasks
    enum class DialWay { OPEN, NOTIFY }

    /** The dialer can be opened straight away only while Kelo is on screen; otherwise a notification opens it when tapped. */
    fun dialWay(visible: Boolean): DialWay = if (visible) DialWay.OPEN else DialWay.NOTIFY

    /** Why a task is not done on this phone (null: go ahead). The phone checks again what the server already checked. */
    fun refuse(task: DeviceTask, full: Boolean, on: (DeviceFeature) -> Boolean, smsAllowed: Boolean, calWrite: Boolean, nowS: Double): String? {
        val exp = task.d("expires_at")
        if (exp != null && exp < nowS) return "expired"
        return when (task.kind) {
            "sms" -> when {
                !full -> "this build cannot send texts"
                !on(DeviceFeature.MESSAGES) || !smsAllowed -> "sending texts is turned off on the phone"
                digits(task.s("number")).length < 7 -> "not a full phone number"
                task.s("text").isBlank() -> "empty text"
                else -> null
            }
            "dial" -> if (!on(DeviceFeature.DIALER)) "the dialer is turned off on the phone" else if (digits(task.s("number")).length < 3) "no number" else null
            "cal_add", "cal_update", "cal_delete" -> if (!on(DeviceFeature.CALENDAR) || !calWrite) "calendar changes are turned off on the phone" else null
            else -> "unknown task"
        }
    }

    fun digits(s: String): String = s.filter { it.isDigit() }

    /** The tel: number for the dialer (digits, a leading +, and nothing that could be a code). */
    fun dialNumber(raw: String): String {
        val plus = raw.trim().startsWith("+")
        return (if (plus) "+" else "") + digits(raw)
    }

    /** For an all-day event Android wants UTC midnight of the first day and of the day after the last. */
    fun allDayMillis(day: String): Long = LocalDate.parse(day).atStartOfDay(java.time.ZoneOffset.UTC).toInstant().toEpochMilli()
}
