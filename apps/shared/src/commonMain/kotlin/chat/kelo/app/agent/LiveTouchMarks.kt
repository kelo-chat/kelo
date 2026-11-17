package chat.kelo.app.agent

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity

/** Where the owner's own touch is, drawn over the picture: a ring under the finger, a ring and dot where the tap landed, a dashed ring with a cross where it did not reach the page. */
object TouchMarkLook {
    val MISSED = Color(0xFFD32F2F)
    val OUTLINE = Color(0xFF1E1E1E)
    const val DOWN_RADIUS_DP = 22f
    const val LANDED_RADIUS_DP = 14f
    const val DOT_RADIUS_DP = 4f
    const val MISSED_RADIUS_DP = 16f
    /** How long a mark stays and fades (a missed one stays longer: it explains why nothing happened). */
    fun fadeMs(m: TouchMark): Int = if (m.kind == MarkKind.MISSED) TouchMarkRules.FADE_MS * 2 else TouchMarkRules.FADE_MS
}

/**
 * [progress] is how far the [mark] has faded: 0 = just drawn, 1 = gone. [down] is the finger on the glass (drawn at once, before
 * anything is sent).
 */
@Composable
fun TouchMarksLayer(down: Offset?, mark: TouchMark?, progress: Float, accent: Color, modifier: Modifier = Modifier.fillMaxSize()) {
    val dp = LocalDensity.current.density
    Canvas(modifier) {
        if (down != null) ring(down, TouchMarkLook.DOWN_RADIUS_DP * dp, Color.White, 0.9f, dp)
        if (mark != null && progress < 1f) {
            val a = 1f - progress
            when (mark.kind) {
                MarkKind.LANDED -> {
                    ring(mark.at, TouchMarkLook.LANDED_RADIUS_DP * dp * (1f + 0.5f * progress), Color.White, a, dp)
                    drawCircle(TouchMarkLook.OUTLINE.copy(alpha = a), (TouchMarkLook.DOT_RADIUS_DP + 1.5f) * dp, mark.at)
                    drawCircle(accent.copy(alpha = a), TouchMarkLook.DOT_RADIUS_DP * dp, mark.at)
                }
                MarkKind.MISSED -> missed(mark.at, TouchMarkLook.MISSED_RADIUS_DP * dp, a, dp)
            }
        }
    }
}

private fun DrawScope.ring(at: Offset, r: Float, color: Color, alpha: Float, dp: Float) {
    drawCircle(TouchMarkLook.OUTLINE.copy(alpha = alpha * 0.45f), r + 1.5f * dp, at, style = Stroke(2.5f * dp))
    drawCircle(color.copy(alpha = alpha), r, at, style = Stroke(1.5f * dp))
}

private fun DrawScope.missed(at: Offset, r: Float, alpha: Float, dp: Float) {
    val c = TouchMarkLook.MISSED.copy(alpha = alpha)
    drawCircle(Color.White.copy(alpha = alpha * 0.9f), r + 1.5f * dp, at, style = Stroke(3.5f * dp))
    drawCircle(c, r, at, style = Stroke(2f * dp, pathEffect = PathEffect.dashPathEffect(floatArrayOf(5f * dp, 4f * dp))))
    val k = r * 0.4f
    drawLine(c, Offset(at.x - k, at.y - k), Offset(at.x + k, at.y + k), 2f * dp)
    drawLine(c, Offset(at.x - k, at.y + k), Offset(at.x + k, at.y - k), 2f * dp)
}
