package chat.kelo.app.agent

import chat.kelo.app.core.ApiError
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.IOException
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNull
import kotlin.test.assertTrue

private class FakeSocket : WebSocket {
    val binaries = mutableListOf<ByteString>()
    var closedWith: Int? = null
    override fun request(): Request = Request.Builder().url("https://relay.invalid/").build()
    override fun queueSize(): Long = 0
    override fun send(text: String): Boolean = true
    override fun send(bytes: ByteString): Boolean { binaries += bytes; return true }
    override fun close(code: Int, reason: String?): Boolean { closedWith = code; return true }
    override fun cancel() { closedWith = -1 }
}

private class Harness(private val ticket: () -> String = { "tk" }, answerMs: Long = 300) {
    val socket = FakeSocket()
    val view = ViewSize(500, 900, 2.75f)
    var tickets = 0
    @Volatile var url: String? = null
    @Volatile var listener: WebSocketListener? = null
    val relay = VncRelay(
        base = "/api/browser/sessions/tab1/vnc",
        ticket = { tickets++; ticket() },
        open = { u, l -> url = u; listener = l; socket },
        answerMs = answerMs,
    )

    /** Runs connect, delivers the server's first frame (or nothing), and waits for connect to return. */
    fun connectWith(deliver: Harness.() -> Unit) = runBlocking {
        val job = launch(Dispatchers.Default) { relay.connect(view) }
        while (listener == null) delay(2)
        deliver()
        job.join()
    }

    fun hello(text: String) = listener!!.onMessage(socket, text)
    fun server(bytes: ByteArray) = listener!!.onMessage(socket, bytes.toByteString())
}

private class Sink : VncSink {
    val got = mutableListOf<ByteArray>()
    val events = mutableListOf<String>()
    override fun toPage(bytes: ByteArray) { got += bytes; events += "bytes" }
    override fun reset() { events += "reset" }
}

/** A relay whose every open() gets a new socket, so a screen opened again can be told from the first one. */
private class ReopenHarness(answerMs: Long = 400) {
    val sockets = mutableListOf<FakeSocket>()
    val urls = mutableListOf<String>()
    val listeners = mutableListOf<WebSocketListener>()
    val relay = VncRelay(
        base = "/api/browser/sessions/tab1/vnc",
        ticket = { "tk" },
        open = { u, l -> urls += u; listeners += l; FakeSocket().also { sockets += it } },
        answerMs = answerMs,
    )

    fun shown(i: Int, w: Int, h: Int) = listeners[i].onMessage(sockets[i], """{"type":"vnc","width":$w,"height":$h}""")

    fun connectFirst(view: ViewSize = ViewSize(500, 900, 2.75f)) = runBlocking {
        val job = launch(Dispatchers.Default) { relay.connect(view) }
        while (listeners.isEmpty()) delay(2)
        shown(0, 500, 900)
        job.join()
    }

    /** Runs reopen, delivers the answer of the new socket (or nothing) and waits for reopen to return. */
    fun reopenWith(view: ViewSize, answer: ReopenHarness.() -> Unit) = runBlocking {
        val job = launch(Dispatchers.Default) { relay.reopen(view) }
        while (listeners.size < 2) delay(2)
        answer()
        job.join()
    }
}

class VncRelayTest {
    @Test
    fun aVncFrameOpensTheScreenWithTheTabsQuery() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":500,"height":900}""") }
        assertEquals(VncState.Showing(500, 900), h.relay.state.value)
        assertEquals("/api/browser/sessions/tab1/vnc?ticket=tk&width=500&height=900&density=2.75", h.url)
    }

    @Test
    fun aBusyTabIsRefusedAndTheSocketCloses() {
        val h = Harness()
        h.connectWith { hello("""{"type":"error","code":"busy"}""") }
        assertEquals(VncState.Failed(VncFailure.BUSY), h.relay.state.value)
        assertEquals(1000, h.socket.closedWith)
    }

    @Test
    fun unavailableAndUnknownAnswersFallBack() {
        val a = Harness()
        a.connectWith { hello("""{"type":"error","code":"unavailable"}""") }
        assertEquals(VncState.Failed(VncFailure.UNAVAILABLE), a.relay.state.value)
        val b = Harness()
        b.connectWith { hello("not json at all") }
        assertEquals(VncState.Failed(VncFailure.UNAVAILABLE), b.relay.state.value)
        val c = Harness()
        c.connectWith { hello("""{"type":"vnc","width":0,"height":900}""") }
        assertEquals(VncState.Failed(VncFailure.UNAVAILABLE), c.relay.state.value)
    }

    @Test
    fun noFirstFrameInTimeFallsBack() {
        val h = Harness(answerMs = 100)
        runBlocking { h.relay.connect(h.view) }
        assertEquals(VncState.Failed(VncFailure.NO_ANSWER), h.relay.state.value)
    }

    @Test
    fun aTicketErrorNeverOpensTheSocket() {
        val cases = listOf(404 to VncFailure.UNAVAILABLE, 401 to VncFailure.UNAUTHORIZED, 500 to VncFailure.NO_CONNECTION)
        for ((status, want) in cases) {
            val h = Harness(ticket = { throw ApiError(status, "x") })
            runBlocking { h.relay.connect(h.view) }
            assertEquals(VncState.Failed(want), h.relay.state.value, "status $status")
            assertNull(h.listener)
        }
    }

    @Test
    fun aSocketThatDropsBeforeItsFirstFrameFails() {
        val h = Harness()
        h.connectWith { listener!!.onFailure(socket, IOException("dropped"), null) }
        assertEquals(VncState.Failed(VncFailure.NO_CONNECTION), h.relay.state.value)
    }

    @Test
    fun serverBytesBeforeTheAttachAreHeldAndDeliveredInOrder() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":5,"height":6}""") }
        h.server(byteArrayOf(1, 2))
        h.server(byteArrayOf(3))
        val sink = Sink()
        h.relay.attach(sink)
        h.server(byteArrayOf(4))
        assertEquals(listOf(listOf<Byte>(1, 2), listOf<Byte>(3), listOf<Byte>(4)), sink.got.map { it.toList() })
    }

    @Test
    fun thePagesBytesGoToTheServerOnlyWhileShowing() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":5,"height":6}""") }
        h.relay.send(byteArrayOf(9))
        assertEquals(listOf(byteArrayOf(9).toByteString()), h.socket.binaries)
        val failed = Harness()
        failed.connectWith { hello("""{"type":"error","code":"busy"}""") }
        failed.relay.send(byteArrayOf(7))
        assertTrue(failed.socket.binaries.isEmpty())
    }

    @Test
    fun aSocketThatEndsWhileShowingIsClosed() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":5,"height":6}""") }
        h.listener!!.onClosed(h.socket, 1001, "gone")
        assertEquals(VncState.Closed, h.relay.state.value)
    }

    @Test
    fun closingTheRelayClosesItsSocketAndIgnoresTheOldOne() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":5,"height":6}""") }
        val old = h.listener!!
        h.relay.close()
        assertEquals(1000, h.socket.closedWith)
        assertEquals(VncState.Closed, h.relay.state.value)
        old.onMessage(h.socket, """{"type":"vnc","width":5,"height":6}""")
        old.onMessage(h.socket, byteArrayOf(1).toByteString())
        assertEquals(VncState.Closed, h.relay.state.value)
    }

    @Test
    fun connectRunsOnlyOnceAndTheTicketIsAskedOnce() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":5,"height":6}""") }
        runBlocking { h.relay.connect(h.view) }
        assertEquals(1, h.tickets)
        assertIs<VncState.Showing>(h.relay.state.value)
    }
}

class VncRotationTest {
    private val portrait = ViewSize(500, 900, 2.75f)
    private val landscape = ViewSize(900, 500, 2.75f)

    @Test
    fun aSizeChangeClosesTheSocketAndOpensANewOneWithTheNewSizeKeepingTheScreenShowing() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        val sink = Sink()
        h.relay.attach(sink)
        h.reopenWith(landscape) { shown(1, 900, 500) }
        assertEquals(1000, h.sockets[0].closedWith)                                 // the old /vnc socket is closed
        assertEquals("/api/browser/sessions/tab1/vnc?ticket=tk&width=900&height=500&density=2.75", h.urls[1])
        assertEquals(VncState.Showing(900, 500), h.relay.state.value)
        assertEquals(landscape, h.relay.openedWith.value)
    }

    @Test
    fun theLastPictureStaysWhileTheNewSocketIsOpening() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        runBlocking {
            val job = launch(Dispatchers.Default) { h.relay.reopen(landscape) }
            while (h.listeners.size < 2) delay(2)
            assertEquals(VncState.Showing(500, 900), h.relay.state.value)           // not Opening, not Failed: the page keeps drawing the old screen
            assertTrue(VncRules.showsVnc(h.relay.state.value))
            h.shown(1, 900, 500)
            job.join()
        }
    }

    @Test
    fun theOldSocketsLateBytesNeverReachThePageAndThePageIsToldToStartOverBeforeTheNewBytes() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        val sink = Sink()
        h.relay.attach(sink)
        h.listeners[0].onMessage(h.sockets[0], byteArrayOf(1).toByteString())
        h.reopenWith(landscape) { shown(1, 900, 500) }
        h.listeners[0].onMessage(h.sockets[0], byteArrayOf(2).toByteString())       // the old socket is still talking
        h.listeners[1].onMessage(h.sockets[1], byteArrayOf(3).toByteString())
        assertEquals(listOf("bytes", "reset", "bytes"), sink.events)
        assertEquals(listOf<Byte>(1), sink.got[0].toList())
        assertEquals(listOf<Byte>(3), sink.got[1].toList())
    }

    @Test
    fun thePagesBytesGoToTheNewSocketOnly() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        h.reopenWith(landscape) { shown(1, 900, 500) }
        h.relay.send(byteArrayOf(9))
        assertTrue(h.sockets[0].binaries.isEmpty())
        assertEquals(listOf(byteArrayOf(9).toByteString()), h.sockets[1].binaries)
    }

    @Test
    fun aNewScreenThatIsRefusedOrDoesNotAnswerFallsBackToThePicture() {
        val busy = ReopenHarness()
        busy.connectFirst(portrait)
        busy.reopenWith(landscape) { listeners[1].onMessage(sockets[1], """{"type":"error","code":"busy"}""") }
        assertEquals(VncState.Failed(VncFailure.BUSY), busy.relay.state.value)
        assertTrue(VncRules.framesOn(busy.relay.state.value))
        val silent = ReopenHarness(answerMs = 100)
        silent.connectFirst(portrait)
        runBlocking { silent.relay.reopen(landscape) }
        assertEquals(VncState.Failed(VncFailure.NO_ANSWER), silent.relay.state.value)
        val dropped = ReopenHarness()
        dropped.connectFirst(portrait)
        dropped.reopenWith(landscape) { listeners[1].onFailure(sockets[1], IOException("x"), null) }
        assertEquals(VncState.Failed(VncFailure.NO_CONNECTION), dropped.relay.state.value)
    }

    @Test
    fun aSecondChangeWhileOneIsOpeningDoesNotOpenAnotherSocket() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        runBlocking {
            val job = launch(Dispatchers.Default) { h.relay.reopen(landscape) }
            while (h.listeners.size < 2) delay(2)
            h.relay.reopen(ViewSize(700, 700, 2.75f))
            assertEquals(2, h.sockets.size)
            h.shown(1, 900, 500)
            job.join()
        }
    }

    @Test
    fun nothingIsReopenedUnlessTheScreenIsShowing() {
        val h = ReopenHarness()
        runBlocking { h.relay.reopen(landscape) }
        assertTrue(h.sockets.isEmpty())
    }

    @Test
    fun theDebounceWaits300MsAndAFurtherChangeRestartsIt() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        runBlocking {
            val first = launch(Dispatchers.Default) { h.relay.reconnectIfResized(landscape, imeVisible = false) }
            delay(120)
            first.cancel()                                                              // the area changed again: the effect restarts
            val second = launch(Dispatchers.Default) { h.relay.reconnectIfResized(ViewSize(800, 480, 2.75f), imeVisible = false) }
            delay(200)
            assertEquals(1, h.sockets.size, "only 320 ms after the first change, 200 ms after the last: still waiting")
            while (h.listeners.size < 2) delay(5)
            assertEquals("/api/browser/sessions/tab1/vnc?ticket=tk&width=800&height=480&density=2.75", h.urls[1])
            h.shown(1, 800, 480)
            second.join()
            assertEquals(2, h.sockets.size)
        }
    }

    @Test
    fun aKeyboardIsNotASizeChange() {
        val h = ReopenHarness()
        h.connectFirst(portrait)
        runBlocking { h.relay.reconnectIfResized(ViewSize(500, 500, 2.75f), imeVisible = true) }
        assertEquals(1, h.sockets.size)
    }

    @Test
    fun theRulesTellARotationFromAKeyboard() {
        assertTrue(VncRules.needsReconnect(portrait, landscape, imeVisible = false))
        assertTrue(VncRules.needsReconnect(portrait, landscape, imeVisible = true))     // the width changed: not the keyboard
        assertTrue(VncRules.needsReconnect(portrait, ViewSize(500, 450, 2.75f), imeVisible = false))   // split screen
        assertFalse(VncRules.needsReconnect(portrait, ViewSize(500, 450, 2.75f), imeVisible = true))   // the keyboard only shortens it
        assertFalse(VncRules.needsReconnect(portrait, portrait, imeVisible = false))
        assertTrue(VncRules.needsReconnect(portrait, ViewSize(500, 900, 3f), imeVisible = false))      // another density
        assertFalse(VncRules.needsReconnect(null, landscape, imeVisible = false))
        assertFalse(VncRules.needsReconnect(portrait, null, imeVisible = false))
        assertFalse(VncRules.needsReconnect(portrait, ViewSize(0, 0, 2.75f), imeVisible = false))
        assertEquals(300L, VncRules.RESIZE_DEBOUNCE_MS)
    }

    @Test
    fun theResetMessageHasItsShape() {
        assertEquals("""{"t":"reset"}""", VncBridge.reset())
    }
}

class VncRulesTest {
    @Test
    fun theScreenShowsOnlyWhileItIsShowingAndFramesGoOffThen() {
        assertTrue(VncRules.showsVnc(VncState.Showing(1, 1)))
        assertFalse(VncRules.framesOn(VncState.Showing(1, 1)))
        for (s in listOf(VncState.Idle, VncState.Opening, VncState.Failed(VncFailure.BUSY), VncState.Closed)) {
            assertFalse(VncRules.showsVnc(s), "$s")
            assertTrue(VncRules.framesOn(s), "$s")
        }
    }

    @Test
    fun itConnectsOnceWithAHostAKnownSizeAndALiveTab() {
        val area = ViewSize(500, 900, 2.75f)
        assertTrue(VncRules.shouldConnect(VncState.Idle, true, area, false))
        assertFalse(VncRules.shouldConnect(VncState.Idle, false, area, false))
        assertFalse(VncRules.shouldConnect(VncState.Idle, true, null, false))
        assertFalse(VncRules.shouldConnect(VncState.Idle, true, area, true))
        assertFalse(VncRules.shouldConnect(VncState.Failed(VncFailure.BUSY), true, area, false))
        assertFalse(VncRules.shouldConnect(VncState.Idle, true, ViewSize(0, 900, 2.75f), false))
    }

    @Test
    fun theTicketAndServerErrorsMapToTheirReasons() {
        assertEquals(VncFailure.UNAUTHORIZED, VncRules.ticketFailure(401))
        assertEquals(VncFailure.UNAVAILABLE, VncRules.ticketFailure(404))
        assertEquals(VncFailure.NO_CONNECTION, VncRules.ticketFailure(0))
        assertEquals(VncFailure.BUSY, VncRules.failure("busy"))
        assertEquals(VncFailure.UNAUTHORIZED, VncRules.failure("unauthorized"))
        assertEquals(VncFailure.UNAVAILABLE, VncRules.failure(null))
    }
}

class VncBridgeTest {
    @Test
    fun bytesSurviveTheBase64BridgeBothWays() {
        val all = ByteArray(256) { it.toByte() } + ByteArray(70_000) { (it * 31).toByte() }
        assertEquals(all.toList(), VncBridge.decode(VncBridge.encode(all)).toList())
        val fromNative = VncBridge.fromPage(VncBridge.data(all))
        assertEquals(all.toList(), assertIs<VncBridge.FromPage.Bytes>(fromNative).bytes.toList())
        val fromPage = VncBridge.fromPage("""{"t":"d","b":"${VncBridge.encode(all)}"}""")
        assertEquals(all.toList(), assertIs<VncBridge.FromPage.Bytes>(fromPage).bytes.toList())
    }

    @Test
    fun theControlMessagesHaveTheirShape() {
        assertEquals("""{"t":"hold","on":true}""", VncBridge.hold(true))
        assertEquals("""{"t":"open"}""", VncBridge.open())
        assertEquals(VncBridge.FromPage.Ready, VncBridge.fromPage("""{"t":"ready"}"""))
        assertEquals(VncBridge.FromPage.Unknown, VncBridge.fromPage("""{"t":"what"}"""))
        assertEquals(VncBridge.FromPage.Unknown, VncBridge.fromPage("""{"t":"d","b":"!!not base64!!"}"""))
        assertEquals(VncBridge.FromPage.Unknown, VncBridge.fromPage("garbage"))
    }

    @Test
    fun textTheBrowserCopiedComesFromThePageCappedAndNeverEmpty() {
        assertEquals(VncBridge.FromPage.Clipboard("주소 12"), VncBridge.fromPage("""{"t":"clipboard","text":"주소 12"}"""))
        assertEquals(VncBridge.FromPage.Unknown, VncBridge.fromPage("""{"t":"clipboard","text":""}"""))
        assertEquals(VncBridge.FromPage.Unknown, VncBridge.fromPage("""{"t":"clipboard","text":"   "}"""))
        assertEquals(VncBridge.FromPage.Unknown, VncBridge.fromPage("""{"t":"clipboard"}"""))
        val long = VncBridge.fromPage("""{"t":"clipboard","text":"${"a".repeat(ClipboardRules.MAX_OFFER + 5)}"}""")
        assertEquals(ClipboardRules.MAX_OFFER, assertIs<VncBridge.FromPage.Clipboard>(long).text.length)
    }

    @Test
    fun offeredClipboardTextWaitsForATapAndIsGoneWhenTakenOrWhenTheScreenCloses() {
        val h = Harness()
        h.connectWith { hello("""{"type":"vnc","width":500,"height":900}""") }
        assertNull(h.relay.clipboard.value)
        h.relay.offerClipboard("copied in the browser")
        assertEquals("copied in the browser", h.relay.clipboard.value)
        h.relay.clearClipboard()
        assertNull(h.relay.clipboard.value)
        h.relay.offerClipboard("again")
        h.relay.close()
        assertNull(h.relay.clipboard.value)   // never kept past the screen
    }

    @Test
    fun theLiveSocketFramesMessageHasItsShape() {
        assertEquals("""{"type":"frames","on":false}""", LiveMessages.frames(false).toString())
        assertEquals("""{"type":"frames","on":true}""", LiveMessages.frames(true).toString())
    }
}
