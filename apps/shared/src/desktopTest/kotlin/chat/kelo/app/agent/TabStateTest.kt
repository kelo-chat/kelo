package chat.kelo.app.agent

import org.junit.Assert.assertEquals
import org.junit.Test

/** A browser tab's status word (owner report 2026-11-19: a job waiting for the owner's answer showed its tab as resting). */
class TabStateTest {
    @Test fun `a job waiting for the owner shows waiting, not idle`() {
        assertEquals(TabState.WAITING, tabState("waiting"))
    }

    @Test fun `the other states, and a value a newer server adds, as before`() {
        assertEquals(TabState.WORKING, tabState("working"))
        assertEquals(TabState.FAILED, tabState("failed"))
        assertEquals(TabState.IDLE, tabState("idle"))
        assertEquals(TabState.IDLE, tabState("something_newer"))
        assertEquals(TabState.IDLE, tabState(null))
        assertEquals(TabState.WORKING, tabState("waiting", busy = true))   // the live view: a step running now wins
    }
}
