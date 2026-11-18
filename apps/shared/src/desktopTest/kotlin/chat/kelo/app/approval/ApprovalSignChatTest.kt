package chat.kelo.app.approval

import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatPlatform
import chat.kelo.app.chat.ChatError
import chat.kelo.app.chat.ChatViewModel
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
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.util.Collections
import javax.crypto.KeyGenerator

/**
 * A payment Allow on a phone with an approval key (the signature goes with the answer) and on the desktop (no key: the server's
 * 403 signature_required keeps the card open and says to approve on the phone). Against a local fake server.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ApprovalSignChatTest {
    private val vms = chat.kelo.app.TestViewModels()
    private lateinit var server: MockWebServer
    private val requests = java.util.concurrent.CopyOnWriteArrayList<RecordedRequest>()
    private val bodies = Collections.synchronizedMap(mutableMapOf<RecordedRequest, String>())
    @Volatile private var answer: MockResponse = MockResponse().setBody("""{"ok":true}""")

    private val paymentAsk = """{"items":[{"type":"approval_request","approval_id":"ap1","id":"c1","name":"confirm_purchase",
        "label":"Buy","detail":"Book the ticket","site":"shop.example","can_remember":false,"asked_at":100.0,"issued_at":1789000000,
        "payment":{"site":"shop.example","summary":"Ticket","amount":1000,"currency":"KRW","amount_text":"1,000 KRW"},"turn_id":"tw"}]}"""

    @Before fun start() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                bodies[request] = request.body.readUtf8()
                requests += request
                val path = request.path.orEmpty()
                return when {
                    request.method == "GET" && path == "/api/approvals/pending" -> MockResponse().setBody(paymentAsk)
                    request.method == "POST" && path == "/api/approvals/ap1" -> answer
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

    private class MapStore : KeyValueStore {
        val map = HashMap<String, String>()
        override fun getString(key: String) = map[key]
        override fun putString(key: String, value: String) { map[key] = value }
        override fun remove(key: String) { map.remove(key) }
    }

    /** A phone's signer: records what it was asked to sign and answers [result]. */
    private class FakeSigner(private val result: ApprovalSigned) : ApprovalSigner {
        override val available = true
        var signed: ByteArray? = null
        override fun publicKey(): String? = "pub"
        override fun sign(message: ByteArray, title: String, subtitle: String, onDone: (ApprovalSigned) -> Unit) {
            signed = message
            onDone(result)
        }
    }

    private fun platform(signer: ApprovalSigner) = object : ChatPlatform {
        override val approvalSigner: ApprovalSigner get() = signer
    }

    private fun vm(platform: ChatPlatform): ChatViewModel {
        val key = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        val store = SecureStore(MapStore(), AesGcmCipher { key }).apply { saveSession(Session(server.url("/").toString().trimEnd('/'), "tok")) }
        return vms.make { ChatViewModel(Sessions(store, requireE2e = false), platform = platform) }
    }

    private fun until(what: String, cond: () -> Boolean) {
        val end = System.currentTimeMillis() + 5000
        while (!cond()) {
            if (System.currentTimeMillis() > end) fail("timed out waiting for $what")
            Thread.sleep(10)
        }
    }

    private fun posts() = synchronized(requests) { requests.filter { it.method == "POST" && it.path.orEmpty().startsWith("/api/approvals/") } }

    @Test fun anAllowOnAPhoneWithAKeySendsTheSignatureOfTheRightMessage() {
        val signer = FakeSigner(ApprovalSigned.Ok("c2ln"))
        val vm = vm(platform(signer))
        vm.refreshAsks(force = true)
        until("the payment card") { vm.state.value.asks.size == 1 }

        vm.answerAsk("ap1", approve = true)
        until("the answer") { posts().isNotEmpty() }

        assertArrayEquals(ApprovalSignRules.message("ap1", true, 1789000000L), signer.signed)
        val body = Json.parseToJsonElement(bodies[posts().single()]!!).jsonObject
        assertEquals("c2ln", body["sig"]!!.jsonPrimitive.content)
        assertEquals("true", body["approve"]!!.jsonPrimitive.content)
    }

    @Test fun aCancelledCheckSendsNothingAndKeepsTheCardOpen() {
        val vm = vm(platform(FakeSigner(ApprovalSigned.Cancelled)))
        vm.refreshAsks(force = true)
        until("the payment card") { vm.state.value.asks.size == 1 }

        vm.answerAsk("ap1", approve = true)
        until("the check to end") { !vm.state.value.asks.single().working }
        Thread.sleep(50)

        assertTrue(posts().isEmpty())
        assertNull(vm.state.value.asks.single().end)
    }

    @Test fun aDesktopAllowIsRefusedWithTheApproveOnThePhoneLineAndStaysOpen() {
        answer = MockResponse().setResponseCode(403).setBody(
            """{"detail":{"code":"signature_required","message":"Approve payments in the Kelo phone app."}}""")
        val vm = vm(ChatPlatform.Default)
        vm.refreshAsks(force = true)
        until("the payment card") { vm.state.value.asks.size == 1 }

        vm.answerAsk("ap1", approve = true)
        until("the refusal") { vm.state.value.items.any { it is ChatItem.Error } }

        val error = (vm.state.value.items.single { it is ChatItem.Error } as ChatItem.Error).error
        assertTrue(error is ChatError.Localized)
        assertTrue(vm.state.value.asks.single().open)
        assertEquals(1, posts().size)
        assertTrue(!Json.parseToJsonElement(bodies[posts().single()]!!).jsonObject.containsKey("sig"))
    }

    @Test fun aRefusedPhoneSignatureShowsTheServersMessage() {
        answer = MockResponse().setResponseCode(403).setBody(
            """{"detail":{"code":"bad_signature","message":"This payment approval was not signed by this phone."}}""")
        val vm = vm(platform(FakeSigner(ApprovalSigned.Ok("c2ln"))))
        vm.refreshAsks(force = true)
        until("the payment card") { vm.state.value.asks.size == 1 }

        vm.answerAsk("ap1", approve = true)
        until("the refusal") { vm.state.value.items.any { it is ChatItem.Error } }

        val error = (vm.state.value.items.single { it is ChatItem.Error } as ChatItem.Error).error
        assertEquals("This payment approval was not signed by this phone.", (error as ChatError.Message).text)
        assertTrue(vm.state.value.asks.single().open)
    }
}
