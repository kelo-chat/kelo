package chat.kelo.desktop.vnc

import chat.kelo.app.agent.FramePoint
import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertNull

class RfbPointerTest {
    private val sent = ArrayList<ByteArray>()
    private val pointer = RfbPointer { sent += it }

    private fun masks(): List<Int> = sent.map { it[1].toInt() and 0xff }

    @Test fun nothingIsSentWhileNotHolding() {
        pointer.move(FramePoint(1, 1), 0)
        pointer.button(RfbPointer.LEFT, true, FramePoint(1, 1))
        pointer.wheel(2f, 0f, FramePoint(1, 1))
        assertEquals(0, sent.size)
    }

    @Test fun aPressOutsideThePictureSendsNothing() {
        pointer.setHolding(true)
        pointer.button(RfbPointer.LEFT, true, null)
        assertEquals(0, sent.size)
    }

    @Test fun aClickAtTheRightPlaceWithTheRightButton() {
        pointer.setHolding(true)
        pointer.button(RfbPointer.RIGHT, true, FramePoint(10, 20))
        pointer.button(RfbPointer.RIGHT, false, FramePoint(10, 20))
        assertContentEquals(RfbClientMessage.pointer(RfbPointer.RIGHT, 10, 20), sent[0])
        assertContentEquals(RfbClientMessage.pointer(0, 10, 20), sent[1])
    }

    @Test fun movesAreRateLimitedAndTheNewestWins() {
        pointer.setHolding(true)
        pointer.move(FramePoint(1, 1), 0)                  // sent at once
        pointer.move(FramePoint(2, 2), 5)                  // held back
        pointer.move(FramePoint(3, 3), 10)                 // held back, replaces the one before
        pointer.flush(12)                                   // the interval has not passed
        assertEquals(1, sent.size)
        pointer.flush(16)
        assertEquals(2, sent.size)
        assertContentEquals(RfbClientMessage.pointer(0, 3, 3), sent[1])
    }

    @Test fun twoAndAHalfNotchesGiveTwoClicksAndKeepHalf() {
        pointer.setHolding(true)
        pointer.wheel(up = 2.5f, right = 0f, p = FramePoint(5, 5))
        assertEquals(listOf(RfbPointer.WHEEL_UP, 0, RfbPointer.WHEEL_UP, 0), masks())
        pointer.wheel(up = 0.5f, right = 0f, p = FramePoint(5, 5))
        assertEquals(6, sent.size)
    }

    @Test fun horizontalWheelUsesButtonsSixAndSeven() {
        pointer.setHolding(true)
        pointer.wheel(up = 0f, right = -1f, p = FramePoint(0, 0))
        assertEquals(listOf(RfbPointer.WHEEL_LEFT, 0), masks())
    }

    @Test fun releaseAllLetsTheButtonsGo() {
        pointer.setHolding(true)
        pointer.button(RfbPointer.LEFT, true, FramePoint(1, 1))
        sent.clear()
        pointer.setHolding(false)
        assertEquals(listOf(0), masks())
        pointer.releaseAll()
        assertEquals(1, sent.size)
    }

    @Test fun theZoomedPictureMapsBackToFramePixels() {
        // a 100 x 50 frame in a 200 x 100 view is drawn at 2x: zoomed twice around the middle, the middle is the middle of the frame
        assertEquals(FramePoint(50, 25), RfbPointer.frameAt(100f, 50f, 200f, 100f, 100, 50, zoom = 2f, panX = 0f, panY = 0f))
        assertEquals(FramePoint(25, 12), RfbPointer.frameAt(0f, 0f, 200f, 100f, 100, 50, zoom = 2f, panX = 0f, panY = 0f))
        assertNull(RfbPointer.frameAt(-5f, 50f, 200f, 100f, 100, 50, zoom = 1f, panX = 0f, panY = 0f))
    }
}
