package chat.kelo.app.my

import chat.kelo.app.agent.LiveKinds
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** 마이 → 휴대폰 기기: the row shows only for a configured device, the status mapping, the check button, and the live view key. */
@OptIn(ExperimentalCoroutinesApi::class)
class AndroidDeviceViewModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null

    @Before fun setUp() { Dispatchers.setMain(UnconfinedTestDispatcher()) }
    @After fun tearDown() { vms.clear(); server?.shutdown(); Dispatchers.resetMain() }

    private fun body(configured: Boolean = true, connected: Boolean = true, model: String = "Pixel 7", version: String = "15",
                     screen: String = """{"width":720,"height":1280}""", play: Boolean = true, korean: Boolean = false) =
        """{"configured":$configured,"connected":$connected,"model":"$model","android_version":"$version","screen":$screen,
            "play_store":$play,"korean_input":$korean,"checked_at":1794744000.0}"""

    @Test fun theRowShowsOnlyWhenTheServerSaysConfigured() {
        val s = MyFakeServer { m, p, _ -> if (m == "GET" && p == "/api/android") ok(body(configured = false, connected = false, model = "", version = "", screen = "null", play = false)) else null }
            .also { server = it }
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        assertFalse(AndroidDeviceRules.shown(vm.state.value.info))   // nothing yet: no row
        vm.load()
        until("read") { vm.state.value.info != null }
        assertFalse(AndroidDeviceRules.shown(vm.state.value.info))
        assertEquals(1, s.requests.count { it == "GET /api/android" })
    }

    @Test fun aConfiguredDeviceShowsTheRowAndMapsItsFields() {
        val s = MyFakeServer { m, p, _ -> if (m == "GET" && p == "/api/android") ok(body()) else null }.also { server = it }
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        vm.load()
        until("read") { vm.state.value.info != null }
        val info = vm.state.value.info!!
        assertTrue(AndroidDeviceRules.shown(info))
        assertTrue(info.connected)
        assertEquals("Pixel 7", info.model)
        assertEquals("15", info.androidVersion)
        assertEquals("720 × 1280", AndroidDeviceRules.screenSize(info))
        assertTrue(info.playStore)
        assertFalse(info.koreanInput)
        assertFalse(vm.state.value.checking)
    }

    @Test fun aDeviceThatIsNotConnectedKeepsTheRowWithoutFacts() {
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p == "/api/android") ok(body(connected = false, model = "", version = "", screen = "null", play = false)) else null
        }.also { server = it }
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        vm.load()
        until("read") { vm.state.value.info != null }
        val info = vm.state.value.info!!
        assertTrue(AndroidDeviceRules.shown(info))
        assertFalse(info.connected)
        assertNull(AndroidDeviceRules.screenSize(info))
    }

    @Test fun aFailedReadHidesTheRow() {
        val s = MyFakeServer { m, p, _ ->
            if (m == "GET" && p == "/api/android") (if (requestsSeen++ == 0) ok(body()) else fail(500, "boom")) else null
        }.also { server = it }
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        vm.load()
        until("first read") { AndroidDeviceRules.shown(vm.state.value.info) }
        vm.load()                                                      // the second read fails: the row goes
        until("row hidden") { !AndroidDeviceRules.shown(vm.state.value.info) }
        assertNull(vm.state.value.info)
    }

    @Test fun anOlderServerWithoutTheRouteShowsNoRow() {
        val s = MyFakeServer { _, _, _ -> null }.also { server = it }   // 404 for everything
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        vm.load()
        until("asked") { s.saw("GET /api/android") }
        Thread.sleep(100)
        assertFalse(AndroidDeviceRules.shown(vm.state.value.info))
    }

    private var requestsSeen = 0

    @Test fun theCheckButtonIsBusyThenTheNewReadingShows() {
        var release = false
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p == "/api/android" -> ok(body(connected = false, model = "", version = "", screen = "null", play = false))
                m == "POST" && p == "/api/android/check" -> ok(body(korean = true)).setBodyDelay(400, java.util.concurrent.TimeUnit.MILLISECONDS).also { release = true }
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        vm.load()
        until("read") { vm.state.value.info != null }
        assertFalse(vm.state.value.info!!.connected)

        vm.check()
        assertTrue(vm.state.value.checking)                            // busy right away
        vm.check()                                                     // a second tap while busy does nothing
        until("answered") { !vm.state.value.checking }
        assertTrue(release)
        assertEquals(1, s.requests.count { it == "POST /api/android/check" })
        val info = vm.state.value.info!!
        assertTrue(info.connected)
        assertTrue(info.koreanInput)                                   // the new reading replaced the old one
        assertNotNull(info.screen)
    }

    @Test fun aFailedCheckEndsBusyAndSaysNotConnected() {
        val s = MyFakeServer { m, p, _ ->
            when {
                m == "GET" && p == "/api/android" -> ok(body())
                m == "POST" && p == "/api/android/check" -> fail(500, "boom")
                else -> null
            }
        }.also { server = it }
        val vm = vms.make { AndroidDeviceViewModel(s.sessions()) }
        vm.load()
        until("read") { vm.state.value.info?.connected == true }
        vm.check()
        until("check ended") { !vm.state.value.checking }
        assertTrue(AndroidDeviceRules.shown(vm.state.value.info))      // still a configured device
        assertFalse(vm.state.value.info!!.connected)
    }

    @Test fun theScreenOpensTheLiveViewWithTheKeyAndroid() {
        assertEquals("android", AndroidDeviceRules.LIVE_KEY)
        assertTrue(LiveKinds.isDevice(AndroidDeviceRules.LIVE_KEY))   // the same live view the phone card opens
        assertEquals(chat.kelo.app.notify.OpenTarget.Tab("android"), chat.kelo.app.notify.OpenTarget.Tab(AndroidDeviceRules.LIVE_KEY))
        assertEquals("https://github.com/kelo-chat/kelo#android", AndroidDeviceRules.README_URL)
    }
}
