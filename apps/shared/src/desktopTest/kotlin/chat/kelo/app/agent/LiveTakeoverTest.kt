package chat.kelo.app.agent

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.toAwtImage
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import org.jetbrains.compose.resources.StringResource
import java.io.File
import javax.imageio.ImageIO
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.math.abs

/** Taking over a tab or the phone: the shared protocol (live_v 2), the gestures, the touch marks, the size report, the keyboard rule. */
class LiveTakeoverGeometryTest {
    @Test fun `the pan stays inside the picture, not the whole view`() {
        // an 800 x 400 frame in a 400 x 800 view is a 400 x 200 picture in the middle
        val (x, y) = LiveGeometry.clampPan(900f, 900f, 2f, 400f, 800f, 800, 400)
        assertEquals(200f, x)          // (400 * 2 - 400) / 2
        assertEquals(0f, y)            // 200 * 2 = 400 < 800: it stays centred, no panning into the blank bars
        // without the frame size the whole view stands in (as before)
        assertEquals(200f to 400f, LiveGeometry.clampPan(900f, 900f, 2f, 400f, 800f))
        assertEquals(0f to 0f, LiveGeometry.clampPan(50f, 50f, 1f, 400f, 800f, 800, 400))
    }

    @Test fun `frameToView is the way back of toFrame`() {
        for (z in listOf(1f, 2f, 3.5f)) for ((px, py) in listOf(0f to 0f, 40f to -25f)) {
            val (cx, cy) = LiveGeometry.clampPan(px, py, z, 400f, 800f, 720, 1280)
            val p = LiveGeometry.toFrame(210f, 330f, 400f, 800f, 720, 1280, z, cx, cy)!!
            val back = LiveGeometry.frameToView(p.x + 0.5f, p.y + 0.5f, 400f, 800f, 720, 1280, z, cx, cy)!!
            assertTrue(abs(back.x - 210f) <= 1f + z / 2f && abs(back.y - 330f) <= 1f + z / 2f, "z=$z back=$back")
        }
    }

    @Test fun `the agent's pointer and the owner's touch use the same transform`() {
        val a = LiveGeometry.pointerToView(300f, 200f, 400f, 800f, 720, 1280, 360, 640, 2f, 10f, -20f)!!
        val f = LiveGeometry.cssToFrame(300f, 200f, 720, 1280, 360, 640)
        assertEquals(LiveGeometry.frameToView(f.x, f.y, 400f, 800f, 720, 1280, 2f, 10f, -20f), a)
    }

    @Test fun `the iPhone's vectors give the same answers`() {
        // 720x450 picture in a 360x800 view
        assertEquals(0f to 0f, LiveGeometry.clampPan(500f, 500f, 1f, 360f, 800f, 720, 450))
        assertEquals(180f to 0f, LiveGeometry.clampPan(500f, 500f, 2f, 360f, 800f, 720, 450))
        assertEquals(200f to -400f, LiveGeometry.clampPan(900f, -900f, 2f, 400f, 800f, 400, 800))
        assertEquals(0f to 0f, LiveGeometry.clampPan(5f, 5f, 2f, 0f, 0f, 1, 1))
        val v = LiveGeometry.frameToView(300.5f, 200.5f, 360f, 800f, 720, 450, 2f, 30f, -20f)!!
        assertEquals(FramePoint(300, 200), LiveGeometry.toFrame(v.x, v.y, 360f, 800f, 720, 450, 2f, 30f, -20f))
        val c = LiveGeometry.frameToView(499.5f, 499.5f, 1000f, 1000f, 1000, 1000)!!
        assertEquals(499.5f, c.x); assertEquals(499.5f, c.y)
        assertNull(LiveGeometry.frameToView(1f, 1f, 0f, 0f, 1, 1))
    }

    @Test fun `a pinch keeps the point under the fingers where it is`() {
        val (vw, vh) = 400f to 800f
        val c = Offset(300f, 200f)
        val before = LiveGeometry.toFrame(c.x, c.y, vw, vh, 720, 1280, 1f, 0f, 0f)!!
        val (px, py) = LiveGeometry.panForZoomAround(c.x, c.y, 1f, 2.5f, 0f, 0f, vw, vh)
        val after = LiveGeometry.toFrame(c.x, c.y, vw, vh, 720, 1280, 2.5f, px, py)!!
        assertTrue(abs(before.x - after.x) <= 1 && abs(before.y - after.y) <= 1, "$before $after")
    }
}

class LiveTakeoverProtocolTest {
    private fun header(json: String): FrameHeader = (LiveMessages.parse(json) as LiveIn.Frame).header

    @Test fun `a version 2 frame says so and whether a field has focus, an older one says nothing`() {
        val v2 = header("""{"type":"frame","seq":7,"w":720,"h":1280,"live_v":2,"editing":true,"something_new":1}""")
        assertEquals(2, v2.liveV); assertTrue(v2.editing); assertTrue(LiveProtocol.v2(v2))
        val old = header("""{"type":"frame","seq":7,"w":720,"h":1280}""")
        assertEquals(0, old.liveV); assertFalse(old.editing); assertFalse(LiveProtocol.v2(old))
    }

    @Test fun `frame_seq is the seq of the frame the owner saw, only for a server that takes it`() {
        assertEquals(7L, LiveProtocol.seqFor(FrameHeader(seq = 7, liveV = 2)))
        assertNull(LiveProtocol.seqFor(FrameHeader(seq = 7, liveV = 0)))
        assertNull(LiveProtocol.seqFor(FrameHeader(seq = 0, liveV = 2)))
        assertNull(LiveProtocol.seqFor(null))
    }

    @Test fun `every input carries frame_seq, and an ack for the reply, when the server is version 2`() {
        val p = FramePoint(10, 20)
        val msgs = listOf(
            LiveMessages.tap(p, 720, 1280, 9), LiveMessages.longPress(p, 720, 1280, 9),
            LiveMessages.scroll(FramePoint(0, 50), p, 720, 1280, 9), LiveMessages.swipe(p, FramePoint(30, 40), 720, 1280, 200, 9),
        )
        for (m in msgs) assertEquals(9, m["frame_seq"]!!.jsonPrimitive.int, m["type"].toString())
        assertEquals(true, msgs[0]["ack"]!!.jsonPrimitive.booleanOrNull); assertEquals(true, msgs[1]["ack"]!!.jsonPrimitive.booleanOrNull)
        assertEquals(5, LiveMessages.key("Enter", 5)["frame_seq"]!!.jsonPrimitive.int); assertEquals(5, LiveMessages.text("a", 5)["frame_seq"]!!.jsonPrimitive.int)
        // an older server: no frame_seq (the ack is harmless, the server has long known it)
        val old = listOf(
            LiveMessages.tap(p, 720, 1280), LiveMessages.longPress(p, 720, 1280),
            LiveMessages.scroll(FramePoint(0, 50), p, 720, 1280), LiveMessages.swipe(p, FramePoint(30, 40), 720, 1280, 200),
        )
        for (m in old) assertNull(m["frame_seq"])
        assertNull(LiveMessages.key("Enter")["frame_seq"])
    }

    @Test fun `view carries the physical size and the density`() {
        val m = LiveMessages.view(ViewSize(1080, 1900, 2.75f))
        assertEquals("view", m["type"]!!.jsonPrimitive.content)
        assertEquals(1080, m["width"]!!.jsonPrimitive.int); assertEquals(1900, m["height"]!!.jsonPrimitive.int)
        assertEquals(2.75f, m["density"]!!.jsonPrimitive.content.toFloat())
    }

    @Test fun `the answer to an input says where it landed and which frame it was mapped with`() {
        val r = (LiveMessages.parse("""{"type":"input_ok","input":"tap","x":120.5,"y":80,"busy":false,"mapped_with":"frame","extra":[1]}""") as LiveIn.InputOk).reply
        assertEquals("tap", r.input); assertEquals(120.5f, r.x); assertEquals(80f, r.y); assertFalse(r.busy); assertEquals("frame", r.mappedWith)
        val late = (LiveMessages.parse("""{"type":"input_ok","input":"tap","busy":true,"mapped_with":"latest"}""") as LiveIn.InputOk).reply
        assertTrue(late.busy); assertEquals("latest", late.mappedWith); assertNull(late.x)
        // the POST answer has the same shape
        assertEquals("latest", InputReply.of(JsonObject(mapOf("mapped_with" to JsonPrimitive("latest")))).mappedWith)
    }

    @Test fun `the size is sent when controlling starts, again when it changes, never twice, never to an older server`() {
        val r = ViewReporter()
        val a = ViewSize(1080, 1700, 2.75f); val kb = ViewSize(1080, 900, 2.75f)
        assertNull(r.next(2, true))                      // not controlling: nothing wanted
        r.want(a)
        assertNull(r.next(0, true)); assertNull(r.next(1, true))   // an older server
        assertNull(r.next(2, false))                     // no open connection
        assertEquals(a, r.next(2, true)); assertNull(r.next(2, true))   // once
        r.want(kb); assertEquals(kb, r.next(2, true))    // the keyboard opened
        r.want(a); assertEquals(a, r.next(2, true))      // and closed
        r.connectionLost(); assertEquals(a, r.next(2, true))   // a new connection: the server forgot it
        r.want(null); assertNull(r.next(2, true))        // handed back
        r.want(a); assertEquals(a, r.next(2, true))      // the next take-over sends it again
        r.want(ViewSize(0, 100, 1f)); assertNull(r.next(2, true))   // nonsense is not sent
    }

    @Test fun `ok false counts as not reached, like busy`() {
        val r = (LiveMessages.parse("""{"type":"input_ok","ok":false,"busy":false}""") as LiveIn.InputOk).reply
        assertTrue(r.notReached); assertEquals(false, r.ok)
        assertTrue(InputReply(busy = true).notReached); assertFalse(InputReply(ok = true, mappedWith = "frame").notReached); assertFalse(InputReply().notReached)
    }

    @Test fun `the slop is 10 and the long press 500 ms, as on the iPhone`() {
        assertEquals(10f, TouchRules.SLOP_DP); assertEquals(500L, TouchMachine.LONG_PRESS_MS)
    }

    @Test fun `the keyboard comes up once when a field takes focus`() {
        assertTrue(EditingRule.raise(false, true))
        assertFalse(EditingRule.raise(true, true)); assertFalse(EditingRule.raise(false, false)); assertFalse(EditingRule.raise(true, false))
    }
}

class TouchMachineTest {
    private fun m(longPress: Boolean = true) = TouchMachine(slop = 8f, longPress = longPress)

    @Test fun `a tap goes to the DOWN point even when the finger drifts a little before it lifts`() {
        val t = m()
        t.down(Offset(100f, 100f), 0)
        assertNull(t.move(Offset(104f, 103f), 30))
        assertEquals(TouchAction.Tap(Offset(100f, 100f)), t.up(Offset(106f, 105f), 80))
    }

    @Test fun `a long press fires once, at the down point, and the lift then sends nothing`() {
        val t = m()
        t.down(Offset(50f, 60f), 1000)
        assertEquals(500L, t.longPressIn(1000)); assertEquals(100L, t.longPressIn(1400))
        assertNull(t.tick(1499))
        assertEquals(TouchAction.LongPress(Offset(50f, 60f)), t.tick(1500))
        assertNull(t.tick(1600)); assertNull(t.longPressIn(1600))
        assertNull(t.up(Offset(50f, 60f), 1700))
    }

    @Test fun `a finger that moves past the slop drags, never taps, never long presses`() {
        val t = m()
        t.down(Offset(0f, 0f), 0)
        assertEquals(TouchAction.Drag(0f, 20f, Offset(0f, 20f)), t.move(Offset(0f, 20f), 40))
        assertEquals(TouchAction.Drag(0f, 5f, Offset(0f, 25f)), t.move(Offset(0f, 25f), 60))
        assertNull(t.tick(900))
        assertEquals(TouchAction.DragEnd(Offset(0f, 0f), Offset(0f, 25f), 700), t.up(Offset(0f, 25f), 700))
    }

    @Test fun `a second finger ends single-finger input for the whole gesture`() {
        val t = m()
        t.down(Offset(10f, 10f), 0)
        t.second()
        assertTrue(t.multi)
        assertNull(t.move(Offset(200f, 10f), 20)); assertNull(t.tick(900)); assertNull(t.up(Offset(10f, 10f), 950))
        t.reset(); assertFalse(t.multi)
        t.down(Offset(1f, 1f), 2000)   // the next gesture is a fresh one
        assertEquals(TouchAction.Tap(Offset(1f, 1f)), t.up(Offset(1f, 1f), 2050))
    }

    @Test fun `the phone has no long press, a long hold is a tap on lift`() {
        val t = m(longPress = false)
        t.down(Offset(5f, 5f), 0)
        assertNull(t.longPressIn(0)); assertNull(t.tick(900))
        assertEquals(TouchAction.Tap(Offset(5f, 5f)), t.up(Offset(5f, 5f), 900))
    }
}

class TouchMarkRulesTest {
    @Test fun `a tap on the picture lands within a pixel of the finger and sends that frame point`() {
        val (p, mark) = TouchMarkRules.forTouch(1, Offset(210f, 330f), 400f, 800f, 720, 1280, 2f, 0f, 0f)
        assertNotNull(p)
        assertEquals(MarkKind.LANDED, mark.kind)
        assertTrue(abs(mark.at.x - 210f) <= 2f && abs(mark.at.y - 330f) <= 2f, "${mark.at}")
    }

    @Test fun `a tap in the letterbox bar sends nothing and is shown as missed at the finger`() {
        // a wide frame in a tall view: the bars are above and below the picture
        val (p, mark) = TouchMarkRules.forTouch(2, Offset(200f, 20f), 400f, 800f, 800, 400, 1f, 0f, 0f)
        assertNull(p)
        assertEquals(MarkKind.MISSED, mark.kind); assertEquals(MissReason.OUTSIDE, mark.reason); assertEquals(Offset(200f, 20f), mark.at)
    }

    @Test fun `a busy answer turns the mark into a missed one that fades again`() {
        val (_, landed) = TouchMarkRules.forTouch(3, Offset(210f, 330f), 400f, 800f, 720, 1280, 1f, 0f, 0f)
        val m = TouchMarkRules.busy(landed, 4)
        assertEquals(MarkKind.MISSED, m.kind); assertEquals(MissReason.BUSY, m.reason); assertEquals(landed.at, m.at); assertNotEquals(landed.id, m.id)
        assertTrue(TouchMarkLook.fadeMs(m) > TouchMarkLook.fadeMs(landed))
    }
}

/** The view model with a source whose frame, replies and sent events the test controls. */
class LiveTakeoverViewModelTest {
    private class Source(frame: LiveFrame?) : LiveSource {
        val _frame = MutableStateFlow(frame)
        override val frame: StateFlow<LiveFrame?> = _frame
        override val state: StateFlow<LiveState> = MutableStateFlow(LiveState())
        override val pointer: StateFlow<LivePointer?> = MutableStateFlow(null)
        override val notices: SharedFlow<StringResource> = MutableSharedFlow()
        override val replies = MutableSharedFlow<InputReply>(extraBufferCapacity = 4)
        val sent = mutableListOf<JsonObject>()
        val views = mutableListOf<ViewSize?>()
        override fun start() {}
        override fun stop(giveBack: Boolean) {}
        override fun close() {}
        override suspend fun control(take: Boolean, handover: Boolean): LiveClient.ControlResult = LiveClient.ControlResult.Ok
        override fun send(event: JsonObject) { sent += event }
        override fun type(text: String) {}
        override fun setView(v: ViewSize?) { views += v }
    }

    private fun frame(liveV: Int, seq: Long = 12, w: Int = 720, h: Int = 1280) = LiveFrame(ImageBitmap(w, h), FrameHeader(seq = seq, w = w, h = h, cssW = 360, cssH = 640, liveV = liveV))

    private fun waitFor(what: String, ok: () -> Boolean) {
        val end = System.currentTimeMillis() + 3000
        while (!ok() && System.currentTimeMillis() < end) Thread.sleep(10)
        assertTrue(ok(), what)
    }

    @Test fun `a tap is sent for the frame on screen with its seq, and shows where it landed`() {
        val s = Source(frame(2, seq = 12)); val vm = LiveViewModel { s }
        assertTrue(vm.tap(Offset(200f, 400f), 400f, 800f))
        val m = s.sent.single()
        assertEquals("tap", m["type"]!!.jsonPrimitive.content); assertEquals(12, m["frame_seq"]!!.jsonPrimitive.int); assertEquals(true, m["ack"]!!.jsonPrimitive.booleanOrNull)
        assertEquals(360, m["x"]!!.jsonPrimitive.int); assertEquals(640, m["y"]!!.jsonPrimitive.int)
        assertEquals(MarkKind.LANDED, vm.touch.value.mark!!.kind)
        // the next frame arrives: a tap still on the old picture would carry the new seq, one on the new picture the new one
        s._frame.value = frame(2, seq = 13)
        vm.tap(Offset(200f, 400f), 400f, 800f, longPress = true)
        assertEquals("long_press", s.sent.last()["type"]!!.jsonPrimitive.content); assertEquals(13, s.sent.last()["frame_seq"]!!.jsonPrimitive.int)
    }

    @Test fun `an older server gets the tap as before, with no frame_seq`() {
        val s = Source(frame(0)); val vm = LiveViewModel { s }
        vm.tap(Offset(200f, 400f), 400f, 800f)
        assertNull(s.sent.single()["frame_seq"])
    }

    @Test fun `a tap in the bars sends nothing and says it did not reach the page`() {
        val s = Source(frame(2, w = 800, h = 400)); val vm = LiveViewModel { s }
        val got = mutableListOf<StringResource>()
        val t = Thread { kotlinx.coroutines.runBlocking { vm.notices.collect { got += it } } }.also { it.isDaemon = true; it.start() }
        Thread.sleep(200)
        assertFalse(vm.tap(Offset(200f, 20f), 400f, 800f))
        assertTrue(s.sent.isEmpty())
        assertEquals(MarkKind.MISSED, vm.touch.value.mark!!.kind)
        waitFor("a note") { got.isNotEmpty() }
        t.interrupt()
    }

    @Test fun `the server dropping a tap turns its mark into a missed one`() {
        val s = Source(frame(2)); val vm = LiveViewModel { s }
        vm.tap(Offset(200f, 400f), 400f, 800f)
        val landed = vm.touch.value.mark!!
        Thread.sleep(200)   // the view model's own collector is running
        s.replies.tryEmit(InputReply(input = "tap", busy = true))
        waitFor("a missed mark") { vm.touch.value.mark?.kind == MarkKind.MISSED }
        assertEquals(MissReason.BUSY, vm.touch.value.mark!!.reason); assertEquals(landed.at, vm.touch.value.mark!!.at)
    }

    @Test fun `a scroll and a swipe carry the frame seq too`() {
        val s = Source(frame(2, seq = 21)); val vm = LiveViewModel { s }
        vm.scroll(0f, -100f, 200f, 400f, 400f, 800f)
        assertEquals(21, s.sent.last()["frame_seq"]!!.jsonPrimitive.int); assertEquals("scroll", s.sent.last()["type"]!!.jsonPrimitive.content)
        vm.swipe(Offset(200f, 600f), Offset(200f, 200f), 300, 400f, 800f)
        assertEquals(21, s.sent.last()["frame_seq"]!!.jsonPrimitive.int); assertEquals("swipe", s.sent.last()["type"]!!.jsonPrimitive.content)
    }

    @Test fun `a pinch zooms about the fingers and the pan stays on the picture`() {
        val s = Source(frame(2, w = 800, h = 400)); val vm = LiveViewModel { s }
        vm.transform(3f, Offset.Zero, 400f, 800f, centroid = Offset(300f, 400f))
        assertEquals(3f, vm.zoom.value)
        // the 400 x 200 picture at 3x is 1200 x 600: it may pan 400 sideways but not up or down (600 < 800)
        vm.transform(1f, Offset(5000f, 5000f), 400f, 800f)
        assertEquals(400f, vm.pan.value.x); assertEquals(0f, vm.pan.value.y)
    }

    @Test fun `the live area's size goes to the source`() {
        val s = Source(frame(2)); val vm = LiveViewModel { s }
        vm.reportView(ViewSize(1080, 1700, 2.75f)); vm.reportView(null)
        assertEquals(listOf<ViewSize?>(ViewSize(1080, 1700, 2.75f), null), s.views)
    }
}

/** A picture of the three touch marks (kept under build/screens/takeover-touch, or KELO_SHOTS_DIR) and a check of what is drawn. */
@OptIn(ExperimentalTestApi::class)
class TouchMarksShotTest {
    private val accent = Color(0xFF2962FF)
    private val ground = Color(0xFF404040)

    private fun rgb(img: java.awt.image.BufferedImage, x: Int, y: Int) = Color(img.getRGB(x, y))

    @Test fun `the finger ring, the landed ring and dot and the missed cross are drawn, and a faded mark is gone`() = runComposeUiTest {
        val at = Offset(60f, 60f)
        setContent {
            Row(Modifier.testTag("shot")) {
                Box(Modifier.size(120.dp).background(ground)) { TouchMarksLayer(down = at, mark = null, progress = 1f, accent = accent) }
                Box(Modifier.size(120.dp).background(ground)) { TouchMarksLayer(down = null, mark = TouchMark(1, MarkKind.LANDED, at), progress = 0f, accent = accent) }
                Box(Modifier.size(120.dp).background(ground)) { TouchMarksLayer(down = null, mark = TouchMark(2, MarkKind.MISSED, at, MissReason.OUTSIDE), progress = 0f, accent = accent) }
                Box(Modifier.size(120.dp).background(ground)) { TouchMarksLayer(down = null, mark = TouchMark(3, MarkKind.LANDED, at), progress = 1f, accent = accent) }
            }
        }
        waitForIdle()
        val img = onNodeWithTag("shot").captureToImage().toAwtImage()
        val dir = File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/takeover-touch").apply { mkdirs() }
        ImageIO.write(img, "png", File(dir, "touch-marks.png"))
        val d = img.width / 4   // one box in pixels (density 1 in the test: 120 dp = 120 px)
        // 1: a ring around the finger (white), nothing in its middle
        assertTrue((78..86).any { rgb(img, it, 60).let { c -> c.red > 0.65f && c.green > 0.65f } }, "the finger ring")
        assertEquals(ground, rgb(img, 60, 60))
        // 2: a dot in the accent colour at the mapped point, and a ring
        assertEquals(accent, rgb(img, d + 60, 60))
        assertTrue((70..78).any { rgb(img, d + it, 60).red > 0.65f }, "the landed ring")
        // 3: the missed mark is red (dashed ring: look along the cross, which is solid)
        val cross = rgb(img, 2 * d + 60 - 3, 60 - 3)
        assertTrue(cross.red > 0.7f && cross.green < 0.4f, "the missed cross: $cross")
        // 4: faded all the way: the box is as it was
        for (x in listOf(60, 74, 40)) assertEquals(ground, rgb(img, 3 * d + x, 60))
    }
}
