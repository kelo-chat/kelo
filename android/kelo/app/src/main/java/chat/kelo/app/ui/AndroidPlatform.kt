package chat.kelo.app.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import chat.kelo.app.shell.OnTabReselect
import chat.kelo.app.shell.LocalShellChrome
import chat.kelo.app.shell.bottomBarHeight
import chat.kelo.app.ui.platform.TabPageFrame
import kotlinx.coroutines.flow.MutableSharedFlow
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalBackHandler
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.shell.HideShellChrome
import chat.kelo.app.ui.platform.LocalChromeHider
import chat.kelo.app.ui.platform.LocalHaptics
import chat.kelo.app.platform.Toaster
import chat.kelo.app.platform.LocalToaster
import chat.kelo.app.platform.LocalPasswordApp
import chat.kelo.app.platform.LocalMediaPlayer
import chat.kelo.app.platform.LocalQrScanner
import chat.kelo.app.platform.QrScanner
import chat.kelo.app.platform.PasswordApp
import chat.kelo.app.ui.platform.LocalAppBackground
import chat.kelo.app.ui.platform.LocalPredictiveBack
import chat.kelo.app.ui.platform.LocalNetworkInfo
import chat.kelo.app.ui.platform.NetworkInfo
import chat.kelo.app.ui.platform.LocalReloadOnReturn
import chat.kelo.app.ui.platform.LocalVaultGate
import kotlinx.coroutines.flow.map
import chat.kelo.app.ui.platform.LocalSecureWindow
import chat.kelo.app.ui.platform.LocalTextSharer
import chat.kelo.app.ui.platform.TextSharer
import chat.kelo.app.ui.text.LocalTextLocale
import chat.kelo.app.ui.theme.LocalReduceMotion
import chat.kelo.app.ui.theme.Motion

/** Settings.Global.ANIMATOR_DURATION_SCALE == 0 ("remove animations" / "animation off" in developer options). */
fun systemReducesMotion(context: Context): Boolean = Motion.isOff(
    runCatching { Settings.Global.getFloat(context.contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) }.getOrDefault(1f),
)

/** The phone's back gesture steps out of a sub page, except inside a [PushHost] (its predictive back does it). */
private val AndroidBackHandler: @Composable (onBack: () -> Unit) -> Unit = { onBack ->
    if (!LocalInPushHost.current) BackHandler(onBack = onBack)
}

/** The system's predictive back, as the progress of the gesture. */
private val AndroidPredictiveBack: @Composable (Boolean, suspend (kotlinx.coroutines.flow.Flow<Float>) -> Unit) -> Unit = { enabled, onGesture ->
    androidx.activity.compose.PredictiveBackHandler(enabled = enabled) { events -> onGesture(events.map { it.progress }) }
}

/**
 * What the shared screens read from their app root, for the phone: the form factor, haptics on this window's view, whether the
 * system asks for no animation, the language the app is shown in and the back gesture. Wraps the theme (it reads the language).
 */
@Composable
fun ProvideAndroidPlatform(reduceMotion: Boolean, content: @Composable () -> Unit) {
    val view = LocalView.current
    val haptics = remember(view) { AndroidHaptics(view) }
    val context = LocalContext.current
    val owner = androidx.lifecycle.compose.LocalLifecycleOwner.current
    val lifecycle = remember(owner) { AndroidAppLifecycle(owner) }
    val toaster = remember(context) { Toaster { text -> android.widget.Toast.makeText(context, text, android.widget.Toast.LENGTH_SHORT).show() } }
    val passwordApp = remember(view) { AndroidPasswordApp(view) }
    val network = remember(context) {
        val cm = context.getSystemService(android.net.ConnectivityManager::class.java)
        NetworkInfo { cm?.isActiveNetworkMetered == true }
    }
    val sharer = remember(context) { TextSharer { text -> shareText(context, text) } }
    CompositionLocalProvider(
        LocalFormFactor provides FormFactor.Phone,
        LocalHaptics provides haptics,
        LocalReduceMotion provides reduceMotion,
        LocalTextLocale provides LocalConfiguration.current.locales[0],
        LocalBackHandler provides AndroidBackHandler,
        LocalTextSharer provides sharer,
        LocalNetworkInfo provides network,
        LocalChromeHider provides { key, hidden -> HideShellChrome(key, hidden) },
        LocalSecureWindow provides { SecureWindow() },
        LocalPredictiveBack provides AndroidPredictiveBack,
        LocalAppBackground provides { context.findActivity()?.moveTaskToBack(true) },
        chat.kelo.app.ui.platform.LocalAppLifecycle provides lifecycle,
        LocalPasswordApp provides passwordApp,
        LocalMediaPlayer provides ExoMediaPlayerView(),
        LocalQrScanner provides QrScanner { onClose, accept, onScanned -> chat.kelo.app.start.QrScanScreen(onClose, accept, onScanned) },
        chat.kelo.app.ui.platform.LocalViewModelHost provides AndroidViewModels,
        LocalToaster provides toaster,
        LocalVaultGate provides { onNoLock, title, onNotOpened, onOpen -> rememberAndroidVaultGate(onNoLock, title, onNotOpened, onOpen) },
        LocalReloadOnReturn provides { key, load -> ReloadOnReturn(key, load) },
        content = content,
    )
}

/**
 * Runs [load] when [key] changes and every time the app comes back to the foreground (the user signed in in the browser and came
 * back, or changed a permission in the system's settings), so a page shows the new state without being touched.
 */
@Composable
private fun ReloadOnReturn(key: Any?, load: suspend () -> Unit) {
    var returns by remember { mutableIntStateOf(0) }
    val current by rememberUpdatedState(load)
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { returns++ }
    LaunchedEffect(key, returns) { current() }
}

/** What the phone's bars give the page of the tab [route]: the tab bar's height, taps on the open tab, hiding the bars (shared/ui/platform/TabPageFrame.kt). */
@Composable
fun rememberTabPageFrame(route: String): TabPageFrame {
    val chrome = LocalShellChrome.current
    val taps = remember { MutableSharedFlow<Unit>(extraBufferCapacity = 1) }
    OnTabReselect(route) { taps.emit(Unit) }
    val bar = bottomBarHeight()
    return remember(chrome, bar) { TabPageFrame(bottomInset = bar, reselect = taps, hideChrome = chrome::hide) }
}

/** Which providers the user was already asked about, kept on the phone (the file the model picker used before it moved to shared/). */
class AndroidConsentMemory(context: android.content.Context) : chat.kelo.app.chat.ConsentMemory {
    private val prefs = context.getSharedPreferences("kelo_consent", android.content.Context.MODE_PRIVATE)
    override fun asked(key: String): Boolean = prefs.getBoolean(key, false)
    override fun markAsked(key: String) = prefs.edit().putBoolean(key, true).apply()
}

/** The system share sheet with [text]. */
private fun shareText(context: Context, text: String) {
    val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
    try {
        context.startActivity(Intent.createChooser(send, null).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) {
    }
}

/** The activity's ViewModel store behind the shared screens' ViewModels. */
object AndroidViewModels : chat.kelo.app.ui.platform.ViewModelHost {
    @Composable
    @Suppress("UNCHECKED_CAST")
    override fun <T : androidx.lifecycle.ViewModel> obtain(key: String, create: () -> T): T {
        val owner = androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner.current ?: error("no ViewModelStoreOwner")
        val factory = object : androidx.lifecycle.ViewModelProvider.Factory {
            override fun <V : androidx.lifecycle.ViewModel> create(modelClass: Class<V>): V = create() as V
        }
        return androidx.lifecycle.ViewModelProvider(owner, factory)[key, androidx.lifecycle.ViewModel::class.java] as T
    }
}

/** The phone's password app (autofill service), asked to fill the login form that has the focus. */
private class AndroidPasswordApp(private val view: android.view.View) : PasswordApp {
    private val manager get() = view.context.getSystemService(android.view.autofill.AutofillManager::class.java)
    override val available: Boolean
        get() = android.os.Build.VERSION.SDK_INT >= 26 && runCatching { manager?.let { it.isEnabled && it.hasEnabledAutofillServices() } == true }.getOrDefault(false)
    override fun request() {
        if (android.os.Build.VERSION.SDK_INT >= 26) runCatching { manager?.requestAutofill(view) }
    }
}
