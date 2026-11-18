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
 * The search screen's state against a local fake server: an empty query asks nothing, the trimmed text is sent, a full page
 * says more may follow and the next page asks with before = the smallest id shown. Nothing here reaches a real server.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatSearchViewModelTest {
    private val vms = chat.kelo.app.TestViewModels()
    private lateinit var server: MockWebServer
    private val paths = java.util.concurrent.CopyOnWriteArrayList<String>()
    @Volatile private var failing = false

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
                paths += path
                return when {
                    failing -> MockResponse().setResponseCode(500).setBody("""{"detail":"boom"}""")
                    request.method == "GET" && path.contains("/api/messages/search") && path.contains("before=21") ->
                        MockResponse().setBody("""[{"id":5,"role":"user","snippet":"older « 날씨 »","created_at":1700000000}]""")
                    request.method == "GET" && path.contains("/api/messages/search") -> {
                        // a full page (the newest first, ids 40 down to 21): more may follow
                        val rows = (40 downTo 21).joinToString(",") { id ->
                            """{"id":$id,"role":"user","snippet":"날씨 « $id »","created_at":${1700000000 + id}}"""
                        }
                        MockResponse().setBody("[$rows]")
                    }
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

    private fun vm(): ChatSearchViewModel {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val store = SecureStore(MapStore(), AesGcmCipher { key }).apply { saveSession(Session(server.url("/").toString().trimEnd('/'), "tok")) }
        return vms.make { ChatSearchViewModel(Sessions(store, requireE2e = false)) }
    }

    private fun until(what: String, cond: () -> Boolean) {
        val end = System.currentTimeMillis() + 5000
        while (!cond()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for $what")
            Thread.sleep(10)
        }
    }

    @Test fun anEmptyQueryAsksNothing() {
        val vm = vm()
        vm.setQuery("   ")
        Thread.sleep(100)
        assertTrue(paths.isEmpty())
        assertTrue(vm.state.value.hits.isEmpty())
        assertFalse(vm.state.value.searched)
    }

    @Test fun theTrimmedQueryIsSentAndTheFullFirstPageSaysMoreMayFollow() {
        val vm = vm()
        vm.setQuery("  날씨  ")
        until("first page") { vm.state.value.hits.size == ChatSearchRules.PAGE }
        assertEquals("/api/messages/search?q=%EB%82%A0%EC%94%A8&limit=20", paths.single())
        assertEquals(40L, vm.state.value.hits.first().id)   // newest first, as the server sends
        assertTrue(vm.state.value.hasMore)
        assertTrue(vm.state.value.searched)
        assertFalse(vm.state.value.failed)
    }

    @Test fun loadingMoreAsksWithBeforeTheSmallestIdShownAndAppends() {
        val vm = vm()
        vm.setQuery("날씨")
        until("first page") { vm.state.value.hits.size == ChatSearchRules.PAGE }
        vm.loadMore()
        until("older page") { vm.state.value.hits.size == ChatSearchRules.PAGE + 1 }
        assertEquals("/api/messages/search?q=%EB%82%A0%EC%94%A8&limit=20&before=21", paths.last())
        assertEquals(5L, vm.state.value.hits.last().id)
        assertFalse(vm.state.value.hasMore)   // a short page: the end
    }

    @Test fun aFailedSearchSaysSoAndShowsNoHits() {
        failing = true
        val vm = vm()
        vm.setQuery("날씨")
        until("failure") { vm.state.value.failed }
        assertTrue(vm.state.value.hits.isEmpty())
        assertTrue(vm.state.value.searched)
    }

    @Test fun clearingTheFieldForgetsTheHits() {
        val vm = vm()
        vm.setQuery("날씨")
        until("hits") { vm.state.value.hits.isNotEmpty() }
        vm.setQuery("")
        assertTrue(vm.state.value.hits.isEmpty())
        assertFalse(vm.state.value.searched)
        assertTrue(vm.state.value.query.isEmpty())
    }
}
