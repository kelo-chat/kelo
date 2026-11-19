package chat.kelo.app.agent

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveTabsTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun `a session item without tabs parses to no tabs`() {
        val l = json.decodeFromString<BrowserTabsLite>("""{"items":[{"id":"owner","status":"idle"}]}""")
        assertTrue(l.items.single().tabs.isEmpty())
        val t = json.decodeFromString<BrowserTab>("""{"id":"owner"}""")
        assertTrue(t.tabs.isEmpty())
    }

    @Test fun `a session item with tabs parses them`() {
        val l = json.decodeFromString<BrowserTabsLite>(
            """{"items":[{"id":"owner","tabs":[{"id":"t1","title":"A","url":"https://a.example/","active":true},{"id":"t2","title":"","url":"u"}]}]}""",
        )
        val tabs = l.items.single().tabs
        assertEquals(listOf("t1", "t2"), tabs.map { it.id })
        assertTrue(tabs[0].active)
        assertFalse(tabs[1].active)
    }

    @Test fun `chips show only with more than one tab`() {
        assertFalse(LiveRules.showTabs(emptyList()))
        assertFalse(LiveRules.showTabs(listOf(LiveTab("t1"))))
        assertTrue(LiveRules.showTabs(listOf(LiveTab("t1"), LiveTab("t2"))))
    }

    @Test fun `chips are tappable only while this device holds the tab`() {
        assertTrue(LiveRules.tabsTappable(LiveStatus(youControl = true)))
        assertFalse(LiveRules.tabsTappable(LiveStatus(youControl = false)))
        assertFalse(LiveRules.tabsTappable(LiveStatus(controller = "agent")))
    }

    @Test fun `only a tab that is not active has a close mark`() {
        assertTrue(LiveRules.tabClosable(LiveTab("t2", active = false), tappable = true))
        assertFalse(LiveRules.tabClosable(LiveTab("t1", active = true), tappable = true))
        assertFalse(LiveRules.tabClosable(LiveTab("t2", active = false), tappable = false))
    }

    @Test fun `the action body names the action`() {
        assertEquals("""{"action":"activate"}""", LiveRules.tabAction(false).toString())
        assertEquals("""{"action":"close"}""", LiveRules.tabAction(true).toString())
    }
}
