package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.SemanticsNodeInteraction
import androidx.compose.ui.test.hasScrollToIndexAction
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isFocused
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performKeyInput
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.pressKey
import androidx.compose.ui.unit.dp
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatViewModel
import chat.kelo.app.chat.HIT_TAG
import chat.kelo.app.res.*
import chat.kelo.desktop.chat.DesktopChat
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.ui.HeaderHeight
import chat.kelo.desktop.ui.KeloFrame
import chat.kelo.desktop.ui.Page
import chat.kelo.desktop.ui.Shell
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The chat's search on the desktop, as on the phone (apps/shared ChatSearchScreen, ChatSearchViewModel; the server's
 * GET /api/messages/search and /around are answered by tools/mock_parts/chat_search.py). The header's magnifier sits at the page's
 * top end (not on the agent's name pill); clicking it, or Cmd/Ctrl+F, opens the search page under the header; a result opens the chat
 * at its message, and the messages after it load a page at a time; Escape and any navigation close the page.
 */
@OptIn(ExperimentalTestApi::class)
class ChatSearchTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private val servers = mutableListOf<MockServer>()
    private val ko = Words.of("ko")

    @After fun stop() { servers.forEach { it.stop() } }

    private fun server(vararg args: String, env: Map<String, String> = emptyMap()): MockServer =
        MockServer(*args, env = env).also { servers += it }

    /** The window (1024x768 unless given): the chat by default, [page] switchable from the test. The page box is tagged for bounds. */
    private fun setWindow(ui: UiSteps, app: AppContainer, chatHandle: DesktopChat, page: MutableState<Page>, width: Int = 1024, height: Int = 768) {
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Box(Modifier.size(width.dp, height.dp).testTag(PAGE_TAG)) {
                    Shell(app, chatHandle.vm, page.value, onPage = { page.value = it }, onSignedOut = {}, connectorToOpen = null,
                        onConnectorOpened = {}, side = chatHandle.side)
                }
            }
        }
    }

    private val searchLabel get() = ko.get(Res.string.chat_search_open)
    private val pageTitle get() = ko.get(Res.string.chat_search_title)
    private val desktopHint get() = ko.get(Res.string.desktop_chat_search_hint)
    private val phoneHint get() = ko.get(Res.string.chat_search_hint)
    private val noneLine get() = ko.get(Res.string.chat_search_none)
    private val failedLine get() = ko.get(Res.string.chat_search_failed)
    private val goalsTab get() = ko.get(Res.string.tab_goals)

    private fun searchButtons() = compose.onAllNodesWithContentDescription(searchLabel).fetchSemanticsNodes()
    private fun openSearch() = compose.onAllNodesWithContentDescription(searchLabel).onFirst().performClick()
    private fun titleShown() = compose.onAllNodesWithText(pageTitle).fetchSemanticsNodes().isNotEmpty()
    private fun hits() = compose.onAllNodes(hasTestTag(HIT_TAG)).fetchSemanticsNodes()
    /** The search field: the one with focus (the field takes it when the page opens; the chat's composer is under the page) */
    private fun field(): SemanticsNodeInteraction = compose.onNode(hasSetTextAction() and isFocused())
    private fun msgIds(chat: ChatViewModel): List<Long> = chat.state.value.items.filterIsInstance<ChatItem.Msg>().mapNotNull { it.id }

    /** The chat's list scrolled to its newest end: the messages after the last one shown arrive (one page of 50 at most). */
    private fun loadNewerPage(ui: UiSteps, chat: ChatViewModel) {
        val before = msgIds(chat).maxOrNull() ?: 0L
        compose.onNode(hasScrollToIndexAction()).performScrollToIndex(0)
        // the next page adds the messages after [before]; the page that reaches the newest message adds none and ends the window
        ui.waitUntil("the messages after $before") { (msgIds(chat).maxOrNull() ?: 0L) > before || !chat.state.value.hasNewer }
    }

    /** Waits for [ok] without moving the test clock (a debounce on the clock must not finish on its own). */
    private fun waitWithoutClock(what: String, timeout: Long = 10_000, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + timeout
        while (!ok()) {
            if (System.currentTimeMillis() > end) throw AssertionError("timed out waiting for $what")
            Thread.sleep(25)
        }
    }

    private fun shotBoth(ui: UiSteps, app: AppContainer, name: String) {
        ui.shot("$name-light")
        app.settings.setTheme("dark")
        ui.shot("$name-dark")
        app.settings.setTheme("light")
        ui.settle()
    }

    @Test fun theChatHeaderHasASearchButtonAtThePagesEnd() {
        val m = server("--lang", "ko")
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val ui = UiSteps(compose, parityShots())
        val page = mutableStateOf(Page.CHAT)
        setWindow(ui, app, chatHandle, page)
        ui.waitUntil("the profile") { app.profile.value != null }
        val agentName = app.profile.value!!.agentName.ifBlank { "Kelo" }
        ui.waitUntil("the chat's search button") { searchButtons().isNotEmpty() }

        val pageBox = compose.onNodeWithTag(PAGE_TAG).fetchSemanticsNode().boundsInRoot
        val icon = searchButtons().single().boundsInRoot
        val density = compose.density
        val edge = with(density) { 24.dp.toPx() }
        assertTrue("the search button sits at the page's top end (right gap ${pageBox.right - icon.right}px)",
            pageBox.right - icon.right <= edge)
        assertTrue("the search button is in the header's band (top ${icon.top}px)", icon.top < with(density) { HeaderHeight.toPx() })
        val nameBounds = compose.onAllNodes(hasText(agentName), useUnmergedTree = true).fetchSemanticsNodes()
            .map { it.boundsInRoot }.filter { it.top < with(density) { HeaderHeight.toPx() } }
        assertTrue("the search button does not cover the agent's name pill", nameBounds.none { it.overlaps(icon) })

        // on the other pages there is no search button
        compose.runOnIdle { page.value = Page.GOALS }
        ui.settle()
        assertTrue("no search button on the Goals page", searchButtons().isEmpty())
        chatHandle.close()
    }

    @Test fun aResultOpensTheChatAtThatMessageAndTheRestLoadsPageByPage() {
        val m = server("--lang", "ko", env = mapOf("KELO_MOCK_LONG_HISTORY" to "300"))
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val chat = chatHandle.vm
        val ui = UiSteps(compose, parityShots())
        setWindow(ui, app, chatHandle, mutableStateOf(Page.CHAT))
        ui.waitUntil("the newest messages") { msgIds(chat).contains(300L) }
        ui.waitUntil("the search button") { searchButtons().isNotEmpty() }

        openSearch()
        ui.waitUntil("the search page") { titleShown() }
        ui.waitText(desktopHint)
        field().performTextInput("40번째 답")
        ui.waitUntil("two results") { hits().size == 2 }
        // newest first: the id-280 row (140번째 답) above the id-80 row (40번째 답)
        val rows = hits().map { it.boundsInRoot }
        assertTrue("the newer result is above the older one", rows[0].top < rows[1].top)
        shotBoth(ui, app, "parity-chat-search-results")

        // the older result: the page closes, the chat holds the window around message 80, with newer messages to load
        compose.onAllNodes(hasTestTag(HIT_TAG))[1].performClick()
        ui.waitUntil("the search page closes") { !titleShown() }
        ui.waitUntil("the window around message 80") { msgIds(chat).contains(80L) }
        assertEquals((70L..90L).toList(), msgIds(chat))
        assertTrue("messages after the window load", chat.state.value.hasNewer)
        ui.waitUntil("the focus is used up") { app.chatFocus.value == null }

        // scrolled down: the messages after the window load in order (50 at a time), to the newest message
        loadNewerPage(ui, chat)
        val first = msgIds(chat)
        assertEquals("the messages arrive in order, with no gap", (70L..first.last()).toList(), first)
        assertTrue("a page of 50 after message 90 arrived (to ${first.last()})", first.last() >= 140L)
        var guard = 0
        while (chat.state.value.hasNewer && guard++ < 10) loadNewerPage(ui, chat)
        assertEquals((70L..300L).toList(), msgIds(chat))
        assertFalse("the newest message is in the chat", chat.state.value.hasNewer)
        chatHandle.close()
    }

    @Test fun aResultThatIsAlreadyLoadedOpensWithoutAWindow() {
        val m = server("--lang", "ko", env = mapOf("KELO_MOCK_LONG_HISTORY" to "300"))
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val chat = chatHandle.vm
        val ui = UiSteps(compose, parityShots())
        setWindow(ui, app, chatHandle, mutableStateOf(Page.CHAT))
        ui.waitUntil("the newest messages") { msgIds(chat).contains(300L) }
        ui.waitUntil("the search button") { searchButtons().isNotEmpty() }

        openSearch()
        ui.waitUntil("the search page") { titleShown() }
        field().performTextInput("40번째 답")
        ui.waitUntil("two results") { hits().size == 2 }
        compose.onAllNodes(hasTestTag(HIT_TAG))[0].performClick()
        ui.waitUntil("the search page closes") { !titleShown() }
        ui.waitUntil("the message 280 is in the chat") { msgIds(chat).contains(280L) }
        assertFalse("a loaded result needs no window", chat.state.value.hasNewer)
        chatHandle.close()
    }

    @Test fun escapeAndAnotherPageCloseTheSearch() {
        val m = server("--lang", "ko")
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val ui = UiSteps(compose, parityShots())
        val page = mutableStateOf(Page.CHAT)
        setWindow(ui, app, chatHandle, page)
        ui.waitUntil("the search button") { searchButtons().isNotEmpty() }

        openSearch()
        ui.waitUntil("the search page") { titleShown() }
        field().performKeyInput { pressKey(Key.Escape) }
        ui.waitUntil("Escape closes the search page") { !titleShown() }
        ui.waitUntil("the search button is back") { searchButtons().isNotEmpty() }

        openSearch()
        ui.waitUntil("the search page again") { titleShown() }
        compose.onAllNodesWithContentDescription(goalsTab).onFirst().performClick()
        ui.waitUntil("the Goals page") { page.value == Page.GOALS }
        assertFalse("another page closes the search", app.chatSearchOpen.value)

        compose.runOnIdle { page.value = Page.CHAT }
        ui.settle()
        assertFalse("back on the chat the search is not open", titleShown())
        chatHandle.close()
    }

    @Test fun emptyAndNoneStates() {
        val m = server("--lang", "ko")
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val ui = UiSteps(compose, parityShots())
        setWindow(ui, app, chatHandle, mutableStateOf(Page.CHAT))
        ui.waitUntil("the search button") { searchButtons().isNotEmpty() }

        openSearch()
        ui.waitUntil("the search page") { titleShown() }
        ui.waitText(desktopHint)
        assertFalse("the phone's hint is not on the desktop", ui.has(phoneHint))

        // Enter runs the search at once: the clock moves less than the 250 ms pause before the result line appears
        field().performTextInput("없는말xyz")
        field().performKeyInput { pressKey(Key.Enter) }
        compose.mainClock.advanceTimeBy(100)
        waitWithoutClock("the none line") { ui.has(noneLine) }
        assertFalse("the desktop hint is gone once a query is typed", ui.has(desktopHint))
        chatHandle.close()
    }

    @Test fun aFailedSearchSaysSo() {
        val m = server("--lang", "ko", env = mapOf("KELO_MOCK_CHAT_SEARCH_FAIL" to "1"))
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val ui = UiSteps(compose, parityShots())
        setWindow(ui, app, chatHandle, mutableStateOf(Page.CHAT))
        ui.waitUntil("the search button") { searchButtons().isNotEmpty() }

        openSearch()
        ui.waitUntil("the search page") { titleShown() }
        field().performTextInput("40번째 답")
        ui.waitText(failedLine)
        chatHandle.close()
    }

    @Test fun theSearchAtTheWindowsMinimumSize() {
        val m = server("--lang", "ko", env = mapOf("KELO_MOCK_LONG_HISTORY" to "300"))
        val app = m.app(tmp.newFolder(), lang = "ko")
        val chatHandle = DesktopChat(app)
        val chat = chatHandle.vm
        val ui = UiSteps(compose, parityShots())
        setWindow(ui, app, chatHandle, mutableStateOf(Page.CHAT), width = 520, height = 560)
        ui.waitUntil("the newest messages") { msgIds(chat).contains(300L) }
        ui.waitUntil("the search button") { searchButtons().isNotEmpty() }
        ui.shot("parity-chat-header-520-light")

        openSearch()
        ui.waitUntil("the search page") { titleShown() }
        ui.waitText(desktopHint)
        field().performTextInput("40번째 답")
        ui.waitUntil("two results") { hits().size == 2 }
        ui.shot("parity-chat-search-520-light")
        chatHandle.close()
    }

    private companion object {
        const val PAGE_TAG = "chat-search-page"
    }
}
