package chat.kelo.app.ui.theme

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.SpringSpec
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.interaction.InteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer

/**
 * The desktop copy of android/kelo .../ui/theme/Motion.kt (the same Material 3 motion tokens), without Android's system setting:
 * on the desktop [LocalReduceMotion] follows the app's own "reduce motion" choice.
 */
object Motion {
    val EmphasizedDecelerate = CubicBezierEasing(0.05f, 0.7f, 0.1f, 1f)
    val EmphasizedAccelerate = CubicBezierEasing(0.3f, 0f, 0.8f, 0.15f)
    val Standard = CubicBezierEasing(0.2f, 0f, 0f, 1f)

    const val ENTER_MS = 400
    const val EXIT_MS = 200
    const val STANDARD_MS = 300
    const val SMALL_MS = 150
    const val PRESS_MS = 100
    const val PRESS_SHRINK = 0.96f

    fun <T> enter(reduce: Boolean = false): FiniteAnimationSpec<T> = if (reduce) snap() else tween(ENTER_MS, easing = EmphasizedDecelerate)
    fun <T> exit(reduce: Boolean = false): FiniteAnimationSpec<T> = if (reduce) snap() else tween(EXIT_MS, easing = EmphasizedAccelerate)
    fun <T> standard(reduce: Boolean = false): FiniteAnimationSpec<T> = if (reduce) snap() else tween(STANDARD_MS, easing = Standard)
    fun <T> small(reduce: Boolean = false): FiniteAnimationSpec<T> = if (reduce) snap() else tween(SMALL_MS, easing = Standard)
    fun <T> rise(): SpringSpec<T> = spring(dampingRatio = 0.9f, stiffness = 700f)
    fun <T> bump(): SpringSpec<T> = spring(dampingRatio = 0.6f, stiffness = 800f)
}

/** True when the user asked for less motion (My → App). The character then holds a still picture. */
val LocalReduceMotion = staticCompositionLocalOf { false }

/** Shrinks the element a little while [source] is pressed. Does nothing with reduced motion. */
fun Modifier.pressShrink(source: InteractionSource, scale: Float = Motion.PRESS_SHRINK): Modifier = composed {
    val reduce = LocalReduceMotion.current
    val pressed by source.collectIsPressedAsState()
    val s by animateFloatAsState(
        targetValue = if (pressed && !reduce) scale else 1f,
        animationSpec = if (reduce) snap() else tween(if (pressed) Motion.PRESS_MS else Motion.SMALL_MS, easing = Motion.Standard),
        label = "pressShrink",
    )
    graphicsLayer { scaleX = s; scaleY = s }
}
