package chat.kelo.app.ui.components

import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import kotlinx.coroutines.flow.first

/**
 * A list that opens at its top stays there while its content arrives. A lazy list keeps the row that was first on screen, so rows
 * that load in above it (a goal suggestion that comes after the goals, a new approval on the work page) would push the top of the
 * page out of view, under the floating header. Once the person scrolls, the list is theirs and this does nothing.
 * [contentKey] changes whenever rows may have been added or removed (for example their count).
 */
/** Whether [KeepAtTopUntilScrolled] still holds its list at the top. A screen that moves the list itself (a jump to a section) calls
 *  [release] first: a jump that lands in one frame is not seen as a scroll, and the hold would pull the list back to the top. */
@androidx.compose.runtime.Stable
class KeepAtTop {
    var released by mutableStateOf(false)
        private set

    fun release() { released = true }
}

@Composable
fun rememberKeepAtTop(state: LazyListState): KeepAtTop = remember(state) { KeepAtTop() }

/**
 * The hold puts the list back at its top with [LazyListState.requestScrollToItem], which takes no scroll lock: it never waits for or
 * runs after another scroll (with scrollToItem it could finish after a jump that started later and undo it; seen on Windows, 2026-11-15).
 */
@Composable
fun KeepAtTopUntilScrolled(state: LazyListState, contentKey: Any?, keep: KeepAtTop = rememberKeepAtTop(state)) {
    LaunchedEffect(state, keep) { snapshotFlow { state.isScrollInProgress }.first { it }; keep.release() }
    LaunchedEffect(contentKey, keep.released) { if (!keep.released) state.requestScrollToItem(0) }
}
