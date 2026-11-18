package chat.kelo.app.agent

import androidx.compose.ui.geometry.Offset
import chat.kelo.app.res.*
import org.jetbrains.compose.resources.StringResource
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import kotlin.math.min
import kotlin.math.roundToInt

/*
 * The live view of a browser tab (server.py "LIVE VIEW API"), the parts that need no Android: where the picture sits on screen and
 * how a touch becomes a point on the page, when to reconnect and when to fall back to fetching one picture at a time, and the
 * messages going both ways. The screen itself is in LivePage.kt, the connection in LiveClient.kt.
 */

/** Where a frame of [frameW] x [frameH] is drawn inside a view (ContentScale.Fit: the whole picture, centred, letterboxed). */
data class FitBox(val scale: Float, val left: Float, val top: Float, val width: Float, val height: Float)

/** A point on the frame, in its own pixels (what tap / long_press send). */
data class FramePoint(val x: Int, val y: Int)

object LiveGeometry {
    const val MIN_ZOOM = 1f
    const val MAX_ZOOM = 5f

    /** The picture's box in the view; null while either size is unknown. */
    fun fit(viewW: Float, viewH: Float, frameW: Int, frameH: Int): FitBox? {
        if (viewW <= 0f || viewH <= 0f || frameW <= 0 || frameH <= 0) return null
        val s = min(viewW / frameW, viewH / frameH)
        val w = frameW * s
        val h = frameH * s
        return FitBox(s, (viewW - w) / 2f, (viewH - h) / 2f, w, h)
    }

    /**
     * A touch at ([x], [y]) in view pixels, as a frame pixel. The view is zoomed by [zoom] around its centre and then moved by
     * ([panX], [panY]) (what graphicsLayer does with scale and translation); null when the touch is outside the picture (the
     * letterbox bars), so a tap there sends nothing.
     */
    fun toFrame(x: Float, y: Float, viewW: Float, viewH: Float, frameW: Int, frameH: Int, zoom: Float = 1f, panX: Float = 0f, panY: Float = 0f): FramePoint? {
        val box = fit(viewW, viewH, frameW, frameH) ?: return null
        val z = zoom.coerceAtLeast(0.01f)
        val cx = viewW / 2f
        val cy = viewH / 2f
        // undo the zoom and pan: the point of the un-zoomed view under the finger
        val ux = cx + (x - panX - cx) / z
        val uy = cy + (y - panY - cy) / z
        val fx = (ux - box.left) / box.scale
        val fy = (uy - box.top) / box.scale
        if (fx < 0f || fy < 0f || fx > frameW || fy > frameH) return null
        return FramePoint(fx.toInt().coerceIn(0, frameW - 1), fy.toInt().coerceIn(0, frameH - 1))
    }

    /**
     * A finger drag of ([dx], [dy]) view pixels as a scroll of the page, in frame pixels. Natural scrolling: moving the finger up
     * scrolls the page down, so the signs flip.
     */
    fun scrollDelta(dx: Float, dy: Float, viewW: Float, viewH: Float, frameW: Int, frameH: Int, zoom: Float = 1f): FramePoint {
        val box = fit(viewW, viewH, frameW, frameH) ?: return FramePoint(0, 0)
        val k = box.scale * zoom.coerceAtLeast(0.01f)
        return FramePoint((-dx / k).roundToInt(), (-dy / k).roundToInt())
    }

    /**
     * The pan kept inside the zoomed picture: the picture (the frame's box, not the whole view) may not leave a gap at an edge it
     * is wider or taller than the view at; a picture smaller than the view stays centred. Without the frame size ([frameW] or
     * [frameH] 0) the whole view stands in for the picture.
     */
    fun clampPan(panX: Float, panY: Float, zoom: Float, viewW: Float, viewH: Float, frameW: Int = 0, frameH: Int = 0): Pair<Float, Float> {
        val box = fit(viewW, viewH, frameW, frameH)
        val picW = box?.width ?: viewW
        val picH = box?.height ?: viewH
        val z = zoom.coerceAtLeast(0f)
        val maxX = ((picW * z - viewW) / 2f).coerceAtLeast(0f)
        val maxY = ((picH * z - viewH) / 2f).coerceAtLeast(0f)
        return (panX.coerceIn(-maxX, maxX) + 0f) to (panY.coerceIn(-maxY, maxY) + 0f)   // + 0f: never -0.0
    }

    /**
     * The pan that keeps the picture point under ([cx], [cy]) where it is while the zoom goes from [zoom] to [newZoom] (a pinch
     * around the fingers' middle, not around the view's centre).
     */
    fun panForZoomAround(cx: Float, cy: Float, zoom: Float, newZoom: Float, panX: Float, panY: Float, viewW: Float, viewH: Float): Pair<Float, Float> {
        val z = zoom.coerceAtLeast(0.01f)
        val k = newZoom / z
        val mx = viewW / 2f
        val my = viewH / 2f
        return (cx - mx - (cx - mx - panX) * k + 0f) to (cy - my - (cy - my - panY) * k + 0f)
    }

    /**
     * The other way of [toFrame]: where the frame point ([fx], [fy]) is in the view, with the zoom and pan. The pointer overlay, the
     * touch marks and the hit test all go through [fit] and this pair, so what is drawn and what is hit are one transform.
     */
    fun frameToView(fx: Float, fy: Float, viewW: Float, viewH: Float, frameW: Int, frameH: Int, zoom: Float = 1f, panX: Float = 0f, panY: Float = 0f): Offset? {
        val box = fit(viewW, viewH, frameW, frameH) ?: return null
        val ux = box.left + fx * box.scale
        val uy = box.top + fy * box.scale
        val z = zoom.coerceAtLeast(0.01f)
        val cx = viewW / 2f
        val cy = viewH / 2f
        return Offset(cx + (ux - cx) * z + panX, cy + (uy - cy) * z + panY)
    }

    fun clampZoom(zoom: Float): Float = zoom.coerceIn(MIN_ZOOM, MAX_ZOOM)

    /** Map viewport CSS pixels to picture (frame) pixels (a picture pixel is x * frame_w / css_w, y * frame_h / css_h). */
    fun cssToFrame(cssX: Float, cssY: Float, frameW: Int, frameH: Int, cssW: Int, cssH: Int): Offset {
        val x = if (cssW > 0) cssX * frameW.toFloat() / cssW.toFloat() else cssX
        val y = if (cssH > 0) cssY * frameH.toFloat() / cssH.toFloat() else cssY
        return Offset(x, y)
    }

    /**
     * Map a pointer position in CSS pixels to view coordinates inside [viewW] x [viewH],
     * accounting for letterboxing (ContentScale.Fit), [zoom] and [panX], [panY].
     */
    fun pointerToView(
        cssX: Float, cssY: Float,
        viewW: Float, viewH: Float,
        frameW: Int, frameH: Int,
        cssW: Int, cssH: Int,
        zoom: Float = 1f, panX: Float = 0f, panY: Float = 0f,
    ): Offset? {
        val framePt = cssToFrame(cssX, cssY, frameW, frameH, cssW, cssH)
        return frameToView(framePt.x, framePt.y, viewW, viewH, frameW, frameH, zoom, panX, panY)
    }
}

/** How sharp and how often: 720 px / 55 / 10 fps on Wi-Fi, less on a metered network (mobile data). */
data class LiveQuality(val maxWidth: Int, val quality: Int, val fps: Int) {
    companion object {
        val WIFI = LiveQuality(720, 55, 10)
        val METERED = LiveQuality(540, 45, 5)
        fun pick(metered: Boolean): LiveQuality = if (metered) METERED else WIFI
    }

    fun message(): JsonObject = buildJsonObject {
        put("type", "quality"); put("max_width", maxWidth); put("quality", quality); put("fps", fps)
    }

    fun query(): String = "max_width=$maxWidth&quality=$quality&fps=$fps"
}

/** {"type":"frame",...}: the JPEG that follows it is [bytes] long, [w] x [h] pixels. */
@Serializable
data class FrameHeader(
    val seq: Long = 0,
    val w: Int = 0,
    val h: Int = 0,
    val ts: Double = 0.0,
    val url: String = "",
    val title: String = "",
    val bytes: Int = 0,
    @SerialName("css_w") val cssW: Int = 0,
    @SerialName("css_h") val cssH: Int = 0,
    /** A secret field (password, card, one-time code) shows in the picture; null = the server did not say (an older one). */
    val secure: Boolean? = null,
    /** 2 or more: the server takes `view`, `frame_seq` and says `editing` (older servers leave it 0). */
    @SerialName("live_v") val liveV: Int = 0,
    /** A text field has focus on the page / phone. */
    val editing: Boolean = false,
)

@Serializable
data class LiveAgent(
    val busy: Boolean = false,
    val status: String? = null,
    @SerialName("task_id") val taskId: Long? = null,
    val title: String? = null,
    @SerialName("waiting_approval") val waitingApproval: Boolean = false,
)

/** The agent's pointer in page CSS px (the same space as frame metadata css_w / css_h). */
@Serializable
data class LivePointer(
    val x: Float = 0f,
    val y: Float = 0f,
    val down: Boolean = false,
)

/** {"type":"status"}: who controls the tab, what the agent is doing in it, how many are watching. */
@Serializable
data class LiveStatus(
    val controller: String = "agent",
    @SerialName("controlled_by") val controlledBy: String? = null,
    @SerialName("you_control") val youControl: Boolean = false,
    val label: String = "",
    val agent: LiveAgent = LiveAgent(),
    val viewers: Int = 0,
    val pointer: LivePointer? = null,
    @SerialName("page_busy") val pageBusy: Boolean = false,
    val stale: Boolean = false,
    /** "button": the owner's own 직접 조작 (only hand back ends it); "handover": the app took it because Kelo asked; "": nobody (an older server) */
    val hold: String = "",
    /** The holder has no live connection, so another device may take the tab (the take moves it) */
    @SerialName("holder_away") val holderAway: Boolean = false,
    /** "work" on a tab of Kelo's work browser; "main" or "" (an older server) for the owner's own browser */
    val browser: String = "",
) {
    /** Another phone (or the owner's app) holds the tab */
    val someoneElse: Boolean get() = controller == "user" && !youControl

    /** The owner holds the tab for a hand-over Kelo asked for: leaving the page gives it back (owner decision 2026-11-18). */
    val handoverHold: Boolean get() = youControl && hold == "handover"

    /** The tab is in Kelo's work browser, apart from the owner's own tabs. */
    val inWorkBrowser: Boolean get() = browser == "work"
}

/**
 * {"type":"meta","url","title","editing"}: the tab's address and title and whether a text field has focus, sent while this viewer's
 * frames are off (the VNC screen shows, so the frame headers that normally carry them do not come). A frame arriving again takes over.
 * A server from before `editing` leaves it out: false.
 */
@Serializable
data class LiveMeta(val url: String = "", val title: String = "", val editing: Boolean = false)

/** What the live page's header shows of the page: the newest word from the server, a meta line while frames are off, else the frame header. */
object LiveHeader {
    fun title(meta: LiveMeta?, header: FrameHeader?): String? = (meta?.title ?: header?.title)?.ifBlank { null }
    fun url(meta: LiveMeta?, header: FrameHeader?): String? = (meta?.url ?: header?.url)?.ifBlank { null }

    /**
     * A text field has focus on the page: the meta line while frames are off (the last frame header is old then and would never say
     * it), else the frame header. This is what raises the keyboard into the type line.
     */
    fun editing(meta: LiveMeta?, header: FrameHeader?): Boolean = meta?.editing ?: (header?.editing == true)
}

/** Text going between the phone's clipboard and the page's: only when the owner taps, never on its own, never kept past use. */
object ClipboardRules {
    /** The most text taken from the browser's clipboard for the owner to copy */
    const val MAX_OFFER = 10_000

    /** The phone's clipboard text to type into the page: null when there is none (the paste key stays off), cut at what one entry takes. */
    fun pasteText(clip: String?): String? = clip?.takeIf { it.isNotEmpty() }?.take(LiveMessages.MAX_TEXT)

    /** The browser's clipboard text offered to the owner: null for nothing worth offering, cut at [MAX_OFFER]. */
    fun offer(text: String?): String? = text?.takeIf { it.isNotBlank() }?.take(MAX_OFFER)

    /** The words of the paste key and of the "copied" notice: the phone's own, or the computer's (the desktop's VNC screen). */
    fun words(desktop: Boolean): ClipboardWords =
        if (desktop) ClipboardWords(Res.string.desktop_live_paste, Res.string.desktop_live_copied)
        else ClipboardWords(Res.string.live_paste, Res.string.live_copied_to_phone)
}

/** Which strings the clipboard words use: the paste key's description and the notice after text was copied. */
data class ClipboardWords(val paste: StringResource, val copied: StringResource)

/** One text message from the server, sorted by type. */
sealed interface LiveIn {
    data class Meta(val meta: LiveMeta) : LiveIn
    data class Frame(val header: FrameHeader) : LiveIn
    data class Status(val status: LiveStatus) : LiveIn
    data class Pointer(val pointer: LivePointer) : LiveIn
    data class Ended(val reason: String) : LiveIn
    data class Error(val code: String) : LiveIn
    /** The answer to an input sent with "ack" (a server with live_v 2 or more). */
    data class InputOk(val reply: InputReply) : LiveIn
    data object Pong : LiveIn
    data object Other : LiveIn
}

/** The messages of the live view, both ways. Typed text is never part of anything but the one message that carries it. */
object LiveMessages {
    private val json = chat.kelo.app.core.KeloApi.json

    fun parse(text: String): LiveIn {
        val o = runCatching { json.parseToJsonElement(text) as? JsonObject }.getOrNull() ?: return LiveIn.Other
        fun s(k: String) = (o[k] as? JsonPrimitive)?.contentOrNull.orEmpty()
        return runCatching {
            when (s("type")) {
                "frame" -> LiveIn.Frame(json.decodeFromJsonElement(FrameHeader.serializer(), o))
                "status" -> LiveIn.Status(json.decodeFromJsonElement(LiveStatus.serializer(), o))
                "meta" -> LiveIn.Meta(json.decodeFromJsonElement(LiveMeta.serializer(), o))
                "pointer" -> LiveIn.Pointer(json.decodeFromJsonElement(LivePointer.serializer(), o))
                "ended" -> LiveIn.Ended(s("reason"))
                "error" -> LiveIn.Error(s("code"))
                "input_ok" -> LiveIn.InputOk(InputReply.of(o))
                "pong" -> LiveIn.Pong
                else -> LiveIn.Other
            }
        }.getOrDefault(LiveIn.Other)
    }

    fun ping(): String = """{"type":"ping"}"""

    /** [frameSeq] = the frame the owner was looking at (only for a server with live_v 2: see [LiveProtocol.seqFor]); null leaves it out. */
    fun tap(p: FramePoint, frameW: Int, frameH: Int, frameSeq: Long? = null): JsonObject = point("tap", p, frameW, frameH, frameSeq)

    fun longPress(p: FramePoint, frameW: Int, frameH: Int, frameSeq: Long? = null): JsonObject = point("long_press", p, frameW, frameH, frameSeq)

    fun scroll(d: FramePoint, at: FramePoint?, frameW: Int, frameH: Int, frameSeq: Long? = null): JsonObject = buildJsonObject {
        put("type", "scroll"); put("dx", d.x); put("dy", d.y)
        if (at != null) { put("x", at.x); put("y", at.y) }
        put("frame_w", frameW); put("frame_h", frameH)
        if (frameSeq != null) put("frame_seq", frameSeq)
    }

    fun text(text: String, frameSeq: Long? = null): JsonObject = buildJsonObject {
        put("type", "text"); put("text", text)
        if (frameSeq != null) put("frame_seq", frameSeq)
    }

    fun key(name: String, frameSeq: Long? = null): JsonObject = buildJsonObject {
        put("type", "key"); put("key", name)
        if (frameSeq != null) put("frame_seq", frameSeq)
    }

    /** The Android device's keys: back | home | enter | recents. */
    fun deviceKey(name: String): JsonObject = key(name)

    /** A finger drag on the device's screen, from [from] to [to] in frame pixels, taking [ms] milliseconds. */
    fun swipe(from: FramePoint, to: FramePoint, frameW: Int, frameH: Int, ms: Int, frameSeq: Long? = null): JsonObject = buildJsonObject {
        put("type", "swipe"); put("x1", from.x); put("y1", from.y); put("x2", to.x); put("y2", to.y); put("ms", ms)
        put("frame_w", frameW); put("frame_h", frameH)
        if (frameSeq != null) put("frame_seq", frameSeq)
    }

    /** The size of the live area in physical pixels and its density (sent when controlling starts, on rotation, when the keyboard moves). */
    fun view(v: ViewSize): JsonObject = buildJsonObject {
        put("type", "view"); put("width", v.width); put("height", v.height); put("density", v.density)
    }

    /** Back | forward | reload */
    fun nav(kind: String): JsonObject = buildJsonObject { put("type", kind) }

    /** Taking the tab: `by` "handover" when the app took it because Kelo asked (a hand-over hold); the owner's own press sends none. */
    fun control(take: Boolean, handover: Boolean = false): JsonObject = buildJsonObject {
        put("take", take)
        if (take && handover) put("by", "handover")
    }

    /** Whether the server sends JPEG frames over this socket (`frames`, live socket only). */
    fun frames(on: Boolean): JsonObject = buildJsonObject { put("type", "frames"); put("on", on) }

    private fun point(type: String, p: FramePoint, frameW: Int, frameH: Int, frameSeq: Long?) = buildJsonObject {
        put("type", type); put("x", p.x); put("y", p.y); put("frame_w", frameW); put("frame_h", frameH)
        if (frameSeq != null) put("frame_seq", frameSeq)
        put("ack", true)   // the reply (input_ok) says where it landed, and whether it was dropped (busy)
    }

    /** The control answer (POST …/control) or the X-You-Control / X-Controller headers, as a [LiveStatus] update. */
    fun controlAnswer(o: JsonObject?, before: LiveStatus): LiveStatus {
        if (o == null) return before
        fun s(k: String) = (o[k] as? JsonPrimitive)?.contentOrNull
        return before.copy(
            controller = s("controller") ?: before.controller,
            controlledBy = s("controlled_by"),
            youControl = (o["you_control"] as? JsonPrimitive)?.booleanOrNull ?: before.youControl,
            hold = s("hold").orEmpty(),
            holderAway = (o["holder_away"] as? JsonPrimitive)?.booleanOrNull == true,
        )
    }

    /** Text longer than the server takes in one message is sent in pieces of at most [max] characters. */
    fun chunks(text: String, max: Int = MAX_TEXT): List<String> = if (text.isEmpty()) emptyList() else text.chunked(max)

    const val MAX_TEXT = 2000

    /** The keys the server knows, with what the buttons show. */
    val KEYS = listOf("Enter", "Backspace", "Tab", "Escape", "ArrowLeft", "ArrowUp", "ArrowDown", "ArrowRight")
}

/** Where the live view is. */
enum class LiveMode {
    /** Asking for a ticket / opening the WebSocket */
    CONNECTING,
    /** Frames come over the WebSocket */
    LIVE,
    /** Frames come one at a time (GET …/frame?after=&wait=10): the WebSocket could not be opened */
    POLLING,
    /** Waiting before trying again (the network dropped, too many attempts) */
    WAITING,
    /** The tab is gone: nothing more will come */
    ENDED,
}

/** Why there is nothing more to show. */
enum class LiveEnd { TAB_CLOSED, NO_TAB, NO_BROWSER }

/** What the connection should do next. */
sealed interface LiveStep {
    /** Open the WebSocket (with a new ticket when [freshTicket]) after [delayMs] */
    data class OpenSocket(val freshTicket: Boolean = true, val delayMs: Long = 0) : LiveStep
    /** Fetch frames one at a time after [delayMs] */
    data class Poll(val delayMs: Long = 0) : LiveStep
    data class End(val reason: LiveEnd) : LiveStep
}

/**
 * The reconnect rules, apart from the network so they can be tested:
 * - the WebSocket fails to connect twice in a row (a proxy without WebSocket support) → fetch frames one at a time from then on;
 * - 4401 (the ticket was used or expired) → one new ticket and one more try, then frames one at a time (they use the bearer token);
 * - 4404 or "ended" → the tab is gone; 4408 (silent for 60 s, e.g. the phone slept) → reconnect at once;
 * - 4429 (too many failed attempts) → wait half a minute;
 * - a dropped connection that had worked → reconnect, waiting a little longer each time (at most 15 s).
 */
class LiveMachine {
    var mode: LiveMode = LiveMode.CONNECTING
        private set
    var end: LiveEnd? = null
        private set
    /** The WebSocket attempts in a row that never opened */
    var connectFailures = 0
        private set
    private var ticketRetried = false
    private var drops = 0
    private var everOpened = false

    fun start(): LiveStep { mode = LiveMode.CONNECTING; return LiveStep.OpenSocket(freshTicket = true) }

    fun opened() {
        mode = LiveMode.LIVE
        connectFailures = 0
        everOpened = true
        drops = 0
        ticketRetried = false   // a later 4401 (a ticket that expired while away) gets its one retry again
    }

    /** The upgrade was refused, the server could not be reached, or no ticket could be had. */
    fun connectFailed(): LiveStep {
        if (mode == LiveMode.ENDED) return LiveStep.End(end ?: LiveEnd.TAB_CLOSED)
        connectFailures++
        return if (connectFailures >= FALLBACK_AFTER) poll()
        else { mode = LiveMode.WAITING; LiveStep.OpenSocket(freshTicket = true, delayMs = RETRY_MS) }
    }

    /** The server said {"type":"ended"} before closing. */
    fun ended(reason: String): LiveStep = finish(if (reason == "no_tab") LiveEnd.NO_TAB else LiveEnd.TAB_CLOSED)

    /** The WebSocket closed (or failed after it had opened: [code] 1006). */
    fun closed(code: Int): LiveStep {
        if (mode == LiveMode.ENDED) return LiveStep.End(end ?: LiveEnd.TAB_CLOSED)
        val wasOpen = mode == LiveMode.LIVE
        return when (code) {
            4401 -> if (!ticketRetried) {
                ticketRetried = true
                mode = LiveMode.CONNECTING
                LiveStep.OpenSocket(freshTicket = true)
            } else poll()
            4404 -> finish(LiveEnd.NO_TAB)
            4408 -> { mode = LiveMode.CONNECTING; LiveStep.OpenSocket(freshTicket = true) }
            4429 -> { mode = LiveMode.WAITING; LiveStep.OpenSocket(freshTicket = true, delayMs = TOO_MANY_MS) }
            else -> if (!wasOpen && !everOpened) connectFailed() else {
                drops++
                mode = LiveMode.WAITING
                LiveStep.OpenSocket(freshTicket = true, delayMs = backoff(drops))
            }
        }
    }

    /** One frame request answered: [status] 200 / 204 go on, 404 is the end, anything else waits and tries again. */
    fun polled(status: Int, failures: Int): LiveStep = when {
        mode == LiveMode.ENDED -> LiveStep.End(end ?: LiveEnd.TAB_CLOSED)
        status == 200 || status == 204 -> { mode = LiveMode.POLLING; LiveStep.Poll() }
        status == 404 -> finish(LiveEnd.NO_TAB)
        else -> { mode = LiveMode.POLLING; LiveStep.Poll(delayMs = backoff(failures)) }
    }

    /** The server has no browser at all (404 on the ticket): nothing live to show. */
    fun noBrowser(): LiveStep = finish(LiveEnd.NO_BROWSER)

    private fun poll(): LiveStep { mode = LiveMode.POLLING; return LiveStep.Poll() }

    private fun finish(reason: LiveEnd): LiveStep { mode = LiveMode.ENDED; end = reason; return LiveStep.End(reason) }

    companion object {
        const val FALLBACK_AFTER = 2
        const val RETRY_MS = 1_000L
        const val TOO_MANY_MS = 30_000L
        const val PING_MS = 20_000L

        /** 1 s, 2 s, 4 s, 8 s, then 15 s. */
        fun backoff(n: Int): Long = if (n <= 0) 0 else minOf(15_000L, 1_000L shl (n - 1).coerceAtMost(4))
    }
}

/** What a live view shows: a browser tab, or the Android device (the key "android": a portrait screen, Back / Home / Recents). */
object LiveKinds {
    const val DEVICE_KEY = "android"
    fun isDevice(tabId: String): Boolean = tabId == DEVICE_KEY

    /** The device's navigation keys, left to right (the server's names). */
    val PHONE_KEYS = listOf("back", "home", "recents")

    /** A swipe lasts as long as the finger moved, between these. */
    fun swipeMs(dragMs: Long): Int = dragMs.coerceIn(120L, 800L).toInt()
}

/** When the live view takes its tab over by itself (it was opened to solve a human check). */
object LiveRules {
    /** Leaving the live page gives the tab back only for a hand-over hold; the owner's own button hold stays until they hand it back.
     *  An older server says no hold, so nothing is given back on leaving (its own 30 s grace still applies). */
    fun giveBackOnLeave(s: LiveStatus): Boolean = s.handoverHold

    /** Leaving the page while the owner holds the tab by their own press: the page says it stays theirs until they hand it back. */
    fun leaveNotice(s: LiveStatus): Boolean = s.youControl && !s.handoverHold

    /** Once only, when asked to, after the first picture came, while nobody holds the tab and no take-over is on its way. */
    fun autoTake(asked: Boolean, done: Boolean, hasFrame: Boolean, controlling: Boolean, someoneElse: Boolean, busy: Boolean): Boolean =
        asked && !done && hasFrame && !controlling && !someoneElse && !busy

    /**
     * Whether the live screen is kept out of screenshots and recordings (FLAG_SECURE): never (owner decision 2026-11-16). The live
     * browser view and the phone view can always be captured, whatever the picture shows. The secret entry sheets, the keychain and
     * the card screens keep their own guard (LocalSecureWindow there). The frame header's `secure` hint is still read but not used here.
     */
    fun guardScreen(): Boolean = false

    /** A picture is on screen: the JPEG frame or the VNC screen (which has no frame of its own). The take button and the auto take wait for it. */
    fun hasPicture(hasFrame: Boolean, vnc: VncState): Boolean = hasFrame || VncRules.showsVnc(vnc)

    /** The owner holds a tab of the work browser: the line under the paused banner says where the sign-in goes. */
    fun showWorkNote(s: LiveStatus): Boolean = s.youControl && s.inWorkBrowser
}
