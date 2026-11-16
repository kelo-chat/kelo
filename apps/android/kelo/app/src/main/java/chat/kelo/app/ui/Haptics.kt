package chat.kelo.app.ui

import android.os.Build
import android.view.HapticFeedbackConstants
import android.view.View
import chat.kelo.app.ui.platform.Haptic
import chat.kelo.app.ui.platform.Haptics

/** Which HapticFeedbackConstants value a kind maps to on [sdk] (CONFIRM / REJECT exist from API 30; older phones get the nearest). */
object HapticMap {
    fun constant(kind: Haptic, sdk: Int): Int = when (kind) {
        Haptic.CONFIRM -> if (sdk >= 30) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.VIRTUAL_KEY
        Haptic.REJECT -> if (sdk >= 30) HapticFeedbackConstants.REJECT else HapticFeedbackConstants.LONG_PRESS
        Haptic.LONG_PRESS -> HapticFeedbackConstants.LONG_PRESS
        Haptic.TICK -> HapticFeedbackConstants.CLOCK_TICK
    }
}

/** Plays haptic feedback on [view]; it follows the user's system touch-feedback setting. The Android root provides it as LocalHaptics. */
class AndroidHaptics(private val view: View) : Haptics {
    override fun perform(kind: Haptic) {
        view.performHapticFeedback(HapticMap.constant(kind, Build.VERSION.SDK_INT))
    }
}
