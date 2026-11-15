package chat.kelo.app.device

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.provider.CalendarContract
import android.telephony.SmsManager
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import chat.kelo.app.BuildConfig
import chat.kelo.app.R
import chat.kelo.app.appContainer
import chat.kelo.app.notify.Channel
import chat.kelo.app.notify.NotifyRules
import chat.kelo.app.phonecal.PhoneCalendarSync
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.concurrent.atomic.AtomicInteger

/**
 * Work the user approved that only this phone can do: send a text (SmsManager, from this phone's number), open the dialer with a
 * number filled in (ACTION_DIAL: the user presses call; Kelo never places a call and has no CALL_PHONE permission), or add, change or
 * delete an event in the phone calendar the user picked. The server queues it after the approval (POST /api/phone/tasks/claim hands
 * each one out once); the phone checks on a push {type: device_task}, right after an approval is answered here, when the app comes
 * to the front and from the 15-minute job, then reports how it went (POST /api/phone/tasks/{id}/result). The phone checks again that
 * the row is still on before doing anything (DeviceRules.refuse).
 */
object DeviceTasks {
    private const val TAG = "KeloDeviceTask"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val lock = Mutex()
    private val done = LinkedHashSet<Long>()   // ids already handled in this process (a second check never repeats one)
    private val seq = AtomicInteger(0)
    private var app: Context? = null

    fun init(context: Context) { app = context.applicationContext }

    /** Some row that can do work is on: only then is there anything to ask the server about. */
    fun anyOn(context: Context): Boolean = DeviceFeature.entries.any { it.doesTasks && DeviceSync.on(context, it) }

    /** An approval was just allowed in the app: what it allowed may be waiting for this phone now (checked a few times). */
    fun afterApproval() {
        val ctx = app ?: return
        if (!anyOn(ctx)) return
        scope.launch {
            for (wait in longArrayOf(1_500, 4_000, 10_000)) {
                delay(wait)
                runCatching { check(ctx) }
            }
        }
    }

    fun checkSoon(context: Context) {
        if (!anyOn(context)) return
        scope.launch { runCatching { check(context) } }
    }

    suspend fun check(context: Context) {
        val ctx = context.applicationContext
        if (!anyOn(ctx)) return
        val api = ctx.appContainer.sessions.api.value ?: return
        lock.withLock {
            val tasks = DeviceTask.parseList(api.request("POST", "/api/phone/tasks/claim"))
            for (t in tasks) {
                if (!done.add(t.id)) continue
                while (done.size > 200) done.remove(done.first())
                val (ok, outcome, error) = runCatching { run(ctx, t) }.getOrElse { e -> Triple(false, "", e.message ?: e.javaClass.simpleName) }
                runCatching {
                    api.post<JsonElement>("/api/phone/tasks/${t.id}/result", buildJsonObject {
                        put("ok", ok); if (outcome.isNotEmpty()) put("outcome", outcome); if (error.isNotEmpty()) put("error", error.take(200))
                    })
                }
                if (!ok && t.kind == "sms") notifySmsFailed(ctx, t, error)
            }
        }
    }

    /** (ok, outcome, error) */
    private suspend fun run(ctx: Context, t: DeviceTask): Triple<Boolean, String, String> {
        val prefs = DeviceSync.prefs(ctx)
        val calWrite = PhoneCalendarSync.prefs(ctx).writeAllowed && PhoneCalendarSync.writePermitted(ctx)
        val smsAllowed = prefs.smsSend && DeviceSync.has(ctx, "android.permission.SEND_SMS")
        DeviceRules.refuse(t, BuildConfig.FULL_DEVICE, { DeviceSync.on(ctx, it) }, smsAllowed, calWrite, System.currentTimeMillis() / 1000.0)
            ?.let { return Triple(false, "", it) }
        return when (t.kind) {
            "sms" -> sendSms(ctx, t)
            "dial" -> dial(ctx, t)
            else -> calendar(ctx, t)
        }
    }

    // ---- a text
    private suspend fun sendSms(ctx: Context, t: DeviceTask): Triple<Boolean, String, String> {
        val sms: SmsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) ctx.getSystemService(SmsManager::class.java) else @Suppress("DEPRECATION") SmsManager.getDefault()
        val parts = sms.divideMessage(t.s("text"))
        val action = "${ctx.packageName}.SMS_SENT.${t.id}"
        val results = List(parts.size) { CompletableDeferred<Int>() }
        val got = AtomicInteger(0)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                val n = i.getIntExtra("part", 0)
                results.getOrNull(n)?.complete(resultCode)
                got.incrementAndGet()
            }
        }
        ContextCompat.registerReceiver(ctx, receiver, IntentFilter(action), ContextCompat.RECEIVER_NOT_EXPORTED)
        try {
            val sent = ArrayList(parts.indices.map { n ->
                PendingIntent.getBroadcast(ctx, seq.incrementAndGet(), Intent(action).setPackage(ctx.packageName).putExtra("part", n),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_ONE_SHOT)
            })
            sms.sendMultipartTextMessage(DeviceRules.dialNumber(t.s("number")), null, parts, sent, null)
            val codes = withTimeoutOrNull(60_000) { results.map { it.await() } }
                ?: return Triple(false, "", "the phone did not confirm sending within a minute")
            val bad = codes.firstOrNull { it != Activity.RESULT_OK }
            return if (bad == null) Triple(true, "sent", "") else Triple(false, "", smsError(bad))
        } finally {
            runCatching { ctx.unregisterReceiver(receiver) }
            DeviceSync.sendSoon(ctx, DeviceFeature.MESSAGES)   // the sent text shows up in the shared messages too
        }
    }

    private fun smsError(code: Int): String = when (code) {
        SmsManager.RESULT_ERROR_NO_SERVICE -> "no mobile service"
        SmsManager.RESULT_ERROR_RADIO_OFF -> "airplane mode or the radio is off"
        SmsManager.RESULT_ERROR_NULL_PDU -> "the message could not be made"
        SmsManager.RESULT_ERROR_LIMIT_EXCEEDED -> "too many texts sent; the phone paused sending"
        SmsManager.RESULT_ERROR_SHORT_CODE_NOT_ALLOWED, SmsManager.RESULT_ERROR_SHORT_CODE_NEVER_ALLOWED -> "the phone does not allow texting this number"
        else -> "the phone could not send it (code $code)"
    }

    private fun notifySmsFailed(ctx: Context, t: DeviceTask, error: String) {
        if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return
        val who = t.s("name").ifBlank { t.s("number") }
        val n = NotificationCompat.Builder(ctx, Channel.APPROVALS.id)
            .setSmallIcon(R.drawable.ic_notification).setContentTitle(ctx.getString(R.string.device_sms_failed_title))
            .setContentText("$who · $error").setAutoCancel(true).build()
        runCatching { NotificationManagerCompat.from(ctx).notify(NotifyRules.idFor("sms", t.id.toString()), n) }
    }

    // ---- the dialer
    private fun dial(ctx: Context, t: DeviceTask): Triple<Boolean, String, String> {
        val intent = Intent(Intent.ACTION_DIAL, Uri.fromParts("tel", DeviceRules.dialNumber(t.s("number")), null)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        if (intent.resolveActivity(ctx.packageManager) == null && Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return Triple(false, "", "no dialer app on this phone")
        return when (DeviceRules.dialWay(ctx.appContainer.visibility.visible.value)) {
            DeviceRules.DialWay.OPEN -> runCatching { ctx.startActivity(intent) }.fold(
                { Triple(true, "opened", "") }, { Triple(false, "", "no dialer app on this phone") },
            )
            DeviceRules.DialWay.NOTIFY -> {
                // Android does not let an app in the background open another app; a notification opens the dialer when tapped
                if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return Triple(false, "", "Kelo notifications are off on the phone")
                val who = t.s("name").ifBlank { t.s("number") }
                val pi = PendingIntent.getActivity(ctx, seq.incrementAndGet(), intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
                val n = NotificationCompat.Builder(ctx, Channel.APPROVALS.id)
                    .setSmallIcon(R.drawable.ic_notification).setContentTitle(ctx.getString(R.string.device_dial_title, who))
                    .setContentText(ctx.getString(R.string.device_dial_body)).setContentIntent(pi).setAutoCancel(true)
                    .setPriority(NotificationCompat.PRIORITY_HIGH).setTimeoutAfter(10 * 60_000L).build()
                runCatching { NotificationManagerCompat.from(ctx).notify(NotifyRules.idFor("dial", t.id.toString()), n) }
                    .fold({ Triple(true, "notified", "") }, { Triple(false, "", "the notification could not be shown") })
            }
        }
    }

    // ---- the phone calendar
    private suspend fun calendar(ctx: Context, t: DeviceTask): Triple<Boolean, String, String> {
        val cr = ctx.contentResolver
        val target = PhoneCalendarSync.writeTarget(ctx) ?: return Triple(false, "", "no calendar on the phone can be changed")
        try {
            when (t.kind) {
                "cal_add" -> {
                    if (t.l("calendar_id") != target.first) return Triple(false, "", "the calendar to write to changed on the phone; ask again")
                    val v = eventValues(t).apply { put(CalendarContract.Events.CALENDAR_ID, target.first) }
                    cr.insert(CalendarContract.Events.CONTENT_URI, v) ?: return Triple(false, "", "the calendar did not take the event")
                }
                "cal_update", "cal_delete" -> {
                    val id = t.l("event_id") ?: return Triple(false, "", "no event id")
                    val uri = ContentUris.withAppendedId(CalendarContract.Events.CONTENT_URI, id)
                    var repeats = false
                    var exists = false
                    cr.query(uri, arrayOf(CalendarContract.Events.RRULE, CalendarContract.Events.CALENDAR_ID), null, null, null)?.use { c ->
                        if (c.moveToFirst()) { exists = true; repeats = !c.getString(0).isNullOrBlank() }
                    }
                    if (!exists) return Triple(false, "", "the event is no longer in the phone calendar")
                    if (repeats) return Triple(false, "", "it is a repeating event; change it in the calendar app")
                    val n = if (t.kind == "cal_delete") cr.delete(uri, null, null) else cr.update(uri, eventValues(t), null, null)
                    if (n <= 0) return Triple(false, "", "the calendar did not take the change (it may be read-only)")
                }
            }
        } catch (e: SecurityException) {
            return Triple(false, "", "the calendar permission is off")
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "calendar change refused", e)
            return Triple(false, "", "the calendar refused the change")
        }
        PhoneCalendarSync.sync(ctx, force = true)   // the agent sees the change at once
        return Triple(true, "done", "")
    }

    private fun eventValues(t: DeviceTask): ContentValues = ContentValues().apply {
        if (t.has("title")) put(CalendarContract.Events.TITLE, t.s("title"))
        if (t.has("location")) put(CalendarContract.Events.EVENT_LOCATION, t.s("location"))
        if (t.has("description")) put(CalendarContract.Events.DESCRIPTION, t.s("description"))
        if (t.has("start")) {
            if (t.b("all_day") && t.s("day").isNotEmpty()) {
                put(CalendarContract.Events.ALL_DAY, 1)
                put(CalendarContract.Events.EVENT_TIMEZONE, "UTC")
                put(CalendarContract.Events.DTSTART, DeviceRules.allDayMillis(t.s("day")))
                put(CalendarContract.Events.DTEND, DeviceRules.allDayMillis(t.s("last_day").ifEmpty { t.s("day") }) + 86_400_000L)
            } else {
                put(CalendarContract.Events.ALL_DAY, 0)
                put(CalendarContract.Events.EVENT_TIMEZONE, t.s("timezone").ifEmpty { java.util.TimeZone.getDefault().id })
                put(CalendarContract.Events.DTSTART, ((t.d("start") ?: 0.0) * 1000).toLong())
                put(CalendarContract.Events.DTEND, ((t.d("end") ?: t.d("start") ?: 0.0) * 1000).toLong())
            }
        }
    }
}
