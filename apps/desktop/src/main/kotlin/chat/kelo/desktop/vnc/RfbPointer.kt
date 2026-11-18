package chat.kelo.desktop.vnc

import chat.kelo.app.agent.FramePoint
import chat.kelo.app.agent.LiveGeometry

/**
 * The owner's mouse on the page. Positions are frame pixels (see [frameAt]); the buttons are an RFB mask: left 1, middle 2, right 4.
 * Wheel notches are button 4/5 (up/down) and 6/7 (left/right), each a press and a release, from the fractional trackpad deltas added
 * up until a whole notch. Moves are sent at most once per [MOVE_INTERVAL_MS], the newest position wins; presses and releases go out at
 * once. Nothing is sent while the owner does not hold the tab.
 */
class RfbPointer(private val send: (ByteArray) -> Unit) {
    private var holding = false
    private var mask = 0
    private var at: FramePoint? = null
    private var lastMoveAt: Long? = null          // null until the first move went out
    private var pendingMove: FramePoint? = null
    private var wheelUp = 0f
    private var wheelRight = 0f

    fun setHolding(on: Boolean) {
        if (!on && holding) releaseAll()
        holding = on
    }

    /** The pointer moved over the picture at [p] (null outside it: nothing is sent). */
    fun move(p: FramePoint?, nowMs: Long) {
        if (!holding || p == null) return
        at = p
        if (due(nowMs)) {
            lastMoveAt = nowMs
            pendingMove = null
            send(RfbClientMessage.pointer(mask, p.x, p.y))
        } else {
            pendingMove = p
        }
    }

    /** The move held back for the rate limit goes out once the interval has passed. */
    fun flush(nowMs: Long) {
        val p = pendingMove ?: return
        if (due(nowMs)) {
            lastMoveAt = nowMs
            pendingMove = null
            send(RfbClientMessage.pointer(mask, p.x, p.y))
        }
    }

    /** A button went down or up at [p] (outside the picture, nothing is sent). [button] is the mask bit: [LEFT], [MIDDLE] or [RIGHT]. */
    fun button(button: Int, down: Boolean, p: FramePoint?) {
        if (!holding || p == null) return
        pendingMove?.let { send(RfbClientMessage.pointer(mask, it.x, it.y)); pendingMove = null }
        mask = if (down) mask or button else mask and button.inv()
        at = p
        send(RfbClientMessage.pointer(mask, p.x, p.y))
    }

    /** Wheel movement in notches: [up] positive scrolls up, [right] positive scrolls right. Whole notches are sent, the rest is kept. */
    fun wheel(up: Float, right: Float, p: FramePoint?) {
        if (!holding || p == null) return
        wheelUp += up
        wheelRight += right
        while (wheelUp >= 1f) { click(WHEEL_UP, p); wheelUp -= 1f }
        while (wheelUp <= -1f) { click(WHEEL_DOWN, p); wheelUp += 1f }
        while (wheelRight >= 1f) { click(WHEEL_RIGHT, p); wheelRight -= 1f }
        while (wheelRight <= -1f) { click(WHEEL_LEFT, p); wheelRight += 1f }
    }

    /** The buttons go up (holding ends, or the window loses focus). */
    fun releaseAll() {
        if (mask == 0) return
        mask = 0
        val p = at ?: FramePoint(0, 0)
        send(RfbClientMessage.pointer(0, p.x, p.y))
    }

    private fun due(nowMs: Long): Boolean = lastMoveAt?.let { nowMs - it >= MOVE_INTERVAL_MS } ?: true

    private fun click(button: Int, p: FramePoint) {
        send(RfbClientMessage.pointer(mask or button, p.x, p.y))
        send(RfbClientMessage.pointer(mask, p.x, p.y))
    }

    companion object {
        const val LEFT = 1
        const val MIDDLE = 2
        const val RIGHT = 4
        const val WHEEL_UP = 8
        const val WHEEL_DOWN = 16
        const val WHEEL_LEFT = 32
        const val WHEEL_RIGHT = 64
        const val MOVE_INTERVAL_MS = 16L

        /** The frame pixel under a view position, with the zoom and pan of the picture (the shared rule); null outside the picture. */
        fun frameAt(x: Float, y: Float, viewW: Float, viewH: Float, frameW: Int, frameH: Int, zoom: Float, panX: Float, panY: Float): FramePoint? =
            LiveGeometry.toFrame(x, y, viewW, viewH, frameW, frameH, zoom, panX, panY)
    }
}
