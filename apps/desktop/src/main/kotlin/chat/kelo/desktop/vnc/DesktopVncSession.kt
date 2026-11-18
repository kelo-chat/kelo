package chat.kelo.desktop.vnc

import chat.kelo.app.agent.VncSink
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * The desktop's VNC screen: the page side of the shared relay. It takes the server's bytes ([toPage]) into an RFB session, and it
 * sends the owner's input (keys, pointer, clipboard request) through [send] while the owner holds the tab.
 *
 * A new socket for a new screen size ([reset]) starts a new session with a new frame buffer; the old picture stays on screen until
 * the new session's first update, or [ANSWER_CAP_MS] after the reset ([tick] swaps then, as the phones' page does).
 *
 * [dispatcher] runs the sessions (the default is a background pool; tests pass one that runs inline). [clock] is the time in ms.
 */
class DesktopVncSession(
    private val scope: CoroutineScope,
    private val send: (ByteArray) -> Unit,
    private val offerClipboard: (String) -> Unit,
    private val onFailed: (String) -> Unit,
    macOs: Boolean,
    private val dispatcher: CoroutineDispatcher = Dispatchers.Default,
    private val clock: () -> Long = System::currentTimeMillis,
) : VncSink {
    private val _frame = MutableStateFlow<FrameBuffer?>(null)
    /** The picture on screen: null until the first update. */
    val frame: StateFlow<FrameBuffer?> = _frame.asStateFlow()

    private val _cursor = MutableStateFlow<RfbCursorImage?>(null)
    /** The server's cursor shape, drawn only while the owner holds the tab. */
    val cursor: StateFlow<RfbCursorImage?> = _cursor.asStateFlow()

    val keys = RfbKeys(send, macOs)
    val pointer = RfbPointer(send)

    private var holding = false
    private var current: Attempt? = null
    private var pending: Attempt? = null
    private var resetAt = 0L
    private var closed = false

    init {
        current = Attempt(FrameBuffer(1, 1)).also { it.start() }
    }

    /** The server's RFB bytes (the relay's order). They go to the new session when a new socket is opened. */
    @Synchronized
    override fun toPage(bytes: ByteArray) {
        (pending ?: current)?.reader?.feed(bytes)
    }

    /** The relay opened a new socket: a new session starts; the old picture stays until the new one draws (or [tick] at the cap). */
    @Synchronized
    override fun reset() {
        if (closed) return
        pending?.stop()
        pending = Attempt(FrameBuffer(1, 1)).also { it.start() }
        resetAt = clock()
    }

    /** Called now and then while the screen shows: swaps to a new session that has not drawn [ANSWER_CAP_MS] after its reset. */
    @Synchronized
    fun tick() {
        val p = pending ?: return
        if (clock() - resetAt >= ANSWER_CAP_MS) swapTo(p)
    }

    /** The owner took the tab (true) or gave it back (false). Giving it back lifts every key and button still down. */
    fun setHolding(on: Boolean) {
        holding = on
        pointer.setHolding(on)
        if (!on) keys.releaseAll()
    }

    /** A key went down on the screen: [codePoint] is what it types, [ctrl] whether Ctrl (⌘ on macOS) is held. */
    fun keyDown(keyCode: Long, codePoint: Int, ctrl: Boolean) {
        if (holding) keys.press(keyCode, codePoint, ctrl)
    }

    fun keyUp(keyCode: Long) {
        keys.release(keyCode)
    }

    /** The window lost focus: nothing stays down on the page. */
    fun focusLost() {
        keys.releaseAll()
        pointer.releaseAll()
    }

    /** Stops the sessions: the page has gone for good. */
    fun close() {
        // the lock is not held while the keys and buttons go up: the relay's thread may be waiting for it in reset()
        val wasOpen = synchronized(this) {
            if (closed) return
            closed = true
            pending?.stop()
            current?.stop()
            pending = null
            current = null
            true
        }
        if (wasOpen) focusLost()
    }

    @Synchronized
    private fun swapTo(p: Attempt) {
        current?.stop()
        current = p
        pending = null
        _frame.value = p.frame
    }

    /** One RFB connection: its frame buffer, its bytes and its session, which runs on [dispatcher]. */
    private inner class Attempt(val frame: FrameBuffer) {
        val reader = RfbByteReader()
        private var job: Job? = null
        private val events = object : RfbEvents {
            override fun updated() {
                synchronized(this@DesktopVncSession) {
                    if (closed) return
                    val p = pending
                    when {
                        p === this@Attempt -> swapTo(this@Attempt)
                        current === this@Attempt -> _frame.value = frame
                    }
                }
            }

            override fun failed(why: String) {
                // outside the lock: onFailed closes the relay, whose thread may be waiting for this lock in reset()
                val report = synchronized(this@DesktopVncSession) { !closed && (this@Attempt === current || this@Attempt === pending) }
                if (report) onFailed(why)
            }

            override fun cursor(image: RfbCursorImage?) {
                synchronized(this@DesktopVncSession) {
                    if (!closed && this@Attempt === current) _cursor.value = image
                }
            }
        }
        private val session = RfbSession(
            reader, send, frame, events,
            RfbClipboard(send = send, holding = { holding }, offer = offerClipboard),
        )

        fun start() {
            job = scope.launch(dispatcher) { session.run() }
        }

        fun stop() {
            job?.cancel()
            reader.end()
        }
    }

    companion object {
        /** How long the old picture is kept after a new socket was opened (view.js's cap) */
        const val ANSWER_CAP_MS = 4_000L
    }
}
