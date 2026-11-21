package chat.kelo.app.goals

import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Goal
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GoalLogPagingTest {
    private fun entry(id: Long) = GoalLogEntry(id = id, text = "entry $id")

    private fun workspace(log: List<GoalLogEntry>, next: Int?) =
        GoalWorkspace(goal = Goal(id = 3, title = "G"), log = log, logNext = next)

    @Test fun readsTheLogPageMarkerAndAnOlderWorkspaceWithoutIt() {
        val w = KeloApi.json.decodeFromString<GoalWorkspace>(
            """{"goal":{"id":3,"title":"G"},"log":[{"id":9,"text":"x"}],"log_next":20}""")
        assertEquals(20, w.logNext)
        val old = KeloApi.json.decodeFromString<GoalWorkspace>("""{"goal":{"id":3,"title":"G"},"log":[]}""")
        assertNull(old.logNext)
        val page = KeloApi.json.decodeFromString<GoalLogPage>("""{"items":[{"id":1,"text":"a"},{"id":2,"text":"b"}],"next":null}""")
        assertEquals(listOf(1L, 2L), page.items.map { it.id })
        assertNull(page.next)
    }

    @Test fun everyEntryIsKeptAndAPageIsAddedOnce() {
        val first = workspace((1L..20L).map(::entry), next = 20)
        val page = GoalLogPage(items = (21L..30L).map(::entry), next = null)
        val more = GoalDetailRules.withMoreLog(first, page)
        assertEquals((1L..30L).toList(), more.log.map { it.id })
        assertNull(more.logNext)
        // the same page again adds nothing
        val again = GoalDetailRules.withMoreLog(more, page)
        assertEquals(30, again.log.size)
    }

    @Test fun aRefreshKeepsTheOlderEntriesAlreadyLoaded() {
        val loaded = workspace((1L..30L).map(::entry), next = null)
        val fresh = workspace((2L..21L).map(::entry), next = 20)   // one new entry at the top moved the first page along
        val merged = GoalDetailRules.refreshed(loaded, fresh)
        assertEquals(30, merged.log.size)
        assertEquals(null, merged.logNext)
        // with no older entries loaded, the fresh page is simply the page
        assertEquals(fresh, GoalDetailRules.refreshed(null, fresh))
    }

    @Test fun noCallWhileOneRunsOrAfterTheLastPage() {
        val more = GoalDetailState(workspace(listOf(entry(1)), next = 20))
        assertTrue(GoalDetailRules.canLoadMore(more))
        assertFalse(GoalDetailRules.canLoadMore(more.copy(loadingLog = true)))
        assertFalse(GoalDetailRules.canLoadMore(GoalDetailState(workspace(listOf(entry(1)), next = null))))
        assertFalse(GoalDetailRules.canLoadMore(GoalDetailState()))
    }

    @Test fun aTagKeepsItsLengthAsTyped() {
        val sixty = "a".repeat(60)
        assertEquals(sixty, GoalsRules.tag("  $sixty  "))
        assertEquals("b".repeat(100), GoalsRules.tag("b".repeat(100)))
    }
}
