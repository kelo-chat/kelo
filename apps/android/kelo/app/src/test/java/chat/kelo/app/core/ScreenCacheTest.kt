package chat.kelo.app.core

import chat.kelo.app.Caches
import chat.kelo.app.chat.ChatHistoryCache
import chat.kelo.app.chat.StoredMessage
import chat.kelo.app.my.MyOverview
import chat.kelo.app.my.Usage
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.Executor
import javax.crypto.KeyGenerator

/** Plain JVM: the same AES-GCM code as on the phone, with a software key in place of the Keystore one. */
class ScreenCacheTest {
    @get:Rule val tmp = TemporaryFolder()

    private val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
    private val now = Executor { it.run() }   // writes reach the disk before put() returns
    private val serverA = "https://a.example.com"
    private val serverB = "https://b.example.com"
    private val goals = CacheSlot("goals", ListSerializer(Goal.serializer()))
    private val words = CacheSlot("words", ListSerializer(String.serializer()))

    private fun cache(dir: File = tmp.root, k: javax.crypto.SecretKey = key, max: Int = ScreenCache.MAX_FILE_BYTES) =
        ScreenCache(dir, AesGcmCipher { k }, now, max)

    private fun files(dir: File = tmp.root) = dir.listFiles().orEmpty().filter { it.name.endsWith(".cache") }

    @Test fun roundTripsThroughTheDisk() {
        val list = listOf(Goal(1, "운동", "건강"), Goal(2, "독서", status = Goal.STATUS_DONE))
        cache().put(serverA, goals, list)
        assertEquals(list, cache().get(serverA, goals))   // a fresh instance (a new launch) reads it back from disk
    }

    @Test fun nothingIsWrittenInPlainText() {
        cache().put(serverA, words, listOf("secret-goal-title-xyz"))
        val f = files().single()
        val raw = f.readBytes().toString(Charsets.ISO_8859_1)
        assertFalse(raw.contains("secret-goal-title-xyz"))
        assertFalse(raw.contains("a.example.com"))
        assertFalse(f.name.contains("example"))
    }

    @Test fun eachServerHasItsOwn() {
        val c = cache()
        c.put(serverA, words, listOf("from A"))
        c.put(serverB, words, listOf("from B"))
        assertEquals(listOf("from A"), cache().get(serverA, words))
        assertEquals(listOf("from B"), cache().get(serverB, words))
        assertNull(cache().get("https://c.example.com", words))
    }

    @Test fun aFileMovedToAnotherServersNameIsNotShown() {
        cache().put(serverA, words, listOf("from A"))
        val a = files().single()
        val bName = ScreenCache.serverKey(serverB) + ".words.cache"
        a.copyTo(File(tmp.root, bName))
        assertNull(cache().get(serverB, words))   // the address inside the encrypted data says A
        assertFalse(File(tmp.root, bName).exists())
    }

    @Test fun signingOutWipesEverything() {
        val kv = object : KeyValueStore {
            val map = HashMap<String, String>()
            override fun getString(key: String) = map[key]
            override fun putString(key: String, value: String) { map[key] = value }
            override fun remove(key: String) { map.remove(key) }
        }
        val store = SecureStore(kv, AesGcmCipher { key })
        val c = cache()
        val sessions = Sessions(store, OkHttpClient(), c)
        sessions.signIn(Session(serverA, "tok-0123456789abcdef"))
        sessions.keep(Caches.GOALS, listOf(Goal(1, "운동")))
        sessions.keep(Caches.MESSAGES, listOf(StoredMessage(1, "user", "안녕")))
        assertEquals(2, files().size)
        assertNotNull(sessions.cached(Caches.GOALS))

        sessions.signOut()
        assertTrue(files().isEmpty())
        assertNull(c.get(serverA, Caches.GOALS))
        sessions.signIn(Session(serverA, "tok-other-0123456789"))
        assertNull(sessions.cached(Caches.GOALS))
        assertNull(sessions.cached(Caches.MESSAGES))
    }

    @Test fun anAnswerFromASignedOutSessionIsNotKept() {
        val kv = object : KeyValueStore {
            val map = HashMap<String, String>()
            override fun getString(key: String) = map[key]
            override fun putString(key: String, value: String) { map[key] = value }
            override fun remove(key: String) { map.remove(key) }
        }
        val sessions = Sessions(SecureStore(kv, AesGcmCipher { key }), OkHttpClient(), cache())
        sessions.signIn(Session(serverA, "tok-0123456789abcdef"))
        val old = sessions.requireApi()
        sessions.signOut()
        sessions.signIn(Session(serverA, "tok-new-0123456789ab"))
        sessions.keep(old, Caches.GOALS, listOf(Goal(1, "late")))
        assertNull(sessions.cached(Caches.GOALS))
        assertTrue(files().isEmpty())
    }

    @Test fun aWriteQueuedBeforeClearDoesNotComeBack() {
        val queued = ArrayList<Runnable>()
        val c = ScreenCache(tmp.root, AesGcmCipher { key }, Executor { queued += it })
        c.put(serverA, words, listOf("old"))
        c.clear()
        queued.forEach { it.run() }
        assertTrue(files().isEmpty())
        assertNull(cache().get(serverA, words))
    }

    @Test fun aCorruptFileCountsAsEmpty() {
        cache().put(serverA, words, listOf("ok"))
        val f = files().single()
        f.writeBytes(f.readBytes().also { it[it.size / 2] = (it[it.size / 2].toInt() xor 0x5a).toByte() })
        assertNull(cache().get(serverA, words))
        assertFalse(f.exists())   // dropped, so the next launch does not try again

        File(tmp.root, ScreenCache.serverKey(serverA) + ".goals.cache").writeBytes(byteArrayOf(1, 2, 3))
        assertNull(cache().get(serverA, goals))
        File(tmp.root, ScreenCache.serverKey(serverA) + ".goals.cache").writeBytes(ByteArray(0))
        assertNull(cache().get(serverA, goals))
    }

    @Test fun aFileFromALostKeyCountsAsEmpty() {
        cache().put(serverA, words, listOf("ok"))
        val other = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        assertNull(cache(k = other).get(serverA, words))
    }

    @Test fun aFileOfAnotherShapeCountsAsEmpty() {
        cache().put(serverA, words, listOf("a", "b"))
        // the same file read as another slot's type (an app update changed the format)
        val asNumbers = CacheSlot("words", ListSerializer(Goal.serializer()))
        assertNull(cache().get(serverA, asNumbers))
    }

    @Test fun tooLargeIsNotWritten() {
        val c = cache(max = 2_000)
        c.put(serverA, words, listOf("small"))
        assertEquals(1, files().size)
        c.put(serverA, words, listOf("x".repeat(5_000)))
        assertTrue(files().isEmpty())   // nothing rather than the older, smaller one
        assertNull(cache(max = 2_000).get(serverA, words))
    }

    @Test fun chatHistoryKeepsTheNewest200() {
        val many = (1L..450L).map { StoredMessage(it, if (it % 2 == 0L) "assistant" else "user", "message $it") }
        cache().put(serverA, Caches.MESSAGES, many)
        val back = cache().get(serverA, Caches.MESSAGES)!!
        assertEquals(ChatHistoryCache.MAX_MESSAGES, back.size)
        assertEquals(251L, back.first().id)
        assertEquals(450L, back.last().id)
    }

    @Test fun chatHistoryKeepsWithinItsTextBudget() {
        val long = "가".repeat(100_000)
        val ms = (1L..5L).map { StoredMessage(it, "assistant", long) }
        val kept = ChatHistoryCache.trim(ms)
        assertEquals(listOf(3L, 4L, 5L), kept.map { it.id })
        // one message over the budget on its own is still kept (the newest one)
        val huge = listOf(StoredMessage(1, "assistant", "a"), StoredMessage(2, "assistant", "b".repeat(ChatHistoryCache.MAX_CHARS + 1)))
        assertEquals(listOf(2L), ChatHistoryCache.trim(huge).map { it.id })
        assertEquals(emptyList<StoredMessage>(), ChatHistoryCache.trim(emptyList()))
    }

    @Test fun anEmptyListIsKeptAsEmptyNotMissing() {
        cache().put(serverA, Caches.MESSAGES, emptyList())   // a cleared chat opens empty at once
        assertEquals(emptyList<StoredMessage>(), cache().get(serverA, Caches.MESSAGES))
    }

    @Test fun theMyOverviewRoundTrips() {
        val o = MyOverview(usage = Usage(), devices = 2, documents = 3, sizes = "recommended", version = "1.2.3")
        cache().put(serverA, Caches.MY, o)
        assertEquals(o, cache().get(serverA, Caches.MY))
    }

    @Test fun myOverviewIsOfflineOnlyWhenNothingAnswered() {
        val net = ApiError(ApiError.NO_RESPONSE, "no route")
        assertTrue(MyOverview.offline(listOf(net, net)))
        assertFalse(MyOverview.offline(listOf(net, null)))
        assertFalse(MyOverview.offline(listOf(ApiError(500, "x"), ApiError(404, "y"))))
        assertFalse(MyOverview.offline(emptyList()))
    }
}
