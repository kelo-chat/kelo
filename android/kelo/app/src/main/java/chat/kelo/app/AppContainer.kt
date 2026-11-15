package chat.kelo.app

import android.content.Context
import android.os.Build
import chat.kelo.app.core.AcceptLanguage
import chat.kelo.app.core.Languages
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.KeystoreCipher
import chat.kelo.app.core.PairingClient
import chat.kelo.app.core.ScreenCache
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Sessions
import chat.kelo.app.core.secureStoreFor
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File

/** The app's long-lived objects, made once in [KeloApp]. Screens reach it through [LocalAppContainer] or [Context.appContainer]. */
class AppContainer(context: Context) {
    val appContext: Context = context.applicationContext
    val httpClient = KeloApi.defaultClient
    val store: SecureStore = secureStoreFor(appContext)

    /** The language every request asks the server for: the app's pick, then the phone's languages (see [AcceptLanguage]). */
    fun refreshAcceptLanguage() {
        AcceptLanguage.current = runCatching { AcceptLanguage.build(Languages.override(store.language), LocaleSupport.deviceLanguageTags()) }.getOrDefault("")
    }

    init {
        LocaleSupport.syncWithSystem(appContext, store)   // a language changed in the system's per-app setting (Android 13+)
        refreshAcceptLanguage()
    }
    /** Each screen's last good data, encrypted with its own Keystore key (see [ScreenCache]) */
    val screenCache = ScreenCache(File(appContext.noBackupFilesDir, "screens"), KeystoreCipher(CACHE_KEY_ALIAS))
    val sessions = Sessions(store, httpClient, screenCache)

    private val _cacheReady = MutableStateFlow(false)
    /** true once the signed-in server's kept screens are in memory (the splash screen waits for it, briefly) */
    val cacheReady: StateFlow<Boolean> = _cacheReady.asStateFlow()

    init {
        Thread({
            runCatching { sessions.warmCache(Caches.ALL) }
            _cacheReady.value = true
        }, "kelo-cache-warm").start()
    }
    /** Pairing uses the key this phone already pinned for a server, never a fetched one instead (KE2E 5.5) */
    val pairing = PairingClient(httpClient) { url -> store.pinFor(url) }
    /** What the agent is doing right now, for the header's name pill */
    val agentActivity = chat.kelo.app.chat.AgentActivity()
    /** What another app shared with Kelo, waiting for the chat */
    val shareInbox = chat.kelo.app.core.ShareInbox()

    // ---- notifications
    /** Whether one of the app's screens is on screen (registered in [KeloApp]) */
    val visibility = chat.kelo.app.notify.AppVisibility()
    val notifier = chat.kelo.app.notify.Notifier(appContext)
    val notifyPrefs = chat.kelo.app.notify.NotifyPrefs(appContext)
    val poller = chat.kelo.app.notify.NotificationPoller(notifyPrefs, notifier) { visibility.visible.value }
    /** Whether this phone shares its own calendar with the server (마이 → 권한 → 휴대폰 캘린더) */
    val phoneCalendarPrefs = chat.kelo.app.phonecal.PhoneCalendarPrefs(appContext)
    /** The other 이 기기에서 rows: contacts, messages, dialer, notifications, call log, Health Connect (chat.kelo.app.device) */
    val devicePrefs = chat.kelo.app.device.DevicePrefs(appContext)
    /** The chat's followed reply, for "reply done" while the app is away */
    val replyWatch = chat.kelo.app.notify.ReplyWatch(appContext, notifier, visibility)
    /** A place a tapped notification asked to open, waiting for the signed-in app */
    val openRequests = kotlinx.coroutines.flow.MutableStateFlow<chat.kelo.app.notify.OpenTarget?>(null)
    /** A chat message a tapped follow-up notification asked to show (by its id), waiting for the chat */
    val chatFocus = kotlinx.coroutines.flow.MutableStateFlow<Long?>(null)
    /** A secure entry's card the chat should scroll to and mark for a moment (a row in 답이 필요해요 was tapped) */
    val chatEntryFocus = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)
    /** A card number from a message the server held back, for the wallet's card form (memory only; chat/SecretGuard.kt) */
    val secretHandoff = chat.kelo.app.chat.SecretHandoff()
    /** A 마이 sub page a tapped notification asked for (e.g. the wallet), waiting for the My tab */
    val myPageRequests = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)
    /** The agent's picture (GET /api/agent/avatar), by version */
    val agentAvatar = chat.kelo.app.agent.AgentAvatar(sessions)
    /** The app's updates: the file builds' own check and download, or Google Play's In-App Updates in the Play build (update/AppUpdates.kt) */
    val updater: chat.kelo.app.update.AppUpdates = chat.kelo.app.update.AppUpdatesFactory.create(appContext)

    companion object {
        const val CACHE_KEY_ALIAS = "chat.kelo.app.cache.v1"
    }

    /** What this phone is called in 마이 → 내 기기, e.g. "Pixel 8 · Kelo" (the server keeps 40 characters). */
    fun deviceName(): String = appContext.getString(R.string.device_name_format, Build.MODEL.orEmpty().ifBlank { "Android" }).take(40)
}

val Context.appContainer: AppContainer get() = (applicationContext as KeloApp).container
