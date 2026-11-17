package chat.kelo.app.ui.platform

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.test.swipeRight
import androidx.compose.ui.unit.dp
import chat.kelo.app.chat.UserBubble
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.components.TwoPane
import chat.kelo.app.ui.components.rememberSplitState
import chat.kelo.app.ui.theme.KeloTheme
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The wide-window rules (breakpoints, columns, panes) and what the shared screens do with them. The iOS helper uses the same vectors. */
@OptIn(ExperimentalTestApi::class)
class WindowRulesTest {
    @Test fun widthClassesBreakAt600840And1200() {
        val vectors = listOf(
            0f to WidthClass.Narrow, 360f to WidthClass.Narrow, 599f to WidthClass.Narrow, 600f to WidthClass.Middle, 673f to WidthClass.Middle,
            839f to WidthClass.Middle, 840f to WidthClass.Wide, 841f to WidthClass.Wide, 1199f to WidthClass.Wide, 1200f to WidthClass.Roomy, 1280f to WidthClass.Roomy,
        )
        for ((w, want) in vectors) assertEquals(want, WindowRules.widthClassFor(w), "width $w")
    }

    @Test fun twoPanesNeedWidth840AndHeight480() {
        assertTrue(WindowRules.shapeFor(841f, 673f).twoPane)
        assertTrue(WindowRules.shapeFor(840f, 480f).twoPane)
        assertTrue(!WindowRules.shapeFor(839f, 900f).twoPane)
        assertTrue(!WindowRules.shapeFor(900f, 479f).twoPane, "a landscape phone stays one pane")
        assertTrue(!WindowRules.shapeFor(673f, 841f).twoPane)
    }

    @Test fun columnsAndBubblesFollowTheWidthClass() {
        val narrow = WindowRules.shapeFor(412f, 800f)
        assertEquals(null, WindowRules.chatColumn(narrow)); assertEquals(null, WindowRules.bubbleMax(narrow)); assertEquals(null, WindowRules.pageColumn(narrow))
        val middle = WindowRules.shapeFor(673f, 841f)
        assertEquals(720.dp, WindowRules.chatColumn(middle)); assertEquals(540.dp, WindowRules.bubbleMax(middle)); assertEquals(640.dp, WindowRules.pageColumn(middle))
        assertEquals(860.dp, WindowRules.chatColumn(WindowRules.shapeFor(900f, 600f)))
        assertEquals(860.dp, WindowRules.chatColumn(WindowRules.shapeFor(1280f, 800f)))
    }

    @Test fun theSidePaneIsLiveWhenOneIsOpenElseWorkAndOnlyOnTheChatTabInTwoPanes() {
        val wide = WindowRules.shapeFor(841f, 673f)
        assertEquals(WindowRules.Side.Work, WindowRules.side(wide, onChatTab = true, liveOpen = false))
        assertEquals(WindowRules.Side.Live, WindowRules.side(wide, onChatTab = true, liveOpen = true))
        assertEquals(WindowRules.Side.None, WindowRules.side(wide, onChatTab = false, liveOpen = true))
        assertEquals(WindowRules.Side.None, WindowRules.side(WindowRules.shapeFor(673f, 841f), onChatTab = true, liveOpen = true))
    }

    @Test fun theDividerKeepsEachPaneAtLeast320AndAWindowTooNarrowKeepsTheDefault() {
        assertEquals(320f / 900f, WindowRules.firstShare(10f, 900f))
        assertEquals(580f / 900f, WindowRules.firstShare(890f, 900f))
        assertEquals(500f / 1000f, WindowRules.firstShare(500f, 1000f))
        assertEquals(WindowRules.DEFAULT_SPLIT, WindowRules.firstShare(100f, 600f))
    }

    /** The width of a long user bubble's text (the bubble minus its 15 dp padding each side) in a 900 dp wide row. */
    private fun bubbleTextWidthPx(shape: WindowShape): Int {
        var px = 0
        runComposeUiTest {
            setContent {
                CompositionLocalProvider(LocalWindowShape provides shape) {
                    KeloTheme(dark = false) { Box(Modifier.width(900.dp)) { UserBubble("word ".repeat(200), "", emptyList()) {} } }
                }
            }
            px = onNode(hasText("word", substring = true)).fetchSemanticsNode().size.width
        }
        return px
    }

    @Test fun aBubbleFillsTheRowOnAPhone() {
        val w = bubbleTextWidthPx(WindowShape.Phone)
        assertTrue(w > 700, "the phone layout is not capped: $w")
    }

    @Test fun aBubbleStopsAt540InAWideWindow() {
        val w = bubbleTextWidthPx(WindowRules.shapeFor(900f, 600f))
        assertTrue(w <= 540 && w > 400, "capped at 540 dp: $w")
    }

    @Test fun aSubPageIsACentredColumnWhenTheFrameSaysSo() = runComposeUiTest {
        setContent {
            CompositionLocalProvider(LocalPageFrame provides PageFrame(0.dp, 640.dp)) {
                KeloTheme(dark = false) { Box(Modifier.width(1000.dp).height(600.dp)) { SubPage("Title", onBack = {}) { Box(Modifier.fillMaxWidth().height(20.dp).testTag("row")) } } }
            }
        }
        val w = onNodeWithTag("row").fetchSemanticsNode().size.width
        assertTrue(w <= 640, "the page body is at most 640 dp wide: $w")
    }

    @Test fun draggingTheDividerMovesThePaneEdgeAndStopsAt320() = runComposeUiTest {
        setContent {
            KeloTheme(dark = false) {
                Box(Modifier.width(1000.dp).height(400.dp)) {
                    TwoPane(first = { Box(Modifier.fillMaxSize().testTag("first")) }, second = { Box(Modifier.fillMaxSize().testTag("second")) }, split = rememberSplitState())
                }
            }
        }
        val before = onNodeWithTag("first").fetchSemanticsNode().size.width
        assertEquals(550, before)
        onNodeWithTag("first").performTouchInput { down(center.copy(x = width - 2f)); moveBy(androidx.compose.ui.geometry.Offset(200f, 0f)); up() }
        // a drag started on the divider (its 8 dp target straddles the edge) moves the edge right
        val after = onNodeWithTag("first").fetchSemanticsNode().size.width
        assertTrue(after > before, "the first pane grew: $before -> $after")
        onNodeWithTag("first").performTouchInput { down(center.copy(x = width - 2f)); moveBy(androidx.compose.ui.geometry.Offset(5000f, 0f)); up() }
        val last = onNodeWithTag("second").fetchSemanticsNode().size.width
        assertTrue(last >= 320, "the second pane keeps 320 dp: $last")
    }
}
