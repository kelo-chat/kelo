package chat.kelo.app

import android.content.Context
import android.os.Build
import chat.kelo.app.core.AcceptLanguage
import chat.kelo.app.core.Languages
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.SourceLink
import chat.kelo.app.core.KeystoreCipher
import chat.kelo.app.core.PairingClient
import chat.kelo.app.core.ScreenCache
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Sessions
import chat.kelo.app.core.secureStoreFor
import chat.kelo.app.platform.*
import chat.kelo.app.wallet.PrefsWalletConsent
import chat.kelo.app.wallet.WalletConsent
import kotlinx.coroutines.flow.MutableStateFlow
import java.io.File

/** The app's long-lived objects, made once in [KeloApp]. Screens reach it through [LocalAppContainer] or [Context.appContainer]. */
class AppContainer(context: Context) : AppServices {
    override val appVersion: String = BuildConfig.VERSION_NAME
    override val sourceTag: String? = SourceLink.appTag("android", BuildConfig.VERSION_NAME)
    override val sourceDirty: Boolean = BuildConfig.SOURCE_DIRTY
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
    override val sessions = Sessions(store, httpClient, screenCache)

    init {
        Thread({ runCatching { sessions.warmCache(Caches.ALL) } }, "kelo-cache-warm").start()
    }
    /** Pairing uses the key this phone already pinned for a server, never a fetched one instead (KE2E 5.5) */
    override val pairing = PairingClient(httpClient) { url -> store.pinFor(url) }
    /** What the agent is doing right now, for the header's name pill */
    override val agentActivity = chat.kelo.app.chat.AgentActivity()
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
    override val openRequests = kotlinx.coroutines.flow.MutableStateFlow<chat.kelo.app.notify.OpenTarget?>(null)
    /** A chat message a tapped follow-up notification asked to show (by its id), waiting for the chat */
    override val chatFocus = kotlinx.coroutines.flow.MutableStateFlow<Long?>(null)
    /** A secure entry's card the chat should scroll to and mark for a moment (a row in 답이 필요해요 was tapped) */
    override val chatEntryFocus = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)
    override val chatFollowTurn = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)
    /** A card number from a message the server held back, for the wallet's card form (memory only; chat/SecretGuard.kt) */
    override val secretHandoff = chat.kelo.app.chat.SecretHandoff()
    /** A 마이 sub page a tapped notification asked for (e.g. the wallet), waiting for the My tab */
    override val myPageRequests = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)
    /** The agent's picture (GET /api/agent/avatar), by version */
    override val agentAvatar = chat.kelo.app.agent.AgentAvatar(sessions)
    /** The app's updates: the file builds' own check and download, or Google Play's In-App Updates in the Play build (update/AppUpdates.kt) */
    val updater: chat.kelo.app.update.AppUpdates = chat.kelo.app.update.AppUpdatesFactory.create(appContext)
    override val appUpdates get() = updater
    /** A newer released app that works with the server (X-Kelo-App-Update): the banner shows once per version; dismissed versions stay on this phone */
    val appNotice = chat.kelo.app.update.AppNotice(
        load = { appContext.getSharedPreferences("kelo_update", android.content.Context.MODE_PRIVATE).getString("app_notice_seen", null) },
        save = { appContext.getSharedPreferences("kelo_update", android.content.Context.MODE_PRIVATE).edit().putString("app_notice_seen", it).apply() },
    )
    /** The "new version" popup: once per version, "Later" = 3 days; its answers stay on this phone */
    val updatePopup = chat.kelo.app.update.UpdatePopup(
        load = { appContext.getSharedPreferences("kelo_update", android.content.Context.MODE_PRIVATE).getString("popup_until", null) },
        save = { appContext.getSharedPreferences("kelo_update", android.content.Context.MODE_PRIVATE).edit().putString("popup_until", it).apply() },
    )

    // ---- what the My and wallet pages need from the phone (shared interfaces in chat.kelo.app.platform / wallet)
    /** The activity on screen, for the services below that open a picker, ask for a permission or run the owner's check */
    override val allowLocalHttp: Boolean = BuildConfig.DEBUG
    val activityHost = chat.kelo.app.ui.ActivityHost()
    override val filePicker: FilePicker = AndroidFilePicker(activityHost)
    override val systemSettings: SystemSettings = AndroidSystemSettings(appContext)
    override val browser: ExternalBrowser = AndroidExternalBrowser(appContext, activityHost)
    override val phoneData: PhoneDataSection = AndroidPhoneData
    override val connectorIcons = chat.kelo.app.connectors.ConnectorIcons(File(appContext.cacheDir, "connector-icons"))
    override val notifications: PhoneNotifications = AndroidPhoneNotifications(notifier, notifyPrefs)
    override val phoneLocation: PhoneLocation = AndroidPhoneLocation(appContext, activityHost)
    override val language: AppLanguage = AndroidAppLanguage(this, activityHost)
    override val walletConsent: WalletConsent = PrefsWalletConsent(appContext)
    override val cardKeys: chat.kelo.app.wallet.CardKeys = chat.kelo.app.wallet.AndroidCardKeys(appContext) { activityHost.current }
    override val keyguardKeys: chat.kelo.app.keyguard.KeyguardKeys =
        chat.kelo.app.keyguard.AndroidKeyguardKeys(appContext, { activityHost.current as? androidx.fragment.app.FragmentActivity }, { sessions.api.value?.baseUrl })
    override val cardScanner: chat.kelo.app.wallet.CardScanner = chat.kelo.app.wallet.AndroidCardScanner()
    /** What was typed on the card pages, kept in memory while the lock that comes with leaving the app is on (never saved) */
    override val walletPlaces = chat.kelo.app.wallet.WalletPlaces((appContext as KeloApp).appScope, android.os.SystemClock::elapsedRealtime)

    companion object {
        const val CACHE_KEY_ALIAS = "chat.kelo.app.cache.v1"
    }

    /** What this phone is called in 마이 → 내 기기, e.g. "Galaxy S24 · Kelo" (the server keeps 40 characters). */
    override fun deviceName(): String = appContext.getString(
        R.string.device_name_format,
        chat.kelo.app.device.DeviceModel.name(
            Build.MANUFACTURER.orEmpty(),
            Build.MODEL.orEmpty(),
            chat.kelo.app.device.DeviceModel.marketNameProperty(),
        ),
    ).take(40)
}

val Context.appContainer: AppContainer get() = (applicationContext as KeloApp).container
