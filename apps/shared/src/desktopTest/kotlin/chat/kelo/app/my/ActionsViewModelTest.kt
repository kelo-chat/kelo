package chat.kelo.app.my

import chat.kelo.app.agent.DayGroups
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZoneOffset
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ActionsViewModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null
    private val fixedToday = LocalDate.of(2026, 11, 15)
    private val utcZone = ZoneOffset.UTC

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun tearDown() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private fun sampleDidJson(): String {
        return """
        {
          "items": [
            {
              "at": 1794744000.0,
              "kind": "action",
              "title": "Sent a mail to bob@example.com",
              "detail": "",
              "source": "chat",
              "site": null,
              "connector": "gmail",
              "task_id": 101,
              "outcome": "done"
            },
            {
              "at": 1794650400.0,
              "kind": "browser",
              "title": "Search flights",
              "detail": "Finished search",
              "source": "job",
              "site": "airline.com",
              "connector": null,
              "task_id": 102,
              "outcome": "done"
            },
            {
              "at": 1794307200.0,
              "kind": "task",
              "title": "Morning Briefing",
              "detail": "Weather & calendar",
              "source": "schedule",
              "site": null,
              "connector": null,
              "task_id": 103,
              "outcome": "done"
            },
            {
              "at": 1794307100.0,
              "kind": "reply",
              "title": "How is the weather?",
              "detail": "Sunny and warm",
              "source": "chat",
              "site": null,
              "connector": null,
              "task_id": 104,
              "outcome": "done"
            }
          ]
        }
        """.trimIndent()
    }

    private fun sampleJson(): String {
        // 2026-11-15T12:00:00Z -> ts = 1794744000 (Today)
        // 2026-11-14T10:00:00Z -> ts = 1794650400 (Yesterday)
        // 2026-11-10T08:00:00Z -> ts = 1794307200 (Past date)
        return """
        {
          "items": [
            {
              "id": 1,
              "ts": 1794744000.0,
              "run_id": "r1",
              "task_id": 101,
              "turn_id": "t1",
              "source": "chat",
              "source_name": "Chat",
              "line": "Sent a mail to bob@example.com",
              "tool": "email_send",
              "kind": "send",
              "connector": "gmail",
              "targets": ["bob@example.com"],
              "state": "done",
              "outside_id": "m1",
              "detail": ""
            },
            {
              "id": 2,
              "ts": 1794650400.0,
              "run_id": "r2",
              "task_id": null,
              "turn_id": "t2",
              "source": "schedule:5",
              "source_name": "Daily Cleanup",
              "line": "Deleted in Slack",
              "tool": "connector_send",
              "kind": "delete",
              "connector": "slack",
              "targets": ["row1"],
              "state": "unknown",
              "outside_id": "",
              "detail": "Timeout"
            },
            {
              "id": 3,
              "ts": 1794307200.0,
              "run_id": "r3",
              "task_id": 103,
              "turn_id": "t3",
              "source": "job",
              "source_name": "Calendar Sync",
              "line": "Changed the calendar",
              "tool": "gcal_update_event",
              "kind": "change",
              "connector": "",
              "targets": ["calendar"],
              "state": "failed",
              "outside_id": "",
              "detail": "NetworkError"
            }
          ]
        }
        """.trimIndent()
    }

    @Test fun loadsItemsFromDidApi() {
        var requestedPath = ""
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/did?days=")) {
                requestedPath = p
                ok(sampleDidJson())
            } else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("did items loaded") { vm.state.value.items.isNotEmpty() }

        assertEquals("/api/did?days=7", requestedPath)
        assertEquals(4, vm.state.value.items.size)
        assertEquals("action", vm.state.value.items[0].kind)
        assertEquals("browser", vm.state.value.items[1].kind)
        assertEquals("task", vm.state.value.items[2].kind)
        assertEquals("reply", vm.state.value.items[3].kind)
        assertEquals("airline.com", vm.state.value.items[1].site)
    }

    @Test fun filterChipsFilterItemsByKind() {
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/did")) ok(sampleDidJson()) else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("did items loaded") { vm.state.value.items.isNotEmpty() }

        // Default ALL: 4 items
        assertEquals(DidFilter.ALL, vm.state.value.filter)
        val allCount = vm.state.value.groups.sumOf { it.items.size }
        assertEquals(4, allCount)

        // Filter ACTIONS: 1 item
        vm.setFilter(DidFilter.ACTIONS)
        assertEquals(1, vm.state.value.groups.sumOf { it.items.size })
        assertEquals("action", vm.state.value.groups[0].items[0].normalizedKind)

        // Filter BROWSER: 1 item
        vm.setFilter(DidFilter.BROWSER)
        assertEquals(1, vm.state.value.groups.sumOf { it.items.size })
        assertEquals("browser", vm.state.value.groups[0].items[0].normalizedKind)

        // Filter TASKS: 1 item
        vm.setFilter(DidFilter.TASKS)
        assertEquals(1, vm.state.value.groups.sumOf { it.items.size })
        assertEquals("task", vm.state.value.groups[0].items[0].normalizedKind)

        // Filter back to ALL: 4 items
        vm.setFilter(DidFilter.ALL)
        assertEquals(4, vm.state.value.groups.sumOf { it.items.size })
    }

    @Test fun fallsBackToActionsApiWhenDidReturns404() {
        val requestedPaths = mutableListOf<String>()
        val s = MyFakeServer { m, p, _ ->
            requestedPaths += p
            if (m == "GET" && p.startsWith("/api/actions")) {
                ok(sampleJson())
            } else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("actions fallback loaded") { vm.state.value.items.isNotEmpty() }

        // Both /api/did and /api/actions were requested
        assertTrue(requestedPaths.any { it.startsWith("/api/did") })
        assertTrue(requestedPaths.any { it.startsWith("/api/actions") })

        assertEquals(3, vm.state.value.items.size)
        // All items from legacy /api/actions are actions (send/change/delete)
        vm.setFilter(DidFilter.ACTIONS)
        assertEquals(3, vm.state.value.groups.sumOf { it.items.size })

        vm.setFilter(DidFilter.BROWSER)
        assertEquals(0, vm.state.value.groups.sumOf { it.items.size })
    }

    @Test fun loadsItemsForDefaultPeriodOf7Days() {
        var requestedDays = 0
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/actions?days=")) {
                requestedDays = p.substringAfter("days=").toInt()
                ok(sampleJson())
            } else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("items loaded") { vm.state.value.items.isNotEmpty() }

        assertEquals(7, requestedDays)
        assertEquals(ActionPeriod.DAYS_7, vm.state.value.period)
        assertEquals(3, vm.state.value.items.size)
        assertFalse(vm.state.value.loading)
        assertFalse(vm.state.value.failed)
    }

    @Test fun periodChangeLoadsCorrespondingDays() {
        var lastDays = 0
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/actions?days=")) {
                lastDays = p.substringAfter("days=").toInt()
                ok(sampleJson())
            } else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.setPeriod(ActionPeriod.TODAY)
        until("loaded today") { lastDays == 1 }
        assertEquals(ActionPeriod.TODAY, vm.state.value.period)

        vm.setPeriod(ActionPeriod.DAYS_30)
        until("loaded 30 days") { lastDays == 30 }
        assertEquals(ActionPeriod.DAYS_30, vm.state.value.period)
    }

    @Test fun groupingGroupsItemsIntoTodayYesterdayAndPastDates() {
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/actions")) ok(sampleJson()) else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("grouped") { vm.state.value.groups.isNotEmpty() }

        val groups = vm.state.value.groups
        assertEquals(3, groups.size)

        // First group is Today (item id 1)
        assertEquals(DayGroups.Label.Today, groups[0].label)
        assertEquals(listOf(1L), groups[0].items.map { it.id })

        // Second group is Yesterday (item id 2)
        assertEquals(DayGroups.Label.Yesterday, groups[1].label)
        assertEquals(listOf(2L), groups[1].items.map { it.id })

        // Third group is specific Date (item id 3 on 2026-11-10)
        assertEquals(DayGroups.Label.Date(LocalDate.of(2026, 11, 10)), groups[2].label)
        assertEquals(listOf(3L), groups[2].items.map { it.id })
    }

    @Test fun handlesUnknownAndFailedTags() {
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/actions")) ok(sampleJson()) else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("loaded") { vm.state.value.items.isNotEmpty() }

        val items = vm.state.value.items
        assertEquals("done", items[0].state)
        assertEquals("unknown", items[1].state)
        assertEquals("failed", items[2].state)
    }

    @Test fun handlesEmptyState() {
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p.startsWith("/api/actions")) ok("""{"items":[]}""") else null
        }.also { server = it }

        val vm = vms.make { ActionsViewModel(s.sessions(), { fixedToday }, { utcZone }) }
        vm.load()
        until("not loading") { !vm.state.value.loading }

        assertTrue(vm.state.value.items.isEmpty())
        assertTrue(vm.state.value.groups.isEmpty())
        assertFalse(vm.state.value.failed)
    }
}
