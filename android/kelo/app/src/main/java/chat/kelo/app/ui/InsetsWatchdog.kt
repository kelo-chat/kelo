package chat.kelo.app.ui

import android.os.Build
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.view.WindowInsets
import android.view.WindowInsetsAnimation
import androidx.annotation.RequiresApi

/**
 * Keeps Compose's window insets from getting stuck.
 *
 * Compose stops taking new insets while a window-insets animation is "prepared" and waits for that animation's start or end. The
 * system sometimes prepares an animation and then drops it without ever starting or ending it: the keyboard closing while the
 * focus moves to a dialog (pairing's 연결 confirmation), or the system bars animating while the launch screen still holds the first
 * frame back. Compose then keeps the insets it had (no status bar height, or a keyboard that is gone) until the app is started
 * again: the header sat a status bar higher on some launches, and the tab bar stayed away after pairing.
 *
 * This watches the same animations one level up (the activity's content view, before the Compose view gets them). One that was
 * prepared and has not started after [GRACE_MS] is ended for the views below, which makes Compose apply the newest insets.
 * Compose itself does this only on Android 11 (API 30); before that the animations are not the system's, so this is for API 30+.
 */
@RequiresApi(Build.VERSION_CODES.R)
class InsetsWatchdog private constructor(private val host: ViewGroup) :
    WindowInsetsAnimation.Callback(DISPATCH_MODE_CONTINUE_ON_SUBTREE) {

    private val waiting = HashSet<WindowInsetsAnimation>()

    override fun onPrepare(animation: WindowInsetsAnimation) {
        waiting += animation
        host.postDelayed({ endIfNeverStarted(animation) }, GRACE_MS)
    }

    override fun onStart(animation: WindowInsetsAnimation, bounds: WindowInsetsAnimation.Bounds): WindowInsetsAnimation.Bounds {
        waiting -= animation
        return bounds
    }

    override fun onProgress(insets: WindowInsets, running: MutableList<WindowInsetsAnimation>): WindowInsets = insets

    override fun onEnd(animation: WindowInsetsAnimation) {
        waiting -= animation
    }

    private fun endIfNeverStarted(animation: WindowInsetsAnimation) {
        if (!waiting.remove(animation)) return
        Log.i(TAG, "an insets animation was prepared but never started; ending it so the screen takes the current insets")
        for (i in 0 until host.childCount) host.getChildAt(i).dispatchWindowInsetsAnimationEnd(animation)
        host.requestApplyInsets()
    }

    companion object {
        private const val TAG = "KeloInsets"
        /** An animation that has not started by then is not going to */
        const val GRACE_MS = 300L

        /** Watches [content] (the activity's android.R.id.content). Does nothing before API 30. */
        fun install(content: View) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return
            val group = content as? ViewGroup ?: return
            group.setWindowInsetsAnimationCallback(InsetsWatchdog(group))
        }
    }
}
