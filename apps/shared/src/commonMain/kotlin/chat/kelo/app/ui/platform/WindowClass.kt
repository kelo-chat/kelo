package chat.kelo.app.ui.platform

import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

/**
 * How wide the window is right now, in dp. Decided from the window (a split screen, a folded cover screen, an unfolded
 * foldable, a tablet, a desktop window), never from "is it a foldable".
 */
enum class WidthClass { Narrow, Middle, Wide, Roomy }

/** The window the UI is drawn in: its width class and height. Provided once near the top of each app; the default is a phone. */
@Immutable
data class WindowShape(val width: WidthClass, val heightDp: Int) {
    /** One page centred in a column: from 600 dp on. */
    val centred: Boolean get() = width != WidthClass.Narrow

    /** Two panes side by side: from 840 dp wide, and only when the window is at least 480 dp tall (a landscape phone stays one pane). */
    val twoPane: Boolean get() = (width == WidthClass.Wide || width == WidthClass.Roomy) && heightDp >= WindowRules.TWO_PANE_MIN_HEIGHT

    companion object {
        val Phone = WindowShape(WidthClass.Narrow, 800)
    }
}

val LocalWindowShape = staticCompositionLocalOf { WindowShape.Phone }

/** The numbers behind the wide layouts, in one place (so a test can pin them and iOS can use the same vectors). */
object WindowRules {
    const val MIDDLE_FROM = 600
    const val WIDE_FROM = 840
    const val ROOMY_FROM = 1200
    const val TWO_PANE_MIN_HEIGHT = 480

    /** The chat column in a window that is not narrow. */
    const val CHAT_COLUMN_MIDDLE = 720
    const val CHAT_COLUMN_WIDE = 860
    /** The widest a chat bubble gets (about 65 characters a line) once the window is not narrow. */
    const val BUBBLE_MAX = 540
    /** Other pages (lists, settings) in a centred column. */
    const val PAGE_COLUMN = 640
    /** The tab bar's own width cap when it floats in a wide window. */
    const val TAB_BAR_MAX = 560

    /** Each pane keeps at least this much width when the divider is dragged. */
    const val PANE_MIN = 320
    const val DEFAULT_SPLIT = 0.55f

    fun widthClassFor(widthDp: Float): WidthClass = when {
        widthDp >= ROOMY_FROM -> WidthClass.Roomy
        widthDp >= WIDE_FROM -> WidthClass.Wide
        widthDp >= MIDDLE_FROM -> WidthClass.Middle
        else -> WidthClass.Narrow
    }

    fun shapeFor(widthDp: Float, heightDp: Float) = WindowShape(widthClassFor(widthDp), heightDp.toInt())

    fun chatColumn(shape: WindowShape): Dp? = when (shape.width) {
        WidthClass.Narrow -> null
        WidthClass.Middle -> CHAT_COLUMN_MIDDLE.dp
        else -> CHAT_COLUMN_WIDE.dp
    }

    fun bubbleMax(shape: WindowShape): Dp? = if (shape.centred) BUBBLE_MAX.dp else null

    /** The page frame a phone-style window gets: centred and at most this wide (null = fill the window). */
    fun pageColumn(shape: WindowShape): Dp? = if (shape.centred) PAGE_COLUMN.dp else null

    /**
     * The first pane's share of [totalDp] after the divider moved to [wantedFirstDp]: each pane keeps [PANE_MIN]; a window too
     * narrow for two minimums keeps the default share.
     */
    fun firstShare(wantedFirstDp: Float, totalDp: Float): Float {
        if (totalDp < PANE_MIN * 2f) return DEFAULT_SPLIT
        return (wantedFirstDp.coerceIn(PANE_MIN.toFloat(), totalDp - PANE_MIN) / totalDp)
    }

    /** What the second pane shows. */
    enum class Side { None, Live, Work }

    /**
     * The chat tab in a two-pane window: the live browser / phone view when one is open, else the Work list (never an empty pane).
     * Other tabs, and a window that is not two-pane, have no side pane (a tab's own list-detail needs none).
     */
    fun side(shape: WindowShape, onChatTab: Boolean, liveOpen: Boolean): Side = when {
        !shape.twoPane || !onChatTab -> Side.None
        liveOpen -> Side.Live
        else -> Side.Work
    }
}
