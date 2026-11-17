package chat.kelo.app.core

import chat.kelo.app.core.e2e.KeyPin
import chat.kelo.app.core.e2e.ServerKey
import chat.kelo.app.my.ServerAddressRules
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.KeyGenerator

/** "Change address" in My: the same server (the same pinned KE2E key) moves; another server, an unreachable one and bad text do not. */
class MoveServerTest {
    private class MapStore : KeyValueStore {
        val map = HashMap<String, String>()
        override fun getString(key: String) = map[key]
        override fun putString(key: String, value: String) { map[key] = value }
        override fun remove(key: String) { map.remove(key) }
    }

    private val servers = mutableListOf<MockWebServer>()
    private val keyA = ServerKey(ByteArray(32) { 1 })
    private val keyB = ServerKey(ByteArray(32) { 2 })

    @After fun stop() { servers.forEach { runCatching { it.shutdown() } } }

    private fun server(key: ServerKey?): MockWebServer = MockWebServer().apply {
        if (key == null) enqueue(MockResponse().setResponseCode(404))
        else enqueue(MockResponse().setBody("""{"v":1,"kid":"${key.kid}","pk":"${key.base64Url}","fingerprint":"${key.fingerprint}"}"""))
        start(); servers += this
    }

    private fun url(s: MockWebServer) = s.url("/").toString().trimEnd('/')

    private fun signedIn(at: String, pin: KeyPin?): Pair<Sessions, SecureStore> {
        val aes = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val store = SecureStore(MapStore(), AesGcmCipher { aes })
        store.saveSession(Session(at, "device-token-0123456789", pin))
        return Sessions(store) to store
    }

    @Test fun theSameServerAtANewAddressSwitchesAndKeepsTokenAndKey() = runBlocking {
        val pin = KeyPin(keyA, verified = true)
        val newOne = server(keyA)
        val (sessions, store) = signedIn("https://kelo.old.example", pin)
        assertTrue(sessions.canMoveServer)
        assertEquals(AddressMove.MOVED, sessions.moveServer(url(newOne), allowLocalHttp = true))
        assertEquals(url(newOne), sessions.api.value?.baseUrl)
        assertEquals("device-token-0123456789", sessions.api.value?.token)
        assertEquals(pin, sessions.pin)
        assertEquals(Session(url(newOne), "device-token-0123456789", pin), store.session())   // kept for the next start
        assertEquals(pin, store.pinFor(url(newOne)))
    }

    @Test fun aDifferentServerIsRefusedAndNothingChanges() = runBlocking {
        val other = server(keyB)
        val (sessions, store) = signedIn("https://kelo.old.example", KeyPin(keyA, verified = false))
        assertEquals(AddressMove.OTHER_SERVER, sessions.moveServer(url(other), allowLocalHttp = true))
        assertEquals("https://kelo.old.example", sessions.api.value?.baseUrl)
        assertEquals("https://kelo.old.example", store.session()?.serverUrl)
        assertNull(store.pinFor(url(other)))
        // a server without KE2E answers 404: not this server either
        val plain = server(null)
        assertEquals(AddressMove.OTHER_SERVER, sessions.moveServer(url(plain), allowLocalHttp = true))
        assertNotNull(ServerAddressRules.problem(AddressMove.OTHER_SERVER))
    }

    @Test fun anAddressThatAnswersNothingIsRefused() = runBlocking {
        val gone = server(keyA).also { it.shutdown() }
        val (sessions, _) = signedIn("https://kelo.old.example", KeyPin(keyA, verified = true))
        assertEquals(AddressMove.UNREACHABLE, sessions.moveServer(url(gone), allowLocalHttp = true))
        assertEquals("https://kelo.old.example", sessions.api.value?.baseUrl)
    }

    @Test fun anOlderServerWithNoKeyHasNoButton() = runBlocking {
        val newOne = server(keyA)
        val (sessions, _) = signedIn("https://kelo.old.example", null)
        assertFalse(sessions.canMoveServer)
        assertEquals(AddressMove.NOT_SUPPORTED, sessions.moveServer(url(newOne), allowLocalHttp = true))
        assertEquals(0, newOne.requestCount)   // nothing was asked
    }

    @Test fun textThatIsNotAnHttpsAddressOrTheCurrentOneIsRefusedBeforeAsking() = runBlocking {
        val (sessions, _) = signedIn("https://kelo.old.example", KeyPin(keyA, verified = true))
        assertEquals(AddressMove.INVALID, sessions.moveServer("not an address", allowLocalHttp = false))
        assertEquals(AddressMove.INVALID, sessions.moveServer("http://kelo.new.example", allowLocalHttp = false))
        assertEquals(AddressMove.SAME_ADDRESS, sessions.moveServer("https://KELO.old.example/", allowLocalHttp = false))
    }
}
