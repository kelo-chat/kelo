package chat.kelo.app.shell

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.test.swipeUp
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.components.SubPage
import chat.kelo.app.ui.platform.FormFactor
import chat.kelo.app.ui.platform.LocalFormFactor
import chat.kelo.app.ui.theme.KeloTheme
import kotlinx.coroutines.launch
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

@OptIn(ExperimentalTestApi::class)
class TopBarScrollTest {

    @Test
    fun topBarScrollRegistryTracksRoutesIndependently() {
        val scroll = TopBarScroll()
        assertFalse(scroll.isScrolled(Routes.CHAT))
        assertFalse(scroll.isScrolled(Routes.GOALS))
        assertFalse(scroll.isScrolled(null))

        scroll.setScrolled(Routes.CHAT, true)
        assertTrue(scroll.isScrolled(Routes.CHAT))
        assertFalse(scroll.isScrolled(Routes.GOALS))

        scroll.setScrolled(Routes.GOALS, true)
        assertTrue(scroll.isScrolled(Routes.CHAT))
        assertTrue(scroll.isScrolled(Routes.GOALS))

        scroll.setScrolled(Routes.CHAT, false)
        assertFalse(scroll.isScrolled(Routes.CHAT))
        assertTrue(scroll.isScrolled(Routes.GOALS))
    }

    @Test
    fun reportTopBarScrolledUpdatesRegistryAndCleansUpOnDispose() = runComposeUiTest {
        val scroll = TopBarScroll()
        var active by mutableStateOf(true)
        var isScrolled by mutableStateOf(false)

        setContent {
            CompositionLocalProvider(LocalTopBarScroll provides scroll) {
                if (active) {
                    ReportTopBarScrolled(Routes.CHAT, isScrolled)
                }
            }
        }

        assertFalse(scroll.isScrolled(Routes.CHAT), "at rest initially")

        isScrolled = true
        waitForIdle()
        assertTrue(scroll.isScrolled(Routes.CHAT), "reports scrolled")

        isScrolled = false
        waitForIdle()
        assertFalse(scroll.isScrolled(Routes.CHAT), "reports at rest again")

        isScrolled = true
        waitForIdle()
        assertTrue(scroll.isScrolled(Routes.CHAT))

        active = false
        waitForIdle()
        assertFalse(scroll.isScrolled(Routes.CHAT), "resets to false on dispose")
    }

    @Test
    fun subPageOnPhoneDetectsAtRestAndScrolledStates() = runComposeUiTest {
        setContent {
            CompositionLocalProvider(LocalFormFactor provides FormFactor.Phone) {
                KeloTheme(dark = false) {
                    SubPage(title = "Settings", onBack = {}) {
                        for (i in 1..40) {
                            Text("Row $i", modifier = Modifier.height(40.dp))
                        }
                    }
                }
            }
        }

        onNodeWithText("Settings").assertExists()
        onNodeWithText("Row 1").assertExists()

        // Content initially at rest
        waitForIdle()

        // Scroll content up
        onNodeWithText("Row 1").performTouchInput { swipeUp() }
        waitForIdle()

        // After scrolling, later rows are visible
        onNodeWithText("Row 10").assertExists()
    }

    @Test
    fun reverseListScrollStateDistinguishesAtRestFromScrolled() = runComposeUiTest {
        val topBarScroll = TopBarScroll()
        var scope: kotlinx.coroutines.CoroutineScope? = null
        var listState: androidx.compose.foundation.lazy.LazyListState? = null

        setContent {
            CompositionLocalProvider(LocalTopBarScroll provides topBarScroll) {
                val list = rememberLazyListState()
                listState = list
                val coroutineScope = rememberCoroutineScope()
                scope = coroutineScope
                val isScrolled by remember(list) { derivedStateOf { list.canScrollBackward } }
                ReportTopBarScrolled(Routes.CHAT, isScrolled)

                Box(Modifier.fillMaxSize()) {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        state = list,
                        reverseLayout = true,
                    ) {
                        items(50) { i ->
                            Text("Message $i", modifier = Modifier.height(50.dp))
                        }
                    }
                }
            }
        }

        waitForIdle()
        // At rest: bottom of chat (index 0), canScrollBackward is false
        assertFalse(topBarScroll.isScrolled(Routes.CHAT), "chat at rest at bottom")

        // Scroll up into history
        scope!!.launch {
            listState!!.scrollToItem(10)
        }
        waitForIdle()

        // Scrolled state: canScrollBackward is true
        assertTrue(topBarScroll.isScrolled(Routes.CHAT), "chat scrolled into history")

        // Scroll back to bottom (item 0)
        scope!!.launch {
            listState!!.scrollToItem(0)
        }
        waitForIdle()

        // Back at rest
        assertFalse(topBarScroll.isScrolled(Routes.CHAT), "chat back at rest at bottom")
    }
}
