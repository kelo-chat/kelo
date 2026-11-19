package chat.kelo.app.agent

import androidx.compose.ui.geometry.Offset
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.core.KeloApi
import chat.kelo.app.res.*
import chat.kelo.app.ui.platform.NetworkInfo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import org.jetbrains.compose.resources.StringResource

/**
 * One browser tab's live view: the connection ([LiveSource]) lives in this view model's scope and closes with it, so the page owns no
 * server calls or coroutines of its own. Also holds how the picture is zoomed and moved (the pointer's zoom buttons and wheel, and the
 * phone's pinch), so it survives a recomposition and a rotation.
 *
 * [source] makes the connection from the scope it should run in (the real one: [liveViewModel]).
 */
class LiveViewModel(source: (CoroutineScope) -> LiveSource) : ViewModel() {
    private val live = source(viewModelScope)

    val frame: StateFlow<LiveFrame?> = live.frame
    val state: StateFlow<LiveState> = live.state
    val pointer: StateFlow<LivePointer?> = live.pointer
    /** Address and title while the frames are off (the VNC screen shows); see [LiveHeader]. */
    val meta: StateFlow<LiveMeta?> = live.meta
    // not viewModelScope: the collector needs no main thread (and a test without a Main dispatcher can build the view model)
    private val ownScope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.Default)
    private val own = MutableSharedFlow<StringResource>(extraBufferCapacity = 4)
    /** Short lines for the user: "someone else is controlling", "the browser could not do that", "that did not reach the page" */
    val notices: kotlinx.coroutines.flow.Flow<StringResource> = merge(live.notices, own)

    /** What the owner's own touch looks like while controlling: the finger on the glass, and where the last tap landed (or did not). */
    data class TouchState(val down: Offset? = null, val mark: TouchMark? = null)
    private val _touch = MutableStateFlow(TouchState())
    val touch: StateFlow<TouchState> = _touch.asStateFlow()
    private var markId = 0L

    // ---- live typing (live_v 3): the capture field's changes, in order with every other input
    private var typer: RemoteTypingSender? = null
    private fun typer(): RemoteTypingSender = typer ?: RemoteTypingSender(viewModelScope) { live.send(it) }.also { typer = it }
    private val _typingResets = MutableStateFlow(0)
    /** Bumps whenever the capture field must start again from [RemoteTyping.EMPTY] (the page's field is no longer what the app thinks). */
    val typingResets: StateFlow<Int> = _typingResets.asStateFlow()

    /** The capture field's new state: what changed goes to the page at once (see [RemoteTypingSender]). */
    fun typingUpdate(t: Typed) = typer().update(t)

    /** Sends what is pending now (before any other input, so the order stays the owner's). */
    fun typingFlush() { typer?.flush() }

    /**
     * Named keys for the page (Enter, Tab, arrows, Backspace from the key bar...): what composes is committed first, the keys go as one
     * `edit` (none for an empty list: only the restart), and the capture field starts again (the page's caret moved or its text changed behind the buffer's back).
     */
    fun typingKeys(names: List<String>) {
        typer?.commit()
        if (names.isNotEmpty()) live.send(LiveMessages.edit(names.take(RemoteTyping.MAX_STEPS).map { Step.Key(it) }))
        typer?.reset()
        _typingResets.update { it + 1 }
    }

    /** The page keeps what the owner saw: a composition still showing is committed. */
    fun typingCommit() { typer?.commit() }

    /** The capture field starts again from empty (no steps); false while composing, then nothing changes. */
    fun typingReset(): Boolean = typer?.reset() ?: true

    /** What the page has was last sent as this: the capture field mirrors it after a reset. */
    val typingSent: Typed get() = typer?.lastSent ?: RemoteTyping.EMPTY

    init {
        // an `edit` the page did not take: what it shows is no longer known, the capture field starts again
        ownScope.launch {
            live.replies.collect { r ->
                if (r.input == "edit" && r.notReached) { typer?.forceReset(); _typingResets.update { it + 1 } }
            }
        }
        // the server says a tap was dropped (the page or the phone was busy): the mark that showed it landing turns into a missed one
        ownScope.launch {
            live.replies.collect { r ->
                if (!r.notReached || r.input == "edit") return@collect   // an `edit` the page did not take is the capture field's business (above)
                _touch.update { t -> t.copy(mark = t.mark?.takeIf { it.kind == MarkKind.LANDED }?.let { TouchMarkRules.busy(it, ++markId) } ?: t.mark) }
                own.tryEmit(Res.string.live_touch_missed)
            }
        }
    }

    private val _zoom = MutableStateFlow(1f)
    /** The picture's zoom, [LiveGeometry.MIN_ZOOM] to [LiveGeometry.MAX_ZOOM] */
    val zoom: StateFlow<Float> = _zoom.asStateFlow()
    private val _pan = MutableStateFlow(Offset.Zero)
    val pan: StateFlow<Offset> = _pan.asStateFlow()

    /** On screen: the connection opens. */
    fun start() = live.start()

    /** Off screen (another app, the screen off): the connection closes; the tab stays held for a short while. */
    fun stop() { typer?.commit(); live.stop(giveBack = false) }

    suspend fun control(take: Boolean, handover: Boolean = false): LiveClient.ControlResult {
        if (!take) typer?.commit()   // handing back while a syllable forms: the page keeps it
        return live.control(take, handover)
    }
    /** JPEG frames on or off (off while the VNC screen shows). */
    fun setFrames(on: Boolean) = live.setFrames(on)
    /** Keys, back / forward / reload: with the seq of the frame on screen when the server takes it. */
    fun send(event: JsonObject) {
        if (event["type"].toString().trim('"') != "edit") typer?.flush()   // the owner's order on the socket
        val seq = LiveProtocol.seqFor(live.frame.value?.header)
        live.send(if (seq != null && event["frame_seq"] == null && event["type"].toString().trim('"') in STAMPED) JsonObject(event + ("frame_seq" to kotlinx.serialization.json.JsonPrimitive(seq))) else event)
    }
    fun type(text: String) {
        typer?.let { it.flush(); if (!it.reset()) { it.commit(); it.reset() }; _typingResets.update { n -> n + 1 } }   // a paste: the field starts again
        val seq = LiveProtocol.seqFor(live.frame.value?.header)
        if (seq == null) live.type(text) else LiveMessages.chunks(text).forEach { live.send(LiveMessages.text(it, seq)) }
    }

    /**
     * A pinch or drag: the new zoom and pan, kept inside the picture (clamped to its box in the view of [viewW] x [viewH]). With a
     * [centroid] (the middle between the fingers, in view pixels) the picture point under it stays put while the zoom changes.
     */
    fun transform(zoomBy: Float, panBy: Offset, viewW: Float, viewH: Float, centroid: Offset? = null) {
        val old = _zoom.value
        val z = LiveGeometry.clampZoom(old * zoomBy)
        var px = _pan.value.x
        var py = _pan.value.y
        if (centroid != null && z != old) {
            val (ax, ay) = LiveGeometry.panForZoomAround(centroid.x, centroid.y, old, z, px, py, viewW, viewH)
            px = ax; py = ay
        }
        val h = live.frame.value?.header
        val (x, y) = LiveGeometry.clampPan(px + panBy.x, py + panBy.y, z, viewW, viewH, h?.w ?: 0, h?.h ?: 0)
        _zoom.value = z
        _pan.value = Offset(x, y)
    }

    /** Zoom to [z] around the centre (the zoom buttons, the wheel). */
    fun zoomTo(z: Float, viewW: Float, viewH: Float) {
        val zoom = LiveGeometry.clampZoom(z)
        val h = live.frame.value?.header
        val (x, y) = LiveGeometry.clampPan(_pan.value.x, _pan.value.y, zoom, viewW, viewH, h?.w ?: 0, h?.h ?: 0)
        _zoom.value = zoom
        _pan.value = Offset(x, y)
    }

    /** Back to the whole picture. */
    fun resetZoom() {
        _zoom.value = 1f
        _pan.value = Offset.Zero
    }

    /** Double tap or click: out when zoomed in, else in to [ZOOM_IN]. */
    fun toggleZoom(viewW: Float, viewH: Float) {
        if (_zoom.value > 1.01f) resetZoom() else zoomTo(ZOOM_IN, viewW, viewH)
    }

    // ---- the owner's touch while controlling (every point is mapped by the transform the picture is drawn with)

    /** A finger went down at [at] (view pixels): the ring shows at once, before anything is sent. */
    fun fingerDown(at: Offset) = _touch.update { it.copy(down = at) }

    fun fingerUp() = _touch.update { it.copy(down = null) }

    /**
     * A tap or long press at [at]: sent for the frame the owner was looking at (the one shown now), and a mark shows where it
     * landed. Outside the picture (the letterbox bars) nothing is sent: a "missed" mark and a line say so. Returns whether it was sent.
     */
    fun tap(at: Offset, viewW: Float, viewH: Float, longPress: Boolean = false): Boolean {
        val h = live.frame.value?.header ?: return false
        val (p, mark) = TouchMarkRules.forTouch(++markId, at, viewW, viewH, h.w, h.h, _zoom.value, _pan.value.x, _pan.value.y)
        _touch.update { it.copy(mark = mark) }
        if (p == null) { own.tryEmit(Res.string.live_touch_missed); return false }
        val seq = LiveProtocol.seqFor(h)
        live.send(if (longPress) LiveMessages.longPress(p, h.w, h.h, seq) else LiveMessages.tap(p, h.w, h.h, seq))
        return true
    }

    /** One finger moved by ([dx], [dy]) view pixels, at ([atX], [atY]): the page scrolls (the shown frame's seq goes with it). */
    fun scroll(dx: Float, dy: Float, atX: Float, atY: Float, viewW: Float, viewH: Float) {
        val h = live.frame.value?.header ?: return
        val z = _zoom.value
        val d = LiveGeometry.scrollDelta(dx, dy, viewW, viewH, h.w, h.h, z)
        if (d.x == 0 && d.y == 0) return
        val at = LiveGeometry.toFrame(atX, atY, viewW, viewH, h.w, h.h, z, _pan.value.x, _pan.value.y)
        live.send(LiveMessages.scroll(d, at, h.w, h.h, LiveProtocol.seqFor(h)))
    }

    /** The phone's own swipe, from where the finger went down to where it came up (both must be on the picture). */
    fun swipe(from: Offset, to: Offset, dragMs: Long, viewW: Float, viewH: Float) {
        val h = live.frame.value?.header ?: return
        val z = _zoom.value; val pan = _pan.value
        val a = LiveGeometry.toFrame(from.x, from.y, viewW, viewH, h.w, h.h, z, pan.x, pan.y)
        val b = LiveGeometry.toFrame(to.x, to.y, viewW, viewH, h.w, h.h, z, pan.x, pan.y)
        if (a == null || b == null) { own.tryEmit(Res.string.live_touch_missed); return }
        live.send(LiveMessages.swipe(a, b, h.w, h.h, LiveKinds.swipeMs(dragMs), LiveProtocol.seqFor(h)))
    }

    /** The size of the live area while controlling (null when not): sent as `view` when the server takes it (see [ViewReporter]). */
    fun reportView(v: ViewSize?) = live.setView(v)

    override fun onCleared() {
        ownScope.cancel()
        live.close()
    }

    companion object {
        /** What a double tap zooms in to. */
        const val ZOOM_IN = 2.5f
        /** One tap on a zoom button, and one notch of the wheel with the zoom key held. */
        private val STAMPED = setOf("key", "text", "tap", "long_press", "scroll", "swipe")
        const val BUTTON_STEP = 1.25f
        const val WHEEL_STEP = 1.15f
    }
}

/** The view model of [tabId]'s live view over the real connection ([LiveClient]); [network] says when pictures should be smaller. */
fun liveViewModel(api: KeloApi, tabId: String, network: NetworkInfo) = LiveViewModel { scope ->
    LiveClient(api, tabId, scope) { LiveQuality.pick(runCatching { network.isMetered() }.getOrDefault(false)) }
}
