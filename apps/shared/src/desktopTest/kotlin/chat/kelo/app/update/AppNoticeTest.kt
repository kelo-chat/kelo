package chat.kelo.app.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** docs/version-notices.md part 3: the banner for a newer app that works with the server shows once per version, on this device. */
class AppNoticeTest {
    private fun notice(start: String? = null, saved: MutableList<String> = mutableListOf()) = AppNotice({ start }, { saved += it }) to saved

    @Test fun showsOnlyAnOfferedVersionThatWasNotDismissed() {
        assertFalse(AppNoticeRules.show(null, emptySet()))
        assertFalse(AppNoticeRules.show("", emptySet()))
        assertTrue(AppNoticeRules.show("0.10.3", emptySet()))
        assertFalse(AppNoticeRules.show("0.10.3", setOf("0.10.3")))
        assertTrue(AppNoticeRules.show("0.10.4", setOf("0.10.3")))   // a newer one comes again
    }

    @Test fun dismissingIsRememberedPerVersionAndSaved() {
        val (n, saved) = notice()
        assertEquals("0.10.3", n.visible("0.10.3"))
        n.dismiss("0.10.3")
        assertNull(n.visible("0.10.3"))
        assertEquals("0.10.4", n.visible("0.10.4"))
        n.dismiss("0.10.4")
        assertEquals("0.10.3,0.10.4", saved.last())
        // a new start reads what was saved
        val (again, _) = notice(saved.last())
        assertNull(again.visible("0.10.4")); assertNull(again.visible("0.10.3"))
        assertEquals("0.11.0", again.visible("0.11.0"))
    }

    @Test fun aBrokenStoreNeverBreaksTheApp() {
        val n = AppNotice({ error("no storage") }, { error("no storage") })
        assertEquals("0.10.3", n.visible("0.10.3"))
        n.dismiss("0.10.3")
        assertNull(n.visible("0.10.3"))   // held in memory for this run
    }

    @Test fun storeBuildsOpenTheStoreOthersUseTheirOwnUpdater() {
        for (c in listOf("play", "appstore", "msstore")) assertEquals(AppNoticeRules.Action.STORE, AppNoticeRules.action(c))
        for (c in listOf("download", "")) assertEquals(AppNoticeRules.Action.UPDATER, AppNoticeRules.action(c))
        assertEquals(setOf("a", "b"), AppNoticeRules.decode(" a, b,,"))
    }
}
