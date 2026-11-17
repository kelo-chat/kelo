package chat.kelo.app.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.saveable.listSaver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.platform.WindowRules
import chat.kelo.app.ui.theme.Kelo

/** Where the divider between two panes sits: the first pane's share of the width. Saved with the screen; the app may also keep it per device. */
@Stable
class SplitState(initial: Float = WindowRules.DEFAULT_SPLIT) {
    var share by mutableFloatStateOf(initial)
        private set

    /** The divider was dragged to [firstDp] of [totalDp]. */
    fun dragTo(firstDp: Float, totalDp: Float) { share = WindowRules.firstShare(firstDp, totalDp) }

    companion object {
        val Saver = listSaver<SplitState, Float>(save = { listOf(it.share) }, restore = { SplitState(it[0]) })
    }
}

@Composable
fun rememberSplitState(initial: Float = WindowRules.DEFAULT_SPLIT): SplitState = rememberSaveable(saver = SplitState.Saver) { SplitState(initial) }

/**
 * Two panes side by side with a divider the owner can drag (an 8 dp wide touch target on a 1 dp line). Each pane keeps at least
 * [WindowRules.PANE_MIN]. [onSplit] reports a finished drag (to keep it per device).
 */
@Composable
fun TwoPane(
    first: @Composable () -> Unit,
    second: @Composable () -> Unit,
    split: SplitState,
    modifier: Modifier = Modifier,
    /** False: the first pane takes the whole width (a tab that has nothing for the second). The first pane keeps its place in the tree either way. */
    showSecond: Boolean = true,
    onSplit: (Float) -> Unit = {},
) {
    val density = LocalDensity.current
    BoxWithConstraints(modifier.fillMaxSize()) {
        val totalDp = maxWidth.value
        val share = WindowRules.firstShare(split.share * totalDp, totalDp)
        val firstWidth = if (showSecond) (totalDp * share).dp else maxWidth
        Box(Modifier.width(firstWidth).fillMaxHeight()) { first() }
        if (showSecond) Box(Modifier.align(Alignment.TopEnd).width((totalDp - firstWidth.value).dp).fillMaxHeight()) { second() }
        if (showSecond) Box(
            Modifier.offset(x = firstWidth - 4.dp).width(8.dp).fillMaxHeight()
                .pointerInput(totalDp) {
                    var startDp = 0f
                    detectHorizontalDragGestures(
                        onDragStart = { startDp = WindowRules.firstShare(split.share * totalDp, totalDp) * totalDp },
                        onDragEnd = { onSplit(split.share) },
                        onHorizontalDrag = { change, delta ->
                            change.consume()
                            startDp += with(density) { delta.toDp().value }
                            split.dragTo(startDp, totalDp)
                        },
                    )
                },
            contentAlignment = Alignment.Center,
        ) {
            Box(Modifier.width(1.dp).fillMaxHeight().background(Kelo.colors.line))
        }
    }
}
