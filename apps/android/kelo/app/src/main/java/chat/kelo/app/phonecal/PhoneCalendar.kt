package chat.kelo.app.phonecal

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.CalendarContract
import android.util.Log
import androidx.core.content.ContextCompat
import chat.kelo.app.appContainer
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import java.time.ZoneId

/** Whether the phone calendar is shared from this phone, and how the last send went (not secret: a flag, a time and a count). */
class PhoneCalendarPrefs(context: Context) {
    private val p = context.getSharedPreferences("kelo_phonecal", Context.MODE_PRIVATE)

    var enabled: Boolean
        get() = p.getBoolean("enabled", false)
        set(v) = p.edit().putBoolean("enabled", v).apply()

    /** Turned off while the server could not be reached: its copy is deleted at the next chance. */
    var wipePending: Boolean
        get() = p.getBoolean("wipe", false)
        set(v) = p.edit().putBoolean("wipe", v).apply()

    /** The user said no to the calendar permission when turning it on (the card keeps saying so until it is allowed) */
    var denied: Boolean
        get() = p.getBoolean("denied", false)
        set(v) = p.edit().putBoolean("denied", v).apply()

    /** "일정 추가·수정도 허용": Kelo may add and change events (each one approved on a card), in [writeCalendar]. Off by default. */
    var writeAllowed: Boolean
        get() = p.getBoolean("write", false)
        set(v) = p.edit().putBoolean("write", v).apply()

    /** The calendar Kelo writes to (CalendarContract.Calendars._ID); -1 = the phone's primary one. */
    var writeCalendar: Long
        get() = p.getLong("write.calendar", -1)
        set(v) = p.edit().putLong("write.calendar", v).apply()

    val lastAt: Long get() = p.getLong("last.at", 0)
    val lastCount: Int get() = p.getInt("last.count", 0)

    fun sent(at: Long, count: Int) = p.edit().putLong("last.at", at).putInt("last.count", count).apply()
    fun clear() = p.edit().remove("last.at").remove("last.count").apply()
}

/** One calendar on the phone that Kelo could write to. */
data class PhoneCalendarInfo(val id: Long, val name: String, val account: String, val primary: Boolean = false, val visible: Boolean = true)

/** What the switch shows. */
data class PhoneCalendarStatus(val busy: Boolean = false, val lastAt: Long = 0, val count: Int = 0, val failed: Boolean = false)

/**
 * Reads the phone's calendar and keeps the user's server up to date (PUT /api/phone-calendar). Sends on app open/resume (at most every
 * few minutes), a few seconds after the phone's calendar changes (a ContentObserver, while the app's process lives) and from the
 * 15-minute background job. Read-only unless the user also allowed writing: then approved changes from Kelo are made by
 * chat.kelo.app.device.DeviceTasks in the calendar the user picked.
 */
object PhoneCalendarSync {
    private const val TAG = "KeloPhoneCal"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Mutex()
    private var observer: ContentObserver? = null
    private var pending: Job? = null
    private val _status = MutableStateFlow(PhoneCalendarStatus())
    val status: StateFlow<PhoneCalendarStatus> = _status.asStateFlow()

    fun permitted(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED

    fun writePermitted(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_CALENDAR) == PackageManager.PERMISSION_GRANTED

    /** The phone's calendars the user may pick to write to (ones it can change), primary first. */
    fun writableCalendars(context: Context): List<PhoneCalendarInfo> {
        if (!permitted(context)) return emptyList()
        val out = mutableListOf<PhoneCalendarInfo>()
        val cols = arrayOf(
            CalendarContract.Calendars._ID, CalendarContract.Calendars.CALENDAR_DISPLAY_NAME, CalendarContract.Calendars.ACCOUNT_NAME,
            CalendarContract.Calendars.CALENDAR_ACCESS_LEVEL, CalendarContract.Calendars.IS_PRIMARY, CalendarContract.Calendars.VISIBLE,
        )
        runCatching {
            context.contentResolver.query(CalendarContract.Calendars.CONTENT_URI, cols, null, null, null)?.use { c ->
                while (c.moveToNext()) {
                    val access = c.getInt(3)
                    if (access < CalendarContract.Calendars.CAL_ACCESS_CONTRIBUTOR) continue
                    out += PhoneCalendarInfo(c.getLong(0), c.getString(1).orEmpty(), c.getString(2).orEmpty(), primary = c.getInt(4) == 1, visible = c.getInt(5) == 1)
                }
            }
        }
        return out.sortedWith(compareByDescending<PhoneCalendarInfo> { it.primary }.thenByDescending { it.visible }.thenBy { it.name })
    }

    /** Where Kelo writes: the picked calendar when it still exists, else the primary/first writable one; null = read-only. */
    fun writeTarget(context: Context): Pair<Long, String>? {
        val prefs = prefs(context)
        if (!prefs.writeAllowed || !writePermitted(context)) return null
        val cals = writableCalendars(context)
        val pick = cals.firstOrNull { it.id == prefs.writeCalendar } ?: cals.firstOrNull() ?: return null
        return pick.id to pick.name.ifBlank { pick.account }
    }

    fun prefs(context: Context): PhoneCalendarPrefs = context.applicationContext.appContainer.phoneCalendarPrefs

    private fun api(context: Context): KeloApi? = context.applicationContext.appContainer.sessions.api.value

    /** Sends the snapshot now when the feature is on and allowed. [force] = false skips it when the last send is only minutes old. */
    suspend fun sync(context: Context, force: Boolean = true): Boolean {
        val app = context.applicationContext
        val prefs = prefs(app)
        if (!prefs.enabled && prefs.wipePending) {
            api(app)?.let { a -> if (runCatching { a.delete<JsonElement>("/api/phone-calendar") }.isSuccess) prefs.wipePending = false }
        }
        if (!prefs.enabled || !permitted(app)) return false
        val api = api(app) ?: return false
        if (!force && !PhoneCalendarRules.dueOnResume(prefs.lastAt, System.currentTimeMillis())) return true
        return lock.withLock {
            _status.value = _status.value.copy(busy = true)
            try {
                val zone = ZoneId.systemDefault()
                val window = PhoneCalendarRules.window(System.currentTimeMillis(), zone)
                val events = withContext(Dispatchers.IO) { PhoneCalendarRules.clean(read(app, window), zone) }
                val body = PhoneCalendarRules.snapshot(events, window, zone, app.appContainer.deviceName(), writeTarget(app))
                api.put<JsonElement>("/api/phone-calendar", body)
                val now = System.currentTimeMillis()
                prefs.sent(now, events.size)
                _status.value = PhoneCalendarStatus(lastAt = now, count = events.size)
                true
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                if (e !is ApiError) Log.w(TAG, "phone calendar not sent", e)
                _status.value = _status.value.copy(busy = false, failed = true)
                false
            }
        }
    }

    /** Turned on: remember it, watch for changes and send at once. */
    suspend fun turnOn(context: Context): Boolean {
        prefs(context).enabled = true
        prefs(context).wipePending = false
        watch(context)
        return sync(context, force = true)
    }

    /** Turned off: stop watching and wipe what the server keeps for this phone. */
    suspend fun turnOff(context: Context): Boolean {
        val prefs = prefs(context)
        prefs.enabled = false
        unwatch(context)
        prefs.clear()
        prefs.writeAllowed = false
        _status.value = PhoneCalendarStatus()
        val ok = api(context)?.let { a -> runCatching { a.delete<JsonElement>("/api/phone-calendar") }.isSuccess } ?: false
        prefs.wipePending = !ok
        return ok
    }

    /** Starts the ContentObserver when the feature is on (called when the app starts and when it is turned on). */
    fun watch(context: Context) {
        val app = context.applicationContext
        if (observer != null || !prefs(app).enabled || !permitted(app)) return
        val o = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                pending?.cancel()
                pending = scope.launch {
                    delay(4_000)   // one send for a burst of changes (a sync account writes many rows at once)
                    sync(app, force = true)
                }
            }
        }
        runCatching { app.contentResolver.registerContentObserver(CalendarContract.CONTENT_URI, true, o) }
            .onSuccess { observer = o }
    }

    fun unwatch(context: Context) {
        observer?.let { runCatching { context.applicationContext.contentResolver.unregisterContentObserver(it) } }
        observer = null
        pending?.cancel()
    }

    /** Fire and forget, for the app coming to the front. */
    fun syncSoon(context: Context, force: Boolean = false) {
        scope.launch { sync(context, force) }
    }

    /** The occurrences in the window (recurring events expanded by the Instances table), with the number of people invited. */
    private fun read(context: Context, window: Pair<Long, Long>): List<PhoneEvent> {
        val cr = context.contentResolver
        val uri = CalendarContract.Instances.CONTENT_URI.buildUpon().also {
            android.content.ContentUris.appendId(it, window.first)
            android.content.ContentUris.appendId(it, window.second)
        }.build()
        val cols = arrayOf(
            CalendarContract.Instances.EVENT_ID, CalendarContract.Instances.TITLE, CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END, CalendarContract.Instances.ALL_DAY, CalendarContract.Instances.EVENT_LOCATION,
            CalendarContract.Instances.CALENDAR_DISPLAY_NAME, CalendarContract.Instances.SELF_ATTENDEE_STATUS,
            CalendarContract.Instances.STATUS,
        )
        val rows = mutableListOf<PhoneEvent>()
        cr.query(uri, cols, "${CalendarContract.Instances.VISIBLE} = 1", null, "${CalendarContract.Instances.BEGIN} ASC")?.use { c ->
            while (c.moveToNext() && rows.size < PhoneCalendarRules.MAX_EVENTS * 2) {
                rows += PhoneEvent(
                    eventId = c.getLong(0), title = c.getString(1).orEmpty(), beginMs = c.getLong(2), endMs = c.getLong(3),
                    allDay = c.getInt(4) == 1, location = c.getString(5).orEmpty(), calendar = c.getString(6).orEmpty(),
                    selfStatus = if (c.isNull(7)) 0 else c.getInt(7), status = if (c.isNull(8)) 0 else c.getInt(8),
                )
            }
        }
        val counts = attendeeCounts(context, rows.map { it.eventId }.distinct())
        return rows.map { it.copy(attendees = counts[it.eventId] ?: 0) }
    }

    private fun attendeeCounts(context: Context, ids: List<Long>): Map<Long, Int> {
        val out = HashMap<Long, Int>()
        ids.chunked(400).forEach { chunk ->
            val sel = "${CalendarContract.Attendees.EVENT_ID} IN (${chunk.joinToString(",")})"
            runCatching {
                context.contentResolver.query(CalendarContract.Attendees.CONTENT_URI, arrayOf(CalendarContract.Attendees.EVENT_ID), sel, null, null)
                    ?.use { c -> while (c.moveToNext()) out.merge(c.getLong(0), 1, Int::plus) }
            }
        }
        return out
    }
}
