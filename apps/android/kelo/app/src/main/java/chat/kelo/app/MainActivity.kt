package chat.kelo.app

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.os.SystemClock
import androidx.activity.compose.setContent
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.lifecycleScope
import chat.kelo.app.core.ShareParse
import chat.kelo.app.core.ShareRequest
import chat.kelo.app.core.Shared
import chat.kelo.app.start.AppRoot
import chat.kelo.app.ui.InsetsWatchdog
import chat.kelo.app.ui.theme.KeloTheme
import chat.kelo.app.ui.AndroidConsentMemory
import chat.kelo.app.ui.ProvideAndroidPlatform
import chat.kelo.app.chat.LocalConsentMemory
import chat.kelo.app.ui.systemReducesMotion
import chat.kelo.app.ui.theme.Motion
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

val LocalAppContainer = staticCompositionLocalOf<AppContainer> { error("no AppContainer") }

/**
 * The one activity. A FragmentActivity so the system's biometric prompt can be shown (opening the vault). It also receives what
 * other apps share with Kelo (text, PDF / Word / Excel documents, images) and "Kelo" in a text selection's menu; those go to the
 * [chat.kelo.app.core.ShareInbox] and from there into the chat, after signing in if need be.
 */
class MainActivity : FragmentActivity() {
    private var reduceMotion by mutableStateOf(false)

    override fun attachBaseContext(newBase: Context) {
        super.attachBaseContext(LocaleSupport.wrap(newBase, newBase.appContainer.store.language))
    }

    /** The first screen is known (see [AppRoot]): the launch screen can go */
    @Volatile private var settled = false
    private var fullyDrawnReported = false

    override fun onCreate(savedInstanceState: Bundle?) {
        // The launch screen (the icon on the page colour) stays until the first screen is known: signed out, or signed in with the
        // profile (kept from last time, or fetched). Never longer than SPLASH_MAX_MS, then whatever is there shows.
        val splash = installSplashScreen()
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val container = appContainer
        container.activityHost.current = this
        val consent = AndroidConsentMemory(this)
        reduceMotion = systemReducesMotion(this)
        container.updater.attach(this)   // checks when the app comes back; the Play build's update screens start from here
        if (savedInstanceState == null) {
            val shownAt = SystemClock.uptimeMillis()
            // The system's own exit (a short fade) is kept: a custom exit listener makes the splash library re-apply window
            // settings (system bar appearance, fitting) itself, which the app sets with enableEdgeToEdge.
            splash.setKeepOnScreenCondition { !SplashRules.canGo(settled, SystemClock.uptimeMillis() - shownAt) }
            receive(intent)
        }
        setContent {
            CompositionLocalProvider(LocalAppContainer provides container, LocalAppServices provides container, LocalConsentMemory provides consent) {
                ProvideAndroidPlatform(reduceMotion) {
                    KeloTheme { AppRoot(onSettled = ::onFirstScreen) }
                }
            }
        }
        // Compose's insets can get stuck on an animation the system never starts (see InsetsWatchdog): the header then sat a status
        // bar higher on some launches, and the tab bar stayed hidden after pairing as if the keyboard were still up.
        InsetsWatchdog.install(findViewById(android.R.id.content))
    }

    private fun onFirstScreen() {
        if (!settled) {
            // the launch screen held the first frame back: ask for the insets once more now that the app draws
            window.decorView.post { window.decorView.requestApplyInsets() }
        }
        settled = true
        if (!fullyDrawnReported) {
            fullyDrawnReported = true
            reportFullyDrawn()   // start-up measurements ("Fully drawn" in logcat) count to here
        }
    }

    override fun onDestroy() {
        if (appContainer.activityHost.current === this) appContainer.activityHost.current = null
        super.onDestroy()
    }

    override fun onResume() {
        super.onResume()
        reduceMotion = systemReducesMotion(this)   // the setting may have changed while away
        // the owner is looking at the app now: the notifications that only told something have done their job (owner 2026-11-12)
        runCatching { chat.kelo.app.notify.Notifier(this).clearNews() }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        receive(intent)
    }

    private fun receive(intent: Intent?) {
        intent ?: return
        // a tapped notification: open its place, and the server's notification counts as seen
        if (intent.action == chat.kelo.app.notify.Notifier.ACTION_OPEN) {
            val container = appContainer
            chat.kelo.app.notify.OpenTarget.decode(intent.getStringExtra(chat.kelo.app.notify.Notifier.EXTRA_OPEN))?.let { container.openRequests.value = it }
            val note = intent.getLongExtra(chat.kelo.app.notify.Notifier.EXTRA_NOTE, -1)
            val api = container.sessions.api.value
            if (note > 0 && api != null) lifecycleScope.launch { chat.kelo.app.notify.markNoteSeen(api, note) }
            return
        }
        val stream = intent.streamUri()
        val request = ShareParse.parse(
            action = intent.action,
            type = intent.type,
            text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT),
            subject = intent.getCharSequenceExtra(Intent.EXTRA_SUBJECT),
            processText = intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT),
            hasStream = stream != null,
        )
        val inbox = appContainer.shareInbox
        when (request) {
            is ShareRequest.Text -> inbox.put(Shared.Text(request.text))
            is ShareRequest.Stream -> {
                val uri = stream ?: return
                // copied now, while this activity holds the permission to read it; uploaded once the chat takes it
                lifecycleScope.launch {
                    inbox.put(withContext(Dispatchers.IO) { copyShared(uri, request.mime) })
                }
            }
            ShareRequest.None -> {}
        }
    }

    @Suppress("DEPRECATION")
    private fun Intent.streamUri(): Uri? =
        if (Build.VERSION.SDK_INT >= 33) getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java) else getParcelableExtra(Intent.EXTRA_STREAM) as? Uri

    private fun copyShared(uri: Uri, mime: String?): Shared = try {
        var display: String? = null
        var size = -1L
        contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { c ->
            if (c.moveToFirst()) {
                c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let { display = c.getString(it) }
                c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { size = c.getLong(it) }
            }
        }
        val type = mime ?: contentResolver.getType(uri)
        val name = ShareParse.uploadName(display, type)
        when {
            name == null -> Shared.Unsupported(display ?: uri.lastPathSegment.orEmpty())
            // no size limit of the server's here: the upload plan decides when it goes up (with this route's limits). The only limit
            // is the phone's own: the copy must fit in the free space of the app's cache.
            size > 0 && size > cacheDir.usableSpace -> Shared.Failed
            else -> {
                val dir = File(cacheDir, "shared").apply { mkdirs() }
                dir.listFiles()?.forEach { it.delete() }   // only the newest share is kept
                val out = File(dir, "share-" + System.currentTimeMillis())
                try {
                    val copied = contentResolver.openInputStream(uri)?.use { input -> out.outputStream().use { o -> input.copyTo(o, 64 * 1024); true } }
                    if (copied == true) Shared.File(out.path, name, type) else { out.delete(); Shared.Failed }
                } catch (e: Exception) {
                    out.delete()   // e.g. the cache ran out of room half way
                    Shared.Failed
                }
            }
        }
    } catch (e: Exception) {
        Shared.Failed
    }
}

/** When the launch screen may go. */
object SplashRules {
    const val MAX_MS = 1000L

    fun canGo(settled: Boolean, shownForMs: Long): Boolean = settled || shownForMs >= MAX_MS
}
