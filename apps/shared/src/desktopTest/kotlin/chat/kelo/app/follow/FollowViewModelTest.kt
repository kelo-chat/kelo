package chat.kelo.app.follow

import chat.kelo.app.agent.AgentTask
import chat.kelo.app.agent.AgentViewModel
import chat.kelo.app.agent.BrowserTabs
import chat.kelo.app.agent.Loadable
import chat.kelo.app.agent.Permissions
import chat.kelo.app.agent.ScheduleList
import chat.kelo.app.agent.TaskList
import chat.kelo.app.agent.WatcherList
import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Session
import chat.kelo.app.core.Sessions
import chat.kelo.app.my.MyFakeServer
import chat.kelo.app.work.PastFilter
import chat.kelo.app.work.SECTION_FOLLOW
import chat.kelo.app.work.SECTION_NOW
import chat.kelo.app.work.WorkRowsInput
import chat.kelo.app.work.buildWorkRows
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.LocalDate
import java.util.Collections
import javax.crypto.KeyGenerator

@OptIn(ExperimentalCoroutinesApi::class)
class FollowViewModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private lateinit var server: MockWebServer
    private val requests = Collections.synchronizedList(mutableListOf<String>())

    private val sampleJson = """
    {
      "items": [
        {
          "key": "cal:123",
          "kind": "flight",
          "title": "Flight KE123",
          "starts_at": 1763500000.0,
          "checks": [
            {
              "kind": "flight_status",
              "fields": ["gate", "status"],
              "from": "-3h",
              "until": "+30m",
              "state": "active",
              "last": {"gate": "A11"},
              "next_at": 1763489200.0
            }
          ],
          "prep": [
            {
              "what": "online check-in",
              "when": "-24h",
              "kind": "check_in",
              "state": "later"
            }
          ],
          "plan": {
            "checks": [],
            "prep": [{"what": "online check-in", "when": "-24h", "kind": "check_in", "state": "later"}],
            "tell": {
              "lead": ["-1d", "-3h"],
              "on_change": true
            }
          },
          "state": "watching"
        }
      ]
    }
    """.trimIndent()

    private class MapStore : KeyValueStore {
        val map = HashMap<String, String>()
        override fun getString(key: String) = map[key]
        override fun putString(key: String, value: String) { map[key] = value }
        override fun remove(key: String) { map.remove(key) }
    }

    @Before fun start() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                val body = request.body.readUtf8()
                requests += "${request.method} $path $body"
                return when {
                    request.method == "GET" && path == "/api/follow" -> MockResponse().setBody(sampleJson)
                    request.method == "POST" && path.startsWith("/api/follow/") && path.endsWith("/stop") ->
                        MockResponse().setBody("""{"ok":true,"key":"cal:123","state":"stopped"}""")
                    request.method == "POST" && path.startsWith("/api/follow/") && path.endsWith("/replan") ->
                        MockResponse().setBody("""{"ok":true,"key":"cal:123","state":"watching"}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"not found"}""")
                }
            }
        }
        server.start()
    }

    @After fun stop() {
        server.shutdown()
        vms.clear()
        Dispatchers.resetMain()
    }

    private fun sessions(): Sessions {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val store = SecureStore(MapStore(), AesGcmCipher { key }).apply {
            saveSession(Session(server.url("/").toString().trimEnd('/'), "tok"))
        }
        return Sessions(store, requireE2e = false)
    }

    private fun until(ok: () -> Boolean) {
        val end = System.currentTimeMillis() + 5_000
        while (!ok() && System.currentTimeMillis() < end) Thread.sleep(10)
    }

    @Test
    fun parsing() {
        val format = Json { ignoreUnknownKeys = true }
        val parsed = format.decodeFromString<FollowItemList>(sampleJson)
        assertEquals(1, parsed.items.size)

        val item = parsed.items.first()
        assertEquals("cal:123", item.key)
        assertEquals("flight", item.kind)
        assertEquals("Flight KE123", item.title)
        assertEquals(1763500000.0, item.startsAt, 0.001)
        assertEquals("watching", item.state)

        // Checks
        assertEquals(1, item.checks.size)
        val check = item.checks.first()
        assertEquals("flight_status", check.kind)
        assertEquals(listOf("gate", "status"), check.fields)
        assertEquals("-3h", check.from)
        assertEquals("+30m", check.until)
        assertEquals("active", check.state)
        assertEquals(1763489200.0, check.nextAt!!, 0.001)
        assertEquals(1763489200.0, item.nextCheck!!, 0.001)

        // Prep
        assertEquals(1, item.prep.size)
        val prep = item.prep.first()
        assertEquals("online check-in", prep.what)
        assertEquals("-24h", prep.`when`)
        assertEquals("later", prep.state)

        // Plan
        assertNotNull(item.plan)
        assertNotNull(item.plan?.tell)
        assertEquals(listOf("-1d", "-3h"), item.plan?.tell?.lead)
        assertTrue(item.plan?.tell?.onChange == true)
    }

    @Test
    fun emptyIsHidden() {
        var fakeServer: MyFakeServer? = null
        try {
            val s = MyFakeServer { _, _, _ -> null }.also { fakeServer = it }
            val sessions = s.sessions()
            val vm = vms.make { AgentViewModel(sessions) }

            fun makeInput(followItems: List<FollowItem>): WorkRowsInput {
                return WorkRowsInput(
                    vm = vm,
                    tasks = Loadable(TaskList(listOf(AgentTask(1, title = "Task 1", status = AgentTask.STATUS_RUNNING, startedAt = 1000.0)))),
                    workers = Loadable(chat.kelo.app.agent.WorkerList()),
                    perms = Loadable(Permissions()),
                    schedules = Loadable(ScheduleList()),
                    browser = Loadable(BrowserTabs()),
                    watchers = Loadable(WatcherList()),
                    followups = Loadable(chat.kelo.app.followup.FollowupList()),
                    followupBusy = emptySet(),
                    entries = Loadable(emptyList()),
                    filter = PastFilter.ALL,
                    showPaused = false,
                    onTogglePaused = {},
                    onFilter = {},
                    today = LocalDate.of(2026, 11, 16),
                    push = {},
                    onOpenLive = {},
                    container = { error("not used") },
                    followItems = followItems,
                )
            }

            // Empty items -> SECTION_FOLLOW is hidden (no row emitted)
            val emptyRows = buildWorkRows(makeInput(emptyList()))
            assertFalse("SECTION_FOLLOW must not be present when empty", emptyRows.any { it.key == SECTION_FOLLOW })
            assertFalse("No follow rows when empty", emptyRows.any { it.key.startsWith("follow:") })

            // With items -> SECTION_FOLLOW is present at the top (right after title)
            val flightItem = Json { ignoreUnknownKeys = true }.decodeFromString<FollowItemList>(sampleJson).items.first()
            val withRows = buildWorkRows(makeInput(listOf(flightItem)))
            val keys = withRows.map { it.key }

            val followIndex = keys.indexOf(SECTION_FOLLOW)
            assertTrue("SECTION_FOLLOW must be present", followIndex >= 0)
            assertEquals("title", keys[0])
            assertEquals(SECTION_FOLLOW, keys[1])
            assertEquals("follow:cal:123", keys[2])
            assertTrue("SECTION_FOLLOW must appear before SECTION_NOW", followIndex < keys.indexOf(SECTION_NOW))
        } finally {
            fakeServer?.shutdown()
        }
    }

    @Test
    fun stopCallsTheApi() {
        val vm = FollowViewModel(sessions())
        until { vm.state.value.items.isNotEmpty() }

        assertEquals(1, vm.state.value.items.size)
        assertEquals("watching", vm.state.value.items.first().state)

        var stopped = false
        vm.stop("cal:123") { stopped = true }
        until { stopped }

        assertTrue("stop callback must be invoked", stopped)
        assertEquals("stopped", vm.state.value.items.first().state)

        // Verify that POST /api/follow/.../stop was called
        val stopCall = requests.firstOrNull { it.contains("POST /api/follow/") && it.contains("/stop") }
        assertNotNull("POST /api/follow/{key}/stop must be called", stopCall)
    }

    @Test
    fun replanCallsTheApi() {
        val vm = FollowViewModel(sessions())
        until { vm.state.value.items.isNotEmpty() }

        var replanned = false
        vm.replan("cal:123") { replanned = true }
        until { replanned }

        assertTrue("replan callback must be invoked", replanned)
        val replanCall = requests.firstOrNull { it.contains("POST /api/follow/") && it.contains("/replan") }
        assertNotNull("POST /api/follow/{key}/replan must be called", replanCall)
    }

    @Test
    fun stateTexts() {
        // Prep state texts (예정 / 하는 중 / 해 뒀어요 / 실패)
        assertEquals("예정", FollowRules.prepStateWord("later", isKo = true))
        assertEquals("하는 중", FollowRules.prepStateWord("running", isKo = true))
        assertEquals("해 뒀어요", FollowRules.prepStateWord("done", isKo = true))
        assertEquals("실패", FollowRules.prepStateWord("failed", isKo = true))

        assertEquals("Scheduled", FollowRules.prepStateWord("later", isKo = false))
        assertEquals("In progress", FollowRules.prepStateWord("running", isKo = false))
        assertEquals("Done ahead", FollowRules.prepStateWord("done", isKo = false))
        assertEquals("Failed", FollowRules.prepStateWord("failed", isKo = false))

        // Plan summary text from brief:
        // "출발 하루 전 알림 · 체크인 열리면 준비" from the plan
        val item = Json { ignoreUnknownKeys = true }.decodeFromString<FollowItemList>(sampleJson).items.first()
        val summaryKo = FollowRules.planSummary(item, isKo = true)
        assertEquals("출발 하루 전 알림 · 체크인 열리면 준비", summaryKo)

        val summaryEn = FollowRules.planSummary(item, isKo = false)
        assertEquals("Notification 1 day before · Prepare check-in when open", summaryEn)

        // the plan's own kind decides, never the words of "what": the same words without the kind read as a plain preparation
        val plain = item.copy(prep = item.prep.map { it.copy(kind = "") }, plan = item.plan?.copy(prep = item.plan.prep.map { it.copy(kind = "") }))
        assertEquals("Notification 1 day before · Prepare online check-in", FollowRules.planSummary(plain, isKo = false))
        val other = item.copy(prep = listOf(FollowPrep(what = "체크인", kind = "other")), plan = null)
        assertEquals("Prepare 체크인", FollowRules.planSummary(other, isKo = false).substringAfterLast(" · "))

        // Other item variations
        val parcelItem = FollowItem(
            key = "order:456",
            kind = "parcel",
            title = "Book delivery",
            checks = listOf(FollowCheck(kind = "parcel", fields = listOf("status"))),
        )
        assertEquals("변경 사항 확인", FollowRules.planSummary(parcelItem, isKo = true))
        assertEquals("Watch for changes", FollowRules.planSummary(parcelItem, isKo = false))
    }
}
