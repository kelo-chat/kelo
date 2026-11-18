package chat.kelo.app.chat

import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Session
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import javax.crypto.KeyGenerator

/**
 * A search window followed down (the reader reaches its bottom): the messages after its newest one come a page at a time, appended
 * in order; a short page ends the window. While a window is open, the newest-message check does not reload the list.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatNewerTest {
    private val vms = chat.kelo.app.TestViewModels()
    private lateinit var server: MockWebServer
    private val requests = java.util.concurrent.CopyOnWriteArrayList<RecordedRequest>()
    /** what GET /api/messages/around?id=32&n=50 answers: the window's newest message and what follows it */
    @Volatile private var after = (33L..34L)

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
                requests += request
                val path = request.path.orEmpty()
                return when {
                    request.method == "GET" && path == "/api/messages/around?id=31&n=10" ->
                        MockResponse().setBody("""[{"id":30,"role":"user","content":"앞"},{"id":31,"role":"assistant","content":"찾은 답"},""" +
                            """{"id":32,"role":"user","content":"뒤"}]""")
                    request.method == "GET" && path == "/api/messages/around?id=32&n=50" ->
                        MockResponse().setBody(rows(after))
                    request.method == "GET" && path.startsWith("/api/messages/around") -> MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                    request.method == "GET" && path == "/api/messages?limit=1" -> MockResponse().setBody("""[{"id":99,"role":"user","content":"다른 폰"}]""")
                    request.method == "GET" && path.startsWith("/api/messages") -> MockResponse().setBody("""[{"id":40,"role":"user","content":"새 메시지"},{"id":41,"role":"assistant","content":"답"}]""")
                    request.method == "GET" && path == "/api/providers" -> MockResponse().setBody("[]")
                    request.method == "GET" && path == "/api/approvals/pending" -> MockResponse().setBody("""{"items":[]}""")
                    else -> MockResponse().setResponseCode(404).setBody("""{"detail":"no"}""")
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

    private fun rows(ids: LongRange): String =
        ids.joinToString(prefix = "[", postfix = "]") { """{"id":$it,"role":"user","content":"m$it"}""" }

    private fun vm(): ChatViewModel {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val store = SecureStore(MapStore(), AesGcmCipher { key }).apply { saveSession(Session(server.url("/").toString().trimEnd('/'), "tok")) }
        return vms.make { ChatViewModel(Sessions(store, requireE2e = false)) }
    }

    private fun until(what: String, cond: () -> Boolean) {
        val end = System.currentTimeMillis() + 5000
        while (!cond()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for $what")
            Thread.sleep(10)
        }
    }

    private fun ids(vm: ChatViewModel) = vm.state.value.items.mapNotNull { (it as? ChatItem.Msg)?.id }

    /** A chat whose window (31 found) is on screen: 30, 31, 32 with messages after it still to load. */
    private fun windowVm(): ChatViewModel {
        val vm = vm()
        vm.reload()
        until("newest page") { vm.state.value.loaded && vm.state.value.items.size == 2 }
        var found: Boolean? = null
        vm.openAt(31L) { found = it }
        until("window") { found == true && vm.state.value.hasNewer }
        return vm
    }

    @Test fun reachingTheBottomLoadsTheMessagesAfterTheWindowOnly() {
        val vm = windowVm()
        val from = synchronized(requests) { requests.size }
        vm.loadNewer()
        until("newer page") { !vm.state.value.loadingNewer && !vm.state.value.hasNewer }
        // the server's views (GET /api/chat/views) are asked too on a load: only the messages are in this order
        val order = synchronized(requests) { requests.drop(from).map { it.method + " " + it.path.orEmpty() }.filterNot { it.contains("/api/chat/views") } }
        assertEquals(listOf("GET /api/messages/around?id=32&n=50"), order)   // not the newest page
        assertEquals(listOf(30L, 31L, 32L, 33L, 34L), ids(vm))
        assertFalse(vm.state.value.hasNewer)   // a short page: the newest message is in
    }

    @Test fun aFullPageLeavesTheWindowOpenForTheNextOne() {
        after = 33L..82L   // 50 messages after 32: more may follow
        val vm = windowVm()
        vm.loadNewer()
        until("first page") { !vm.state.value.loadingNewer && ids(vm).size == 53 }
        assertEquals((30L..82L).toList(), ids(vm))
        assertTrue(vm.state.value.hasNewer)
    }

    @Test fun aWindowIsNotReloadedByTheNewestMessageCheck() {
        val vm = windowVm()
        val from = synchronized(requests) { requests.size }
        runBlocking { vm.checkForNews() }
        // the server's views (GET /api/chat/views) are asked too on a load: only the messages are in this order
        val order = synchronized(requests) { requests.drop(from).map { it.method + " " + it.path.orEmpty() }.filterNot { it.contains("/api/chat/views") } }
        assertTrue("no newest-message check while a window is open: $order", order.none { it.startsWith("GET /api/messages?limit=") })
        assertEquals(listOf(30L, 31L, 32L), ids(vm))
        assertTrue(vm.state.value.hasNewer)
    }
}
