package chat.kelo.app

import android.app.Application
import chat.kelo.app.approval.AndroidApprovalSigner
import chat.kelo.app.notify.PollJobService
import chat.kelo.app.notify.PushRegistrar
import chat.kelo.app.phonecal.PhoneCalendarSync
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class KeloApp : Application() {
    /** The phone's languages changed: requests ask for the new order from now on. */
    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        if (::container.isInitialized) container.refreshAcceptLanguage()
    }

    lateinit var container: AppContainer
        private set

    /** Work that outlives a screen (push registration) */
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    override fun onCreate() {
        super.onCreate()
        // docs/versioning.md: every call says which release this is; answers say the server's, compared with min_server
        chat.kelo.app.core.AppVersion.header = "android/${BuildConfig.VERSION_NAME}+${BuildConfig.VERSION_CODE}"
        chat.kelo.app.core.AppVersion.minServer = BuildConfig.MIN_SERVER
        // where this build is updated from: the store's page (play) or the download page (file builds); the server names a newer app for it
        chat.kelo.app.core.AppVersion.channel = if (BuildConfig.FLAVOR == "play") "play" else "download"
        container = AppContainer(this)
        chat.kelo.app.chat.AndroidChatPlatform.notifier = container.notifier
        // the phone's approval key for signed payment Allows (approval/AndroidApprovalSigner.kt); its check needs the screen's activity
        AndroidApprovalSigner.attach(this) { container.activityHost.current as? androidx.fragment.app.FragmentActivity }
        registerActivityLifecycleCallbacks(container.visibility)
        container.notifier.ensureChannels()
        chat.kelo.app.device.DeviceTasks.init(this)
        // signed in: the 15-minute check runs (a JobScheduler job) and this phone's push token goes to its server; signed out: the check stops
        appScope.launch {
            container.sessions.api.collect { api ->
                if (api == null) {
                    runCatching { PollJobService.cancel(this@KeloApp) }
                    PhoneCalendarSync.unwatch(this@KeloApp)
                    chat.kelo.app.device.DeviceSync.stop(this@KeloApp)
                } else {
                    runCatching { PollJobService.schedule(this@KeloApp) }
                    // KE2E: a session from before KE2E (or a typed sign-in) pins the server's key trust-on-first-use (spec 1.3)
                    if (api.keyPin == null) launch { runCatching { container.sessions.upgradeToE2e() } }
                    launch { runCatching { PushRegistrar.refresh(this@KeloApp, api) } }
                    launch { runCatching { chat.kelo.app.approval.ApprovalKeyRegistrar.refresh(AndroidApprovalSigner, api) } }
                    // the phone calendar, when this phone shares it: watched for changes and sent once the app is open
                    PhoneCalendarSync.watch(this@KeloApp)
                    PhoneCalendarSync.syncSoon(this@KeloApp)
                    chat.kelo.app.device.DeviceSync.start(this@KeloApp)   // the other 이 기기에서 rows that are on
                }
            }
        }
        // each time the app comes to the front: the token goes again when its last send is a day old (a local check, cheap), so a
        // server that lost its push tokens gets this phone's back without waiting for a new token
        appScope.launch {
            container.visibility.visible.collect { shown ->
                val api = container.sessions.api.value
                if (shown && api != null) {
                    launch { runCatching { PushRegistrar.refresh(this@KeloApp, api) } }
                    launch { runCatching { chat.kelo.app.approval.ApprovalKeyRegistrar.refresh(AndroidApprovalSigner, api) } }
                    PhoneCalendarSync.syncSoon(this@KeloApp)   // at most every few minutes (PhoneCalendarRules.RESUME_GAP_MS)
                    chat.kelo.app.device.DeviceSync.syncSoon(this@KeloApp)   // each row at most as often as DeviceRules.gapMs allows
                    chat.kelo.app.device.DeviceTasks.checkSoon(this@KeloApp)  // approved work that waited while the app was away
                }
            }
        }
    }
}
