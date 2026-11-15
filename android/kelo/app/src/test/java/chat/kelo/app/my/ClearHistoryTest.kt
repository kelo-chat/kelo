package chat.kelo.app.my

import chat.kelo.app.Caches
import chat.kelo.app.chat.StoredMessage
import chat.kelo.app.core.AesGcmCipher
import chat.kelo.app.core.KeyValueStore
import chat.kelo.app.core.ScreenCache
import chat.kelo.app.core.SecureStore
import chat.kelo.app.core.Session
import chat.kelo.app.core.Sessions
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.concurrent.Executor
import javax.crypto.KeyGenerator

/** After the server cleared the chat history: the kept chat is empty, the screens are told, the phone's notifications go. */
class ClearHistoryTest {
    @get:Rule val tmp = TemporaryFolder()

    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()

    private fun sessions(): Sessions {
        val kv = object : KeyValueStore {
            val map = HashMap<String, String>()
            override fun getString(key: String) = map[key]
            override fun putString(key: String, value: String) { map[key] = value }
            override fun remove(key: String) { map.remove(key) }
        }
        return Sessions(SecureStore(kv, AesGcmCipher { key }), OkHttpClient(), ScreenCache(tmp.root, AesGcmCipher { key }, Executor { it.run() }), requireE2e = false)
            .apply { signIn(Session("https://a.example.com", "tok-0123456789abcdef")) }
    }

    @Test fun forgetsTheKeptChatTellsTheScreensAndTakesDownNotifications() {
        val s = sessions()
        s.keep(Caches.MESSAGES, listOf(StoredMessage(1, "user", "hello"), StoredMessage(2, "assistant", "hi")))
        var cancelled = 0
        assertEquals(0, s.historyCleared.value)

        ClearHistory.forgetLocally(s) { cancelled++ }

        assertEquals(emptyList<StoredMessage>(), s.cached(Caches.MESSAGES))
        assertEquals(1, s.historyCleared.value)
        assertEquals(1, cancelled)
        ClearHistory.forgetLocally(s) { error("notifications are off") }   // a failure there does not undo the rest
        assertEquals(2, s.historyCleared.value)
    }
}
