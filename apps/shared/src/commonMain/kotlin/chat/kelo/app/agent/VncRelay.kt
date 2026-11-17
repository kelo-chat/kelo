package chat.kelo.app.agent

import androidx.compose.runtime.Composable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.io.IOException
import java.util.Base64

/**
 * The VNC screen of one browser tab: the tab's X screen (noVNC in the phone's WebView) instead of the JPEG picture. The socket is the
 * server's `/api/browser/sessions/{id}/vnc`, opened with the same one-time ticket and connection code as the live view, so KE2E wraps it
 * the same way. The first text frame says whether the screen is ours ([VncState.Showing]) or why not; everything after it is raw RFB
 * bytes both ways. Bytes that come before the page is attached are held and delivered in order.
 */
sealed interface VncState {
    /** Nothing asked yet */
    data object Idle : VncState
    /** The socket is being opened and the first frame awaited */
    data object Opening : VncState
    /** The server's X screen was sized to [width] x [height] for this tab: the page can draw it */
    data class Showing(val width: Int, val height: Int) : VncState
    /** The screen could not be had: the live view's picture stays (the fallback) */
    data class Failed(val why: VncFailure) : VncState
    /** The socket ended after it was showing: the fallback again */
    data object Closed : VncState
}

enum class VncFailure { BUSY, UNAVAILABLE, UNAUTHORIZED, NO_ANSWER, NO_CONNECTION }

/** The page side of a [VncRelay]: receives the server's RFB bytes. */
interface VncSink {
    fun toPage(bytes: ByteArray)
    /** The relay opened a new socket for a new screen size: the page starts a new RFB session, keeping the old picture until it draws. */
    fun reset() {}
}

/** Pure rules of the VNC screen (the first frame, the bridge messages, which screen shows). */
object VncRules {
    /** How long the server has to answer with its first frame before the live picture is kept */
    const val ANSWER_MS = 4_000L
    /** Frames the server may hold for a page that is not attached yet; past this the screen is given up */
    const val EARLY_LIMIT = 4_096

    sealed interface Hello {
        data class Ready(val width: Int, val height: Int) : Hello
        data class Refused(val why: VncFailure) : Hello
        data object Unknown : Hello
    }

    /** The first text frame: `{"type":"vnc","width":W,"height":H}` or `{"type":"error","code":…}`. */
    fun hello(text: String): Hello = runCatching {
        val o = Json.parseToJsonElement(text).jsonObject
        when (o["type"]?.jsonPrimitive?.contentOrNull) {
            "vnc" -> {
                val w = o["width"]?.jsonPrimitive?.intOrNull ?: 0
                val h = o["height"]?.jsonPrimitive?.intOrNull ?: 0
                if (w > 0 && h > 0) Hello.Ready(w, h) else Hello.Unknown
            }
            "error" -> Hello.Refused(failure(o["code"]?.jsonPrimitive?.contentOrNull))
            else -> Hello.Unknown
        }
    }.getOrDefault(Hello.Unknown)

    /** The server's error code: "busy" = another tab holds the screen; anything else the screen cannot be had for. */
    fun failure(code: String?): VncFailure = when (code) {
        "busy" -> VncFailure.BUSY
        "unauthorized" -> VncFailure.UNAUTHORIZED
        else -> VncFailure.UNAVAILABLE
    }

    /** The ticket request failed: 401 signs out (the app's own handling), 404 = no such tab, anything else = no connection. */
    fun ticketFailure(status: Int): VncFailure = when (status) {
        401 -> VncFailure.UNAUTHORIZED
        404 -> VncFailure.UNAVAILABLE
        else -> VncFailure.NO_CONNECTION
    }

    /** The VNC screen is what the page shows only while it is showing; otherwise the JPEG picture (the fallback). */
    fun showsVnc(state: VncState): Boolean = state is VncState.Showing

    /** Live frames over the WebSocket are sent only while the picture is shown: off while the VNC screen shows. */
    fun framesOn(state: VncState): Boolean = !showsVnc(state)

    /** Pause after the live area stops changing before the screen is opened again at the new size (a rotation settles in steps). */
    const val RESIZE_DEBOUNCE_MS = 300L

    /**
     * Is the live area different enough from the size the screen was opened with to open it again? A rotation or split screen changes
     * the width or density, or the height with no keyboard up. The keyboard only shortens the area ([imeVisible]): the screen stays and
     * the keyboard covers it, so nothing is reopened for that.
     */
    fun needsReconnect(opened: ViewSize?, now: ViewSize?, imeVisible: Boolean): Boolean {
        if (opened == null || now == null || now.width <= 0 || now.height <= 0 || now.density <= 0f) return false
        if (opened.width != now.width || opened.density != now.density) return true
        return opened.height != now.height && !imeVisible
    }

    /** Open the screen now? Only once, when the page is live and knows its size, and a page host exists. */
    fun shouldConnect(state: VncState, hasHost: Boolean, area: ViewSize?, liveEnded: Boolean): Boolean =
        hasHost && state == VncState.Idle && !liveEnded && area != null && area.width > 0 && area.height > 0 && area.density > 0f
}

/** The messages between the native side and the page, always strings (WebMessagePort carries strings). */
object VncBridge {
    fun encode(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)
    fun decode(text: String): ByteArray = Base64.getDecoder().decode(text)

    /** Server bytes for the page. */
    fun data(bytes: ByteArray): String = buildJsonObject { put("t", "d"); put("b", encode(bytes)) }.toString()
    /** Whether the owner holds the tab (input from the page is sent only then). */
    fun hold(on: Boolean): String = buildJsonObject { put("t", "hold"); put("on", on) }.toString()
    /** The page may create its connection now. */
    fun open(): String = """{"t":"open"}"""
    /** A new socket for a new screen size follows: the page starts a new session and swaps it in once it connects, the old picture stays till then. */
    fun reset(): String = """{"t":"reset"}"""

    sealed interface FromPage {
        data class Bytes(val bytes: ByteArray) : FromPage
        /** The browser put text on its clipboard (noVNC's `clipboard` event): offered to the owner, never copied on its own. Capped by [ClipboardRules.offer]. */
        data class Clipboard(val text: String) : FromPage
        data object Ready : FromPage
        data object Unknown : FromPage
    }

    /** What the page sent: `{"t":"d","b":…}` (RFB bytes), `{"t":"ready"}`, `{"t":"clipboard","text":…}`; anything else is Unknown. */
    fun fromPage(text: String): FromPage = runCatching {
        val o: JsonObject = Json.parseToJsonElement(text).jsonObject
        when (o["t"]?.jsonPrimitive?.contentOrNull) {
            "d" -> FromPage.Bytes(decode(o["b"]!!.jsonPrimitive.contentOrNull!!))
            "ready" -> FromPage.Ready
            "clipboard" -> ClipboardRules.offer(o["text"]?.jsonPrimitive?.contentOrNull)?.let { FromPage.Clipboard(it) } ?: FromPage.Unknown
            else -> FromPage.Unknown
        }
    }.getOrDefault(FromPage.Unknown)
}

@Serializable
private data class VncTicket(val ticket: String = "")

/**
 * One tab's VNC screen. [connect] runs once: it asks for the ticket, opens the socket and waits up to [answerMs] for the first frame.
 * The state ends as Showing (the page can attach), Failed (the live picture stays) or Closed (the socket ended later).
 */
class VncRelay internal constructor(
    private val base: String,
    private val ticket: suspend () -> String,
    private val open: (url: String, listener: WebSocketListener) -> WebSocket,
    private val answerMs: Long,
) {
    private val _state = MutableStateFlow<VncState>(VncState.Idle)
    val state: StateFlow<VncState> = _state.asStateFlow()

    private val lock = Any()
    private var socket: WebSocket? = null
    private val _openedWith = MutableStateFlow<ViewSize?>(null)
    /** The live-area size the screen was last opened with (the server sized its X screen to it); changes when the screen is opened again. */
    val openedWith: StateFlow<ViewSize?> = _openedWith.asStateFlow()
    /** Callbacks of an older socket are ignored */
    private var gen = 0
    private var sink: VncSink? = null
    /** Server bytes that came before the page was attached */
    private val early = ArrayList<ByteArray>()
    /** The first frame of the socket being opened has not come yet (state Opening, or Showing while a new size is being opened) */
    private var awaiting = false

    /** [connect] to its end even if the caller is cancelled (the page's effect restarts on a size change): never left Opening. */
    suspend fun connectOnce(view: ViewSize) = withContext(NonCancellable) { connect(view) }

    internal suspend fun connect(view: ViewSize) {
        synchronized(lock) {
            if (_state.value != VncState.Idle) return
            _state.value = VncState.Opening
            awaiting = true
        }
        openSocket(view, reopening = false)
    }

    /**
     * The live area changed size (rotation, split screen): wait [VncRules.RESIZE_DEBOUNCE_MS], then [reconnect] if the size really
     * differs. The caller cancels this when the area changes again, which is what makes it a debounce.
     */
    suspend fun reconnectIfResized(view: ViewSize, imeVisible: Boolean) {
        if (!VncRules.needsReconnect(openedWith.value, view, imeVisible)) return
        delay(VncRules.RESIZE_DEBOUNCE_MS)
        reconnect(view)
    }

    /** Opens the screen again at [view] to its end even if the caller is cancelled. */
    suspend fun reconnect(view: ViewSize) = withContext(NonCancellable) { reopen(view) }

    /**
     * Closes the /vnc socket and opens a new one with the new size (the server sizes the X screen again on acquire). The state stays
     * Showing meanwhile, so the page keeps the last picture; the page starts its new session when the server's first frame arrives.
     * A new screen that cannot be had ends as Failed, and the live view's picture takes over.
     */
    internal suspend fun reopen(view: ViewSize) {
        val old = synchronized(lock) {
            if (_state.value !is VncState.Showing || awaiting) return
            awaiting = true
            gen++
            early.clear()
            socket.also { socket = null }
        }
        old?.close(1000, null)
        openSocket(view, reopening = true)
    }

    private suspend fun openSocket(view: ViewSize, reopening: Boolean) {
        val t = try {
            ticket()
        } catch (e: ApiError) {
            fail(VncRules.ticketFailure(e.status)); return
        } catch (e: IOException) {
            fail(VncFailure.NO_CONNECTION); return
        }
        val answer = CompletableDeferred<Unit>()
        val mine = synchronized(lock) { ++gen }
        val url = "$base?ticket=${KeloApi.segment(t)}&width=${view.width}&height=${view.height}&density=${view.density}"
        val ws = open(url, Listener(mine, answer, view, reopening))
        synchronized(lock) {
            if (mine == gen) socket = ws else ws.close(1000, null)
        }
        if (withTimeoutOrNull(answerMs) { answer.await() } == null) {
            val late = synchronized(lock) {
                if (mine != gen || !awaiting) return@synchronized null
                gen++
                awaiting = false
                _state.value = VncState.Failed(VncFailure.NO_ANSWER)
                socket.also { socket = null }
            }
            late?.close(1000, null)
        }
    }

    /** Attaches the page: the bytes held so far go to it first, in order. */
    fun attach(s: VncSink) = synchronized(lock) {
        for (b in early) s.toPage(b)
        early.clear()
        sink = s
    }

    fun detach(s: VncSink) = synchronized(lock) { if (sink === s) sink = null }

    private val _clipboard = MutableStateFlow<String?>(null)
    /** Text the browser copied, waiting for the owner to take it (a tap on the chip); null when there is none. Held in memory only, never logged. */
    val clipboard: StateFlow<String?> = _clipboard.asStateFlow()

    /** The page reports the browser's clipboard text (already capped by [VncBridge.fromPage]). */
    fun offerClipboard(text: String) { _clipboard.value = text }

    /** The owner took it or let it go: nothing is kept. */
    fun clearClipboard() { _clipboard.value = null }

    /** The page's RFB bytes go to the server as they are (only while showing). */
    fun send(bytes: ByteArray) {
        val ws = synchronized(lock) { if (_state.value is VncState.Showing) socket else null }
        ws?.send(bytes.toByteString())
    }

    /** The page went away for good: the socket closes. */
    fun close() {
        val ws = synchronized(lock) {
            val was = socket
            gen++
            socket = null
            sink = null
            early.clear()
            awaiting = false
            _clipboard.value = null
            if (_state.value == VncState.Opening || _state.value is VncState.Showing) _state.value = VncState.Closed
            was
        }
        ws?.close(1000, null)
    }

    private fun fail(why: VncFailure) = synchronized(lock) { awaiting = false; _state.value = VncState.Failed(why) }

    private inner class Listener(
        private val mine: Int,
        private val answer: CompletableDeferred<Unit>,
        private val view: ViewSize,
        private val reopening: Boolean,
    ) : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
            synchronized(lock) {
                if (mine != gen || !awaiting) return
                awaiting = false
                when (val h = VncRules.hello(text)) {
                    is VncRules.Hello.Ready -> {
                        _openedWith.value = view
                        _state.value = VncState.Showing(h.width, h.height)
                        if (reopening) sink?.reset()   // before any byte of this socket reaches the page
                    }
                    is VncRules.Hello.Refused -> refuse(webSocket, h.why)
                    VncRules.Hello.Unknown -> refuse(webSocket, VncFailure.UNAVAILABLE)
                }
            }
            answer.complete(Unit)
        }

        private fun refuse(webSocket: WebSocket, why: VncFailure) {
            _state.value = VncState.Failed(why)
            gen++
            socket = null
            webSocket.close(1000, null)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            val data = bytes.toByteArray()
            val s = synchronized(lock) {
                if (mine != gen || _state.value !is VncState.Showing) return
                sink ?: run {
                    early.add(data)
                    if (early.size > VncRules.EARLY_LIMIT) { gen++; _state.value = VncState.Closed; socket = null; webSocket.close(1000, null) }
                    return
                }
            }
            s.toPage(data)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
            end()
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = end()

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = end()

        private fun end() {
            synchronized(lock) {
                if (mine == gen) {
                    gen++
                    socket = null
                    val before = awaiting
                    awaiting = false
                    _state.value = when {
                        before -> VncState.Failed(VncFailure.NO_CONNECTION)   // no first frame yet: the screen was never (re)opened
                        _state.value is VncState.Showing -> VncState.Closed
                        else -> _state.value
                    }
                }
            }
            answer.complete(Unit)
        }
    }
}

/** The VNC screen of one tab, with the one-time ticket of the live view and the live view's connection code. */
fun vncRelay(api: KeloApi, tabId: String): VncRelay {
    val path = "/api/browser/sessions/${KeloApi.segment(tabId)}"
    return VncRelay(
        base = "$path/vnc",
        ticket = { api.post<VncTicket>("$path/live-ticket").ticket },
        open = { url, listener -> api.newWebSocket(url, listener) },
        answerMs = VncRules.ANSWER_MS,
    )
}

/**
 * The platform's VNC screen (Android: a WebView with noVNC). Null where there is none (desktop keeps the JPEG picture). It shows the
 * relay's bytes, attaches itself to the relay while it is shown, and holds the page's own input back unless the owner holds the tab.
 */
interface VncScreenHost {
    @Composable
    fun Screen(relay: VncRelay, holding: Boolean, modifier: Modifier)
}

val LocalVncScreenHost = staticCompositionLocalOf<VncScreenHost?> { null }
