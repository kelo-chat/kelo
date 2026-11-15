package chat.kelo.app.ui

import android.os.Build
import android.view.HapticFeedbackConstants
import android.view.View
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalView

/** The feedback kinds the app uses. */
enum class Haptic { CONFIRM, REJECT, LONG_PRESS, TICK }

/** Which HapticFeedbackConstants value a kind maps to on [sdk] (CONFIRM / REJECT exist from API 30; older phones get the nearest). */
object HapticMap {
    fun constant(kind: Haptic, sdk: Int): Int = when (kind) {
        Haptic.CONFIRM -> if (sdk >= 30) HapticFeedbackConstants.CONFIRM else HapticFeedbackConstants.VIRTUAL_KEY
        Haptic.REJECT -> if (sdk >= 30) HapticFeedbackConstants.REJECT else HapticFeedbackConstants.LONG_PRESS
        Haptic.LONG_PRESS -> HapticFeedbackConstants.LONG_PRESS
        Haptic.TICK -> HapticFeedbackConstants.CLOCK_TICK
    }
}

/** Plays haptic feedback on [view]; it follows the user's system touch-feedback setting. */
@Immutable
class Haptics(private val view: View) {
    fun perform(kind: Haptic) {
        view.performHapticFeedback(HapticMap.constant(kind, Build.VERSION.SDK_INT))
    }
    fun confirm() = perform(Haptic.CONFIRM)
    fun reject() = perform(Haptic.REJECT)
    fun longPress() = perform(Haptic.LONG_PRESS)
    fun tick() = perform(Haptic.TICK)
}

@Composable
fun rememberHaptics(): Haptics {
    val view = LocalView.current
    return remember(view) { Haptics(view) }
}
