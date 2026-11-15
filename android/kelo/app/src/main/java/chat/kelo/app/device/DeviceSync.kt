package chat.kelo.app.device

import android.content.Context
import android.content.pm.PackageManager
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.ContactsContract
import android.util.Log
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import chat.kelo.app.BuildConfig
import chat.kelo.app.appContainer
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.phonecal.PhoneCalendarSync
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.time.ZoneId

/** What a row's sheet shows about the last send. */
data class FeatureStatus(val busy: Boolean = false, val lastAt: Long = 0, val count: Int = 0, val failed: Boolean = false)

/**
 * Keeps the user's server up to date with what this phone shares (each row under 이 기기에서): on app open/resume (at most every few
 * minutes per row, see DeviceRules.gapMs), after the contacts change (while the process lives), from the 15-minute background job,
 * and with 지금 보내기. Health Connect is read only while Kelo is on screen (no background-read permission is asked). Turning a row
 * off deletes what the server keeps for this phone. The calendar row is chat.kelo.app.phonecal.PhoneCalendarSync.
 */
object DeviceSync {
    private const val TAG = "KeloDevice"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val locks = DeviceFeature.entries.associateWith { Mutex() }
    private val _status = MutableStateFlow(DeviceFeature.entries.associateWith { FeatureStatus() })
    val status: StateFlow<Map<DeviceFeature, FeatureStatus>> = _status.asStateFlow()
    private var contactsObserver: ContentObserver? = null
    private var pendingContacts: Job? = null

    fun prefs(context: Context): DevicePrefs = context.applicationContext.appContainer.devicePrefs
    private fun api(context: Context): KeloApi? = context.applicationContext.appContainer.sessions.api.value
    private fun visible(context: Context): Boolean = context.applicationContext.appContainer.visibility.visible.value

    fun shown(): List<DeviceFeature> = DeviceRules.visible(BuildConfig.FULL_DEVICE, BuildConfig.HEALTH)

    fun on(context: Context, f: DeviceFeature): Boolean =
        if (f == DeviceFeature.CALENDAR) PhoneCalendarSync.prefs(context).enabled else prefs(context).on(f) && (BuildConfig.FULL_DEVICE || !f.sideloadOnly) &&
            (BuildConfig.HEALTH || f != DeviceFeature.HEALTH)

    /** The permission (or access) this row needs is there right now. */
    fun permitted(context: Context, f: DeviceFeature): Boolean = when (f) {
        DeviceFeature.CALENDAR -> PhoneCalendarSync.permitted(context)
        DeviceFeature.NOTIFICATIONS -> NotificationManagerCompat.getEnabledListenerPackages(context).contains(context.packageName)
        DeviceFeature.HEALTH -> prefs(context).healthGranted && HealthReader.available(context)
        DeviceFeature.DIALER -> true
        else -> f.permissions.all { ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED }
    }

    fun has(context: Context, permission: String): Boolean =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED

    fun lastAt(context: Context, f: DeviceFeature): Long =
        if (f == DeviceFeature.CALENDAR) maxOf(PhoneCalendarSync.status.value.lastAt, PhoneCalendarSync.prefs(context).lastAt) else maxOf(status.value[f]?.lastAt ?: 0, prefs(context).lastAt(f))

    private fun set(f: DeviceFeature, s: (FeatureStatus) -> FeatureStatus) = _status.update { m -> m + (f to s(m[f] ?: FeatureStatus())) }

    /** Sends this row now when it is on and allowed. [force] = false skips it when the last send is recent. */
    suspend fun sync(context: Context, f: DeviceFeature, force: Boolean = true): Boolean {
        val app = context.applicationContext
        if (f == DeviceFeature.CALENDAR) return PhoneCalendarSync.sync(app, force)
        val prefs = prefs(app)
        if (!prefs.on(f) && prefs.wipePending(f)) {
            api(app)?.let { a -> if (runCatching { a.delete<JsonElement>(f.path!!) }.isSuccess) prefs.setWipePending(f, false) }
        }
        if (!on(app, f) || !permitted(app, f)) return false
        if (f == DeviceFeature.HEALTH && !visible(app)) return false   // Health Connect is read while Kelo is on screen
        val api = api(app) ?: return false
        if (!force && !DeviceRules.due(f, prefs.lastAt(f), System.currentTimeMillis())) return true
        return locks.getValue(f).withLock {
            set(f) { it.copy(busy = true, failed = false) }
            try {
                val count = send(app, api, f)
                val now = System.currentTimeMillis()
                prefs.sent(f, now, count)
                set(f) { FeatureStatus(lastAt = now, count = count) }
                true
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                if (e !is ApiError) Log.w(TAG, "${f.key} not sent", e)
                set(f) { it.copy(busy = false, failed = true) }
                false
            }
        }
    }

    /** Reads and sends one row; returns how many items went. */
    private suspend fun send(app: Context, api: KeloApi, f: DeviceFeature): Int {
        val device = app.appContainer.deviceName()
        val prefs = prefs(app)
        val now = System.currentTimeMillis()
        return when (f) {
            DeviceFeature.CONTACTS -> {
                val rows = withContext(Dispatchers.IO) { DeviceReaders.contacts(app.contentResolver) }
                api.put<JsonElement>(f.path!!, DeviceRules.contactsBody(rows, device))
                rows.size.coerceAtMost(DeviceRules.MAX_CONTACTS)
            }
            DeviceFeature.MESSAGES -> {
                val days = prefs.messageDays
                val rows = withContext(Dispatchers.IO) { DeviceReaders.messages(app.contentResolver, now - days * 86_400_000L) }
                val canSend = prefs.smsSend && has(app, "android.permission.SEND_SMS")
                api.put<JsonElement>(f.path!!, DeviceRules.messagesBody(rows, days, canSend, device))
                rows.size.coerceAtMost(DeviceRules.MAX_MESSAGES)
            }
            DeviceFeature.CALL_LOG -> {
                val rows = withContext(Dispatchers.IO) { DeviceReaders.calls(app.contentResolver, now - DeviceRules.CALL_DAYS * 86_400_000L) }
                api.put<JsonElement>(f.path!!, DeviceRules.callsBody(rows, device))
                rows.size.coerceAtMost(DeviceRules.MAX_CALLS)
            }
            DeviceFeature.HEALTH -> {
                val zone = ZoneId.systemDefault()
                val types = HealthReader.granted(app, prefs.healthTypes)
                prefs.healthGranted = types.isNotEmpty()
                val days = DeviceRules.healthDays(HealthReader.read(app, types, zone), types, zone)
                api.put<JsonElement>(f.path!!, DeviceRules.healthBody(days, types, zone, device))
                days.size
            }
            DeviceFeature.DIALER -> {
                api.put<JsonElement>(f.path!!, buildJsonObject { put("device_name", device.take(40)) })
                0
            }
            DeviceFeature.NOTIFICATIONS -> {
                val batch = NoteBuffer.take()
                try {
                    val r = api.post<JsonObject>(f.path!!, DeviceRules.notesBody(batch, device))
                    (r["count"] as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull() ?: prefs.lastCount(f)
                } catch (e: Exception) {
                    NoteBuffer.putBack(batch)
                    throw e
                }
            }
            DeviceFeature.CALENDAR -> 0
        }
    }

    suspend fun turnOn(context: Context, f: DeviceFeature): Boolean {
        if (f == DeviceFeature.CALENDAR) return PhoneCalendarSync.turnOn(context)
        val prefs = prefs(context)
        prefs.setOn(f, true)
        prefs.setWipePending(f, false)
        prefs.setDenied(f, false)
        if (f == DeviceFeature.CONTACTS) watch(context)
        return sync(context, f, force = true)
    }

    suspend fun turnOff(context: Context, f: DeviceFeature): Boolean {
        if (f == DeviceFeature.CALENDAR) return PhoneCalendarSync.turnOff(context)
        val prefs = prefs(context)
        prefs.setOn(f, false)
        prefs.setDenied(f, false)
        prefs.clear(f)
        if (f == DeviceFeature.CONTACTS) unwatch(context)
        if (f == DeviceFeature.NOTIFICATIONS) NoteBuffer.clear()
        if (f == DeviceFeature.MESSAGES) prefs.smsSend = false
        set(f) { FeatureStatus() }
        val ok = api(context)?.let { a -> runCatching { a.delete<JsonElement>(f.path!!) }.isSuccess } ?: false
        prefs.setWipePending(f, !ok)
        return ok
    }

    /** Every row that is on (and the calendar), at most as often as each row's gap allows unless [force]. Background: Health is skipped. */
    suspend fun syncAll(context: Context, force: Boolean) {
        for (f in DeviceFeature.entries) if (f != DeviceFeature.CALENDAR) runCatching { sync(context, f, force) }
    }

    fun syncSoon(context: Context, force: Boolean = false) {
        scope.launch { syncAll(context, force) }
    }

    fun sendSoon(context: Context, f: DeviceFeature) {
        scope.launch { sync(context, f, force = true) }
    }

    /** Called when the app starts signed in. */
    fun start(context: Context) {
        watch(context)
    }

    fun stop(context: Context) {
        unwatch(context)
    }

    private fun watch(context: Context) {
        val app = context.applicationContext
        if (contactsObserver != null || !on(app, DeviceFeature.CONTACTS) || !permitted(app, DeviceFeature.CONTACTS)) return
        val o = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) {
                pendingContacts?.cancel()
                pendingContacts = scope.launch {
                    delay(10_000)   // one send for a burst (a sync account writes many rows)
                    sync(app, DeviceFeature.CONTACTS, force = true)
                }
            }
        }
        runCatching { app.contentResolver.registerContentObserver(ContactsContract.Contacts.CONTENT_URI, true, o) }.onSuccess { contactsObserver = o }
    }

    private fun unwatch(context: Context) {
        contactsObserver?.let { runCatching { context.applicationContext.contentResolver.unregisterContentObserver(it) } }
        contactsObserver = null
        pendingContacts?.cancel()
    }

    // ---- notifications forwarded by KeloNotificationListener: gathered for a few seconds, then sent in one batch
    private var pendingNotes: Job? = null

    fun noteArrived(context: Context) {
        val app = context.applicationContext
        pendingNotes?.cancel()
        pendingNotes = scope.launch {
            delay(if (NoteBuffer.size() >= 50) 1_000 else 15_000)
            sync(app, DeviceFeature.NOTIFICATIONS, force = true)
        }
    }
}

/** Notifications waiting to be sent, in memory only (lost if the phone kills the process before the next send; at most 500). */
object NoteBuffer {
    private val items = ArrayDeque<SeenNote>()
    @Synchronized fun add(n: SeenNote) { items.addLast(n); while (items.size > 500) items.removeFirst() }
    @Synchronized fun take(): List<SeenNote> { val out = items.take(300); repeat(out.size) { items.removeFirst() }; return out }
    @Synchronized fun putBack(list: List<SeenNote>) { list.asReversed().forEach { items.addFirst(it) }; while (items.size > 500) items.removeLast() }
    @Synchronized fun clear() = items.clear()
    @Synchronized fun size(): Int = items.size
}
