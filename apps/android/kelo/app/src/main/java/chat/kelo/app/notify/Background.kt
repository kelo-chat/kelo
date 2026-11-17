package chat.kelo.app.notify

import android.content.Context
import android.util.Log
import chat.kelo.app.agent.NotificationPage
import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.agent.SettledItems
import chat.kelo.app.appContainer
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import com.google.firebase.FirebaseApp
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.security.MessageDigest

/**
 * Small things about notifications kept on the phone, not secret (ids and yes/no flags): per server (by a hash of its address)
 * the newest server notification already handled, and the push token last registered.
 */
class NotifyPrefs(context: Context) {
    private val p = context.getSharedPreferences("kelo_notify", Context.MODE_PRIVATE)

    fun lastNote(server: String): Long? = p.getLong("last." + hash(server), -1).takeIf { it >= 0 }
    fun setLastNote(server: String, id: Long) = p.edit().putLong("last." + hash(server), id).apply()

    /** The in-chat question "tell you when a reply comes?" was asked already */
    var askedPermission: Boolean
        get() = p.getBoolean("asked", false)
        set(v) = p.edit().putBoolean("asked", v).apply()

    /** This token went to this server less than a day ago (sent again once a day, so a server that lost it gets it back). */
    fun registered(server: String, token: String, now: Long = System.currentTimeMillis()): Boolean =
        !PushRules.due(p.getString("push", null), hash(server + "\n" + token), p.getLong("push.at", 0), now)

    fun setRegistered(server: String, token: String, now: Long = System.currentTimeMillis()) =
        p.edit().putString("push", hash(server + "\n" + token)).putLong("push.at", now).apply()

    /** The server refused this sign-in's token (403: the access key, not a paired phone): push needs a paired phone. */
    fun pushNeedsDevice(server: String): Boolean = p.getBoolean("push.device." + hash(server), false)
    fun setPushNeedsDevice(server: String, on: Boolean) = p.edit().putBoolean("push.device." + hash(server), on).apply()

    private fun hash(s: String): String =
        MessageDigest.getInstance("SHA-256").digest(s.toByteArray()).take(12).joinToString("") { "%02x".format(it) }
}

/**
 * Asks the server what is new (GET /api/notifications?after=<last>) and posts what nobody has seen yet. The first check on a phone
 * only notes where the list stands (old notifications are not replayed). Used by the 15-minute [PollJobService] and by pushes.
 */
class NotificationPoller(private val prefs: NotifyPrefs, private val notifier: Notifier, private val visible: () -> Boolean) {
    private val lock = Mutex()

    suspend fun check(api: KeloApi) = lock.withLock {
        val last = prefs.lastNote(api.baseUrl)
        val page = api.get<NotificationPage>("/api/notifications?after=${last ?: 0}&limit=50")
        if (last != null && !visible()) {
            NotifyRules.fresh(page.items, last).forEach { n ->
                if (NotifyRules.shouldPost(foreground = visible(), seen = n.seen, alreadyPosted = n.id <= last)) {
                    // an approval's notification gets the buttons its waiting item allows (none when the item cannot be read)
                    val offered = if (n.type == NoteTypes.APPROVAL_WAITING) n.approvalId?.let { pendingActions(api, it) }.orEmpty() else emptyList()
                    notifier.post(n, offered)
                }
            }
        }
        prefs.setLastNote(api.baseUrl, NotifyRules.nextCursor(last ?: 0, page.lastId, page.items))
        dropSettled(api)
    }

    /** What the notification of waiting approval [id] may offer (GET /api/approvals/pending "notify_actions"); empty when it cannot be read. */
    private suspend fun pendingActions(api: KeloApi, id: String): List<String> =
        withTimeoutOrNull(8_000) { runCatching { PendingApproval.notifyActionsFor(api.get<JsonElement>("/api/approvals/pending"), id) }.getOrNull() }.orEmpty()

    /**
     * Takes down the notifications whose waiting item does not wait any more (answered on another device, ran out, cancelled): asks
     * the server about the item ids that are up. Used on every check, so it also runs when the app opens. A failed ask changes nothing.
     */
    suspend fun dropSettled(api: KeloApi) {
        val path = ItemIds.settledPath(notifier.shownItems()) ?: return
        val gone = runCatching { api.get<SettledItems>(path).ids }.getOrNull().orEmpty()
        notifier.settle(gone)
    }
}

/**
 * The background check every 15 minutes (the shortest Android allows), only with a network: a plain JobScheduler job. It also sends
 * the phone calendar's snapshot when this phone shares it (chat.kelo.app.phonecal.PhoneCalendarSync).
 *
 * Not WorkManager: while the app's process is alive but cached, WorkManager runs periodic work in-process (its greedy scheduler)
 * instead of through JobScheduler, and on Android 15+ a cached app has no network outside a job the system runs for it, so every
 * such run timed out (seen on the emulator: APP_BACKGROUND network block). A job the system starts here always has the network.
 */
class PollJobService : android.app.job.JobService() {
    private val scope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.IO)

    override fun onStartJob(params: android.app.job.JobParameters): Boolean {
        val c = applicationContext.appContainer
        val api = c.sessions.api.value ?: return false
        scope.launch {
            val retry = try {
                c.poller.check(api)
                false
            } catch (e: ApiError) {
                e.isTransient
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                false
            }
            // the same job keeps the shared phone calendar fresh (does nothing when it is off)
            runCatching { chat.kelo.app.phonecal.PhoneCalendarSync.sync(applicationContext, force = true) }
            // and the other 이 기기에서 rows (Health Connect only while the app is open), and any approved work waiting for this phone
            runCatching { chat.kelo.app.device.DeviceSync.syncAll(applicationContext, force = false) }
            runCatching { chat.kelo.app.device.DeviceTasks.check(applicationContext) }
            jobFinished(params, retry)
        }
        return true
    }

    override fun onStopJob(params: android.app.job.JobParameters): Boolean = true

    override fun onDestroy() {
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
        super.onDestroy()
    }

    companion object {
        const val JOB_ID = 70_101
        private const val PERIOD_MS = 15 * 60 * 1000L

        fun schedule(context: Context) {
            val js = context.getSystemService(android.app.job.JobScheduler::class.java) ?: return
            if (js.getPendingJob(JOB_ID) != null) return   // already planned: keep its timing
            val job = android.app.job.JobInfo.Builder(JOB_ID, android.content.ComponentName(context, PollJobService::class.java))
                .setRequiredNetworkType(android.app.job.JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(PERIOD_MS)
                .setPersisted(true)   // survives a restart of the phone
                .build()
            js.schedule(job)
        }

        fun cancel(context: Context) {
            context.getSystemService(android.app.job.JobScheduler::class.java)?.cancel(JOB_ID)
        }
    }
}

/**
 * Push registration. The phone's FCM token goes to its own Kelo server: POST /api/push/register {"token", "platform": "android"}
 * with this device's bearer token. A server without that endpoint (404) is asked again on the next start. Builds without
 * google-services.json have no Firebase: push is then simply off (logged once).
 */
object PushRegistrar {
    private const val TAG = "KeloPush"

    fun available(context: Context): Boolean = runCatching { FirebaseApp.getApps(context).isNotEmpty() }.getOrDefault(false)

    suspend fun refresh(context: Context, api: KeloApi) {
        if (!available(context)) {
            Log.i(TAG, "no Firebase in this build (google-services.json missing): push is off")
            return
        }
        val token = try {
            FirebaseMessaging.getInstance().token.awaitResult()
        } catch (e: Exception) {
            Log.w(TAG, "no push token", e)
            return
        }
        register(context, api, token)
    }

    suspend fun register(context: Context, api: KeloApi, token: String) {
        val prefs = context.appContainer.notifyPrefs
        if (prefs.registered(api.baseUrl, token)) return
        try {
            api.post<JsonElement>("/api/push/register", buildJsonObject { put("token", token); put("platform", "android") })
            prefs.setRegistered(api.baseUrl, token)
            prefs.setPushNeedsDevice(api.baseUrl, false)
        } catch (e: ApiError) {
            // 403: signed in with the access key; the server keeps push tokens per paired phone only
            prefs.setPushNeedsDevice(api.baseUrl, PushRules.needsDevice(e.status))
            if (e.status != 404 && e.status != 403) Log.w(TAG, "push registration failed: HTTP ${e.status}")
        }
    }
}

/** When the push token is sent again, and what a refusal means. */
object PushRules {
    const val DAY_MS = 24L * 60 * 60 * 1000

    /** Send when the token or the server changed, or the last send is a day old (or in the future: the clock moved). */
    fun due(stored: String?, current: String, lastAt: Long, now: Long): Boolean =
        stored != current || now - lastAt >= DAY_MS || now < lastAt

    /** 403 on /api/push/register = the access key is signed in; pushes need a paired phone. */
    fun needsDevice(status: Int): Boolean = status == 403
}

/**
 * FCM: a data message {type, id, title?, body?} means "something is new". The details are fetched from the user's own server with
 * this device's token and posted like the 15-minute check does; when the server cannot be reached, the message's own words are
 * shown (they are minimal by design).
 */
class KeloMessagingService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) {
        val push = PushData.parse(message.data) ?: return
        val c = appContainer
        val api = c.sessions.api.value ?: return
        if (push.type == PushData.DEVICE_TASK) {   // a text, the dialer or a calendar change waits for this phone
            kotlinx.coroutines.runBlocking { runCatching { kotlinx.coroutines.withTimeout(70_000) { chat.kelo.app.device.DeviceTasks.check(applicationContext) } } }
            return
        }
        if (push.type == PushData.SETTLED) {   // item ids only: take those notifications down; nothing is shown or fetched
            c.notifier.settle(push.ids)
            return
        }
        kotlinx.coroutines.runBlocking {   // onMessageReceived runs on a worker thread and may take a few seconds
            val ok = runCatching { kotlinx.coroutines.withTimeout(15_000) { c.poller.check(api) } }.isSuccess
            if (!ok && push.type != PushData.WAKE && !c.visibility.visible.value) {
                c.notifier.post(
                    chat.kelo.app.agent.ServerNotification(
                        id = push.id ?: 0, type = push.type, title = push.title.orEmpty(), body = push.body.orEmpty(),
                        messageId = push.messageId,
                    ),
                )
            }
        }
    }

    override fun onNewToken(token: String) {
        val c = appContainer
        val api = c.sessions.api.value ?: return
        kotlinx.coroutines.runBlocking { runCatching { PushRegistrar.register(applicationContext, api, token) } }
    }
}

/** Marks a server notification seen once its phone notification was opened (POST /api/notifications/seen). */
suspend fun markNoteSeen(api: KeloApi, id: Long) {
    runCatching { api.post<JsonElement>("/api/notifications/seen", buildJsonObject { put("up_to", id) }) }
}

/** A Play services task's result, suspending until it is there. */
private suspend fun <T> com.google.android.gms.tasks.Task<T>.awaitResult(): T = kotlinx.coroutines.suspendCancellableCoroutine { cont ->
    addOnCompleteListener { t ->
        val e = t.exception
        if (e != null) cont.resumeWith(Result.failure(e))
        else if (t.isCanceled) cont.cancel()
        else cont.resumeWith(Result.success(t.result))
    }
}
