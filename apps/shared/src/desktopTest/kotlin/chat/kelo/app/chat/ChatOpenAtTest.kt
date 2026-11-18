package chat.kelo.app.chat

import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Session
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
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
 * Opening the chat at a message (a search result, a notification): a message already shown needs no fetch; a message further back
 * loads its window (10 each side, oldest first) in place of the list; sending from such a window first returns to the newest page.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatOpenAtTest {
    private val vms = chat.kelo.app.TestViewModels()
    private lateinit var server: MockWebServer
    private val requests = java.util.concurrent.CopyOnWriteArrayList<RecordedRequest>()
    @Volatile private var newest = """[{"id":40,"role":"user","content":"새 메시지"},{"id":41,"role":"assistant","content":"답"}]"""

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
                    request.method == "GET" && path.startsWith("/api/messages/around") -> MockResponse().setResponseCode(404).setBody("""{"detail":"Not Found"}""")
                    request.method == "GET" && path.startsWith("/api/messages") -> MockResponse().setBody(newest)
                    request.method == "GET" && path == "/api/providers" -> MockResponse().setBody("[]")
                    request.method == "GET" && path == "/api/approvals/pending" -> MockResponse().setBody("""{"items":[]}""")
                    request.method == "POST" && path == "/api/turns" -> MockResponse().setBody("""{"id":"t1"}""")
                    request.method == "GET" && path.startsWith("/api/turns/t1") -> MockResponse().setBody("""{"events":[],"next":0,"done":true}""")
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

    private fun loadedVm(): ChatViewModel {
        val vm = vm()
        vm.reload()
        until("newest page") { vm.state.value.loaded && vm.state.value.items.size == 2 }
        return vm
    }

    @Test fun aMessageAlreadyShownNeedsNoFetch() {
        val vm = loadedVm()
        var found: Boolean? = null
        vm.openAt(40L) { found = it }
        assertEquals(true, found)
        assertTrue(requests.none { it.path.orEmpty().startsWith("/api/messages/around") })
        assertFalse(vm.state.value.hasNewer)
    }

    @Test fun aMessageFurtherBackLoadsItsWindowInPlace() {
        val vm = loadedVm()
        var found: Boolean? = null
        vm.openAt(31L) { found = it }
        until("answer") { found != null }
        assertEquals(true, found)
        assertEquals(listOf(30L, 31L, 32L), vm.state.value.items.mapNotNull { (it as? ChatItem.Msg)?.id })
        assertTrue(vm.state.value.hasNewer)   // the newest page is one tap away
        assertTrue(vm.state.value.hasOlder)
    }

    @Test fun anUnknownOrHiddenMessageLeavesTheListAsItWas() {
        val vm = loadedVm()
        var found: Boolean? = null
        vm.openAt(99L) { found = it }
        until("answer") { found != null }
        assertEquals(false, found)
        assertEquals(listOf(40L, 41L), vm.state.value.items.mapNotNull { (it as? ChatItem.Msg)?.id })
        assertFalse(vm.state.value.hasNewer)
    }

    @Test fun sendingFromAWindowReturnsToTheNewestPageFirst() {
        val vm = loadedVm()
        vm.openAt(31L) { }
        until("window") { vm.state.value.hasNewer }
        val from = synchronized(requests) { requests.size }
        vm.send("다음 질문")
        until("turn sent") { requests.any { it.method == "POST" && it.path == "/api/turns" } }
        val order = synchronized(requests) { requests.drop(from).map { it.method + " " + it.path.orEmpty() } }
        val newestAt = order.indexOfFirst { it.startsWith("GET /api/messages?limit=") }
        val turnAt = order.indexOfFirst { it == "POST /api/turns" }
        assertTrue("newest page first: $order", newestAt in 0 until turnAt)
        assertFalse(vm.state.value.hasNewer)
    }
}
