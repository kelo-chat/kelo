package chat.kelo.app.ui.components

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.SheetState
import androidx.compose.material3.SheetValue
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp

/**
 * A bottom sheet state that only closes on a real pull: Material's default closes on the slightest downward flick (its fling
 * threshold is tiny). Here a drag must bring the sheet down at least [SheetDismiss.PULL] (or a quarter of its height, whichever is
 * smaller) before it lets go; a shorter drag springs back. A tap on the scrim, back, or a programmatic hide still close at once
 * (they don't move the sheet first, so the distance is ~0).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun rememberKeloSheetState(): SheetState {
    val density = LocalDensity.current
    val pull = with(density) { SheetDismiss.PULL.toPx() }
    val still = with(density) { 2.dp.toPx() }
    val holder = remember { arrayOfNulls<SheetState>(1) }
    val top = remember { mutableFloatStateOf(Float.MAX_VALUE) }   // the open sheet's offset (its highest point)
    val state = rememberModalBottomSheetState(skipPartiallyExpanded = true, confirmValueChange = { target ->
        val s = holder[0]
        if (target != SheetValue.Hidden || s == null || top.floatValue == Float.MAX_VALUE) true
        else {
            val offset = runCatching { s.requireOffset() }.getOrNull() ?: return@rememberModalBottomSheetState true
            SheetDismiss.allow(offset - top.floatValue, pull, still)
        }
    })
    holder[0] = state
    LaunchedEffect(state) {
        snapshotFlow { runCatching { state.requireOffset() }.getOrNull() to state.currentValue }.collect { (off, value) ->
            if (off != null && value == SheetValue.Expanded && off < top.floatValue) top.floatValue = off
        }
    }
    return state
}

object SheetDismiss {
    val PULL = 120.dp
    /** Close when the sheet hasn't been dragged at all (tap outside, back, hide()) or was pulled down far enough. */
    fun allow(dragged: Float, pull: Float, still: Float): Boolean = dragged <= still || dragged >= pull
}
