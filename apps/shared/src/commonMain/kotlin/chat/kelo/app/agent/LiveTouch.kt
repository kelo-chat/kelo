package chat.kelo.app.agent

import androidx.compose.ui.geometry.Offset
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlin.math.sqrt

/*
 * Taking over a tab or the phone: what a finger does and what the server needs to know about it. No screen code here, so every rule
 * can be tested: the shared protocol of the live view (live_v 2), the gesture machine (tap at the DOWN point, long press, one-finger
 * drag, two fingers), the touch marks (where the finger is, where the tap landed, "did not reach the page"), the size report
 * (`view`) and the keyboard rule. The screen is LivePage.kt, the connection LiveClient.kt.
 */

/** The size of the live area on the owner's screen: physical pixels and the screen density (what `view` carries). */
data class ViewSize(val width: Int, val height: Int, val density: Float)

/** The server's answer to an input that was sent with "ack" (and the POST …/input answer, same shape). */
data class InputReply(
    /** Which input it answers (tap, scroll...); "" when the server did not say. */
    val input: String = "",
    /** false = the server did not do it; null = it did not say (an older one). */
    val ok: Boolean? = null,
    val x: Float? = null,
    val y: Float? = null,
    /** The page (or phone) did not take the input: it was dropped. */
    val busy: Boolean = false,
    /** "frame" = mapped with the frame the owner saw, "latest" = that frame was not known any more; "" = an older server. */
    val mappedWith: String = "",
    /** Dropped: the tap was aimed at a picture from before the agent's stopped step's last move (tap again on the new picture). */
    val stale: Boolean = false,
) {
    /** The input did not reach the page / phone: dropped as busy, stale, or refused. */
    val notReached: Boolean get() = busy || stale || ok == false

    companion object {
        fun of(o: JsonObject): InputReply {
            fun s(k: String) = (o[k] as? JsonPrimitive)?.contentOrNull.orEmpty()
            fun f(k: String) = (o[k] as? JsonPrimitive)?.doubleOrNull?.toFloat()
            return InputReply(s("input"), (o["ok"] as? JsonPrimitive)?.booleanOrNull, f("x"), f("y"), (o["busy"] as? JsonPrimitive)?.booleanOrNull == true, s("mapped_with"),
                stale = (o["stale"] as? JsonPrimitive)?.booleanOrNull == true)
        }
    }
}

object LiveProtocol {
    /** The version from which the server takes `view` and `frame_seq`, and says `editing` and `mapped_with`. */
    const val V2 = 2

    /** The server speaks version 2 (a newest frame that says so). Older servers keep working without the new messages. */
    fun v2(header: FrameHeader?): Boolean = header != null && header.liveV >= V2

    /** The `frame_seq` of an input: the seq of the frame the owner was looking at; null (left out) for an older server. */
    fun seqFor(header: FrameHeader?): Long? = header?.takeIf { v2(it) && it.seq > 0 }?.seq
}

/**
 * When to tell the server how big the live area is. It is sent once controlling starts, again when the size changes (rotation, the
 * soft keyboard opening or closing) and again on a new connection, never to a server below live_v 2 and never over a connection
 * that is not open; a size already sent is not sent twice. Not controlling: nothing is wanted, and the next take-over sends it again.
 */
class ViewReporter {
    private var wanted: ViewSize? = null
    private var sent: ViewSize? = null

    /** The live area as it is now, or null while the owner is not controlling. */
    fun want(v: ViewSize?) {
        wanted = v?.takeIf { it.width > 0 && it.height > 0 && it.density > 0f }
        if (wanted == null) sent = null
    }

    /** What to send now (and counts as sent), or null. */
    fun next(liveV: Int, socketOpen: Boolean): ViewSize? {
        val w = wanted ?: return null
        if (liveV < LiveProtocol.V2 || !socketOpen || w == sent) return null
        sent = w
        return w
    }

    /** The connection closed: the server forgot the size. */
    fun connectionLost() { sent = null }
}

/** The soft keyboard comes up when the page's text field takes focus, once per focus, not at every frame. */
object TouchRules {
    /** A finger that stays within this (dp) is still; the same on iPhone (10 pt). */
    const val SLOP_DP = 10f
}

object EditingRule {
    fun raise(before: Boolean, now: Boolean): Boolean = now && !before

    /**
     * Whether a field that took focus on the page moves the focus to the type line. On the desktop with the VNC screen showing, the keys go
     * to the screen itself (the owner clicks into the page), so the focus stays there: the type line is only for composed text (see the hint).
     */
    fun focusTypeLine(before: Boolean, now: Boolean, keysGoToScreen: Boolean): Boolean = raise(before, now) && !keysGoToScreen
}

/** What the touch machine asks for. Points are in view pixels. */
sealed interface TouchAction {
    /** A tap: at the point where the finger went DOWN (not where it lifted). */
    data class Tap(val at: Offset) : TouchAction
    data class LongPress(val at: Offset) : TouchAction
    /** One finger moved past the slop: the page scrolls by this much (the first one includes the slop distance). */
    data class Drag(val dx: Float, val dy: Float, val at: Offset) : TouchAction
    /** The finger that dragged lifted: the swipe of the phone, from where it went down to where it came up. */
    data class DragEnd(val from: Offset, val to: Offset, val ms: Long) : TouchAction
}

/**
 * One finger's life from down to up, as a machine, so the rules can be tested without a screen:
 * - lifts within [slop] of where it went down, before [longPressMs]: a tap at the DOWN point;
 * - held [longPressMs] without moving past the slop: a long press at the DOWN point (once; the lift then does nothing) - unless
 *   [longPress] is off (the phone has none), then it is still a tap on lift;
 * - moves past the slop: a drag (scroll / swipe), never a tap, and a long press no longer comes;
 * - a second finger: this is a pinch or a two-finger pan (the screen does that): nothing else comes from this gesture, whatever
 *   the fingers do, until they have all lifted ([reset]).
 */
class TouchMachine(private val slop: Float, private val longPress: Boolean = true, private val longPressMs: Long = LONG_PRESS_MS) {
    private enum class Phase { IDLE, DOWN, DRAGGING, LONG, MULTI }

    private var phase = Phase.IDLE
    private var down = Offset.Zero
    private var downMs = 0L
    private var last = Offset.Zero

    /** True while the machine follows one finger (not a second one, not nothing). */
    val oneFinger: Boolean get() = phase == Phase.DOWN || phase == Phase.DRAGGING || phase == Phase.LONG
    val multi: Boolean get() = phase == Phase.MULTI

    fun down(at: Offset, nowMs: Long) { phase = Phase.DOWN; down = at; last = at; downMs = nowMs }

    /** Milliseconds until a long press is due; null when none can come. */
    fun longPressIn(nowMs: Long): Long? = if (phase == Phase.DOWN && longPress) (downMs + longPressMs - nowMs).coerceAtLeast(0) else null

    fun tick(nowMs: Long): TouchAction? {
        if (phase != Phase.DOWN || !longPress || nowMs - downMs < longPressMs) return null
        phase = Phase.LONG
        return TouchAction.LongPress(down)
    }

    fun move(at: Offset, nowMs: Long): TouchAction? {
        when (phase) {
            Phase.DOWN -> {
                tick(nowMs)?.let { return it }
                if (distance(at, down) <= slop) return null
                phase = Phase.DRAGGING
                last = at
                return TouchAction.Drag(at.x - down.x, at.y - down.y, at)
            }
            Phase.DRAGGING -> {
                val d = TouchAction.Drag(at.x - last.x, at.y - last.y, at)
                last = at
                return d
            }
            else -> return null
        }
    }

    fun up(at: Offset, nowMs: Long): TouchAction? {
        val p = phase
        phase = Phase.IDLE
        return when (p) {
            Phase.DOWN -> if (longPress && nowMs - downMs >= longPressMs && distance(at, down) <= slop) TouchAction.LongPress(down) else TouchAction.Tap(down)
            Phase.DRAGGING -> TouchAction.DragEnd(down, at, nowMs - downMs)
            else -> null
        }
    }

    /** A second finger came down. */
    fun second() { phase = Phase.MULTI }

    /** All fingers are up (or the gesture was cancelled). */
    fun reset() { phase = Phase.IDLE }

    private fun distance(a: Offset, b: Offset): Float { val dx = a.x - b.x; val dy = a.y - b.y; return sqrt(dx * dx + dy * dy) }

    companion object {
        const val LONG_PRESS_MS = 500L
    }
}

enum class MarkKind {
    /** The ring and dot where the tap was mapped to on the picture. */
    LANDED,
    /** The tap did not reach the page: outside the picture, or the page/phone was busy. */
    MISSED,
}

enum class MissReason { OUTSIDE, BUSY }

/** One mark of the owner's own touch. A new [id] restarts its fade. */
data class TouchMark(val id: Long, val kind: MarkKind, val at: Offset, val reason: MissReason? = null)

object TouchMarkRules {
    /** How long the mark of a lift stays and fades. */
    const val FADE_MS = 350

    /**
     * What a tap or long press at [viewAt] becomes: the frame point to send (null in the letterbox bars) and the mark to show - the
     * mapped point drawn back into the view by the same transform the hit test used, or a "missed" mark at the finger.
     */
    fun forTouch(id: Long, viewAt: Offset, viewW: Float, viewH: Float, frameW: Int, frameH: Int, zoom: Float, panX: Float, panY: Float): Pair<FramePoint?, TouchMark> {
        val p = LiveGeometry.toFrame(viewAt.x, viewAt.y, viewW, viewH, frameW, frameH, zoom, panX, panY)
        if (p == null) return null to TouchMark(id, MarkKind.MISSED, viewAt, MissReason.OUTSIDE)
        // the middle of the frame pixel that was sent
        val back = LiveGeometry.frameToView(p.x + 0.5f, p.y + 0.5f, viewW, viewH, frameW, frameH, zoom, panX, panY) ?: viewAt
        return p to TouchMark(id, MarkKind.LANDED, back)
    }

    /** The server said it did not take the input: the mark of that tap turns into a missed one (a new id: it fades again). */
    fun busy(mark: TouchMark, id: Long): TouchMark = TouchMark(id, MarkKind.MISSED, mark.at, MissReason.BUSY)
}
