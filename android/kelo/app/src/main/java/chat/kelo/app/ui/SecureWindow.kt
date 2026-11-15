package chat.kelo.app.ui

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.view.WindowManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.platform.LocalContext

/**
 * Keeps the window out of screenshots, screen recordings and the recent-apps preview (FLAG_SECURE) while any secret screen or
 * form is on screen. Several can be shown at once (a page and a card), so the flag is counted and cleared when the last one goes.
 */
object SecureWindowCount {
    private var count = 0

    /** Returns whether the flag should now be on. */
    @Synchronized
    fun enter(): Boolean { count++; return count > 0 }

    @Synchronized
    fun leave(): Boolean { count = (count - 1).coerceAtLeast(0); return count > 0 }

    @Synchronized
    fun reset() { count = 0 }
}

@Composable
fun SecureWindow() {
    val activity = LocalContext.current.findActivity() ?: return
    if (screenshotsAllowedForDebugging(activity)) return
    DisposableEffect(activity) {
        if (SecureWindowCount.enter()) activity.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        onDispose {
            if (!SecureWindowCount.leave()) activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }
}

fun Context.findActivity(): Activity? {
    var c: Context? = this
    while (c is ContextWrapper) {
        if (c is Activity) return c
        c = c.baseContext
    }
    return null
}

/**
 * Only a debuggable build (never the release APK) can be told to allow screenshots of secret screens, for checking layouts on an
 * emulator: `adb shell settings put global kelo_debug_screens 1` (and `settings delete global kelo_debug_screens` to undo).
 */
private fun screenshotsAllowedForDebugging(activity: Activity): Boolean {
    if (activity.applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE == 0) return false
    return runCatching { android.provider.Settings.Global.getString(activity.contentResolver, "kelo_debug_screens") == "1" }.getOrDefault(false)
}
