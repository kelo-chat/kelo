package chat.kelo.app.agent

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class LiveGeometryTest {
    @Test fun `a wide frame in a tall view is letterboxed top and bottom`() {
        // 720x450 in 360x800: scale 0.5, picture 360x225, centred vertically
        val box = LiveGeometry.fit(360f, 800f, 720, 450)!!
        assertEquals(0.5f, box.scale, 1e-4f)
        assertEquals(0f, box.left, 1e-4f)
        assertEquals((800f - 225f) / 2f, box.top, 1e-4f)
    }

    @Test fun `a tall frame in a wide view is letterboxed left and right`() {
        val box = LiveGeometry.fit(1000f, 500f, 500, 1000)!!
        assertEquals(0.5f, box.scale, 1e-4f)
        assertEquals(375f, box.left, 1e-4f)
        assertEquals(0f, box.top, 1e-4f)
    }

    @Test fun `unknown sizes give no box`() {
        assertNull(LiveGeometry.fit(0f, 100f, 10, 10))
        assertNull(LiveGeometry.fit(100f, 100f, 0, 10))
        assertNull(LiveGeometry.toFrame(5f, 5f, 100f, 100f, 0, 0))
    }

    @Test fun `a tap maps to frame pixels through the letterbox`() {
        // picture starts at y = 287.5 and is half size
        val p = LiveGeometry.toFrame(180f, 287.5f + 100f, 360f, 800f, 720, 450)!!
        assertEquals(360, p.x)
        assertEquals(200, p.y)
    }

    @Test fun `a tap on the bars sends nothing`() {
        assertNull(LiveGeometry.toFrame(180f, 10f, 360f, 800f, 720, 450))
        assertNull(LiveGeometry.toFrame(180f, 790f, 360f, 800f, 720, 450))
    }

    @Test fun `zoom around the centre is undone`() {
        // a square frame filling a square view, zoomed 2x: the view's centre is still the frame's centre
        assertEquals(FramePoint(500, 500), LiveGeometry.toFrame(500f, 500f, 1000f, 1000f, 1000, 1000, zoom = 2f))
        // at 2x the view's top-left corner shows the frame's point (250, 250)
        assertEquals(FramePoint(250, 250), LiveGeometry.toFrame(0f, 0f, 1000f, 1000f, 1000, 1000, zoom = 2f))
    }

    @Test fun `pan is undone after zoom`() {
        // zoomed 2x and moved 200 px right: the view centre now shows a point 100 frame px to the left of the middle
        assertEquals(FramePoint(400, 500), LiveGeometry.toFrame(500f, 500f, 1000f, 1000f, 1000, 1000, zoom = 2f, panX = 200f))
    }

    @Test fun `frame pixels stay inside the frame`() {
        val p = LiveGeometry.toFrame(1000f, 1000f, 1000f, 1000f, 1000, 1000)!!
        assertEquals(999, p.x)
        assertEquals(999, p.y)
    }

    @Test fun `a drag scrolls the other way, in frame pixels`() {
        // half-size picture: 100 view px = 200 frame px; finger up = page down
        val d = LiveGeometry.scrollDelta(0f, -100f, 360f, 800f, 720, 450)
        assertEquals(0, d.x)
        assertEquals(200, d.y)
        // zoomed in 2x the same finger move is half as far on the page
        assertEquals(100, LiveGeometry.scrollDelta(0f, -100f, 360f, 800f, 720, 450, zoom = 2f).y)
    }

    @Test fun `pan stays inside the zoomed picture`() {
        assertEquals(0f to 0f, LiveGeometry.clampPan(50f, -50f, 1f, 400f, 800f))
        assertEquals(200f to -400f, LiveGeometry.clampPan(900f, -900f, 2f, 400f, 800f))
        assertEquals(1f, LiveGeometry.clampZoom(0.3f))
        assertEquals(5f, LiveGeometry.clampZoom(9f))
    }

    @Test fun `cssToFrame scales viewport CSS px to frame pixels`() {
        // css 360x640 in frame 720x1280 (2x scaling)
        val p = LiveGeometry.cssToFrame(100f, 200f, 720, 1280, 360, 640)
        assertEquals(200f, p.x, 1e-4f)
        assertEquals(400f, p.y, 1e-4f)
        // unknown css size falls back to css coords
        val fallback = LiveGeometry.cssToFrame(50f, 60f, 720, 1280, 0, 0)
        assertEquals(50f, fallback.x, 1e-4f)
        assertEquals(60f, fallback.y, 1e-4f)
    }

    @Test fun `pointer maps to view pixels through letterbox and zoom`() {
        // frame 720x450 in view 360x800: scale 0.5, letterboxed top and bottom (top = 287.5f)
        // css 360x225 -> frame 720x450 (2x)
        val p = LiveGeometry.pointerToView(180f, 100f, 360f, 800f, 720, 450, 360, 225)!!
        assertEquals(180f, p.x, 1e-4f)
        assertEquals(287.5f + 100f, p.y, 1e-4f)

        // with 2x zoom around centre (cx = 180, cy = 400) and pan (20, -30)
        val pZoomed = LiveGeometry.pointerToView(180f, 100f, 360f, 800f, 720, 450, 360, 225, zoom = 2f, panX = 20f, panY = -30f)!!
        // un-zoomed point: (180, 387.5); relative to center: dx = 0, dy = -12.5; scaled by 2: (0, -25); + pan: (20, -55); + center: (200, 345)
        assertEquals(200f, pZoomed.x, 1e-4f)
        assertEquals(345f, pZoomed.y, 1e-4f)
    }
}

class LiveMessagesTest {
    @Test fun `quality follows the network`() {
        assertEquals(LiveQuality(720, 55, 10), LiveQuality.pick(metered = false))
        assertEquals(LiveQuality(540, 45, 5), LiveQuality.pick(metered = true))
        val m = LiveQuality.METERED.message()
        assertEquals("quality", m["type"]!!.jsonPrimitive.content)
        assertEquals(540, m["max_width"]!!.jsonPrimitive.int)
        assertEquals("max_width=720&quality=55&fps=10", LiveQuality.WIFI.query())
    }

    @Test fun `a meta line wins over the header of the last picture, until a picture comes again`() {
        val header = FrameHeader(url = "https://old.example/", title = "Old")
        assertEquals("Old", LiveHeader.title(null, header))
        assertEquals("https://old.example/", LiveHeader.url(null, header))
        val meta = LiveMeta("https://new.example/pay", "Pay")
        assertEquals("Pay", LiveHeader.title(meta, header))
        assertEquals("https://new.example/pay", LiveHeader.url(meta, header))
        assertEquals("Old", LiveHeader.title(null, header))    // LiveClient clears the meta when a picture arrives
        assertNull(LiveHeader.title(LiveMeta("https://a.b/", ""), null))   // a page without a title: the caller falls back to the host
        assertNull(LiveHeader.title(null, null))
    }

    @Test fun `the keyboard rises on the meta line while frames are off, not on the old picture's header`() {
        // frames off (VNC): the last picture's header says no field, the meta line says one has focus -> the keyboard comes up
        val oldHeader = FrameHeader(editing = false, liveV = 2)
        val focused = LiveMessages.parse("""{"type":"meta","url":"https://a.b/","title":"T","editing":true}""") as LiveIn.Meta
        assertTrue(focused.meta.editing)
        assertTrue(LiveHeader.editing(focused.meta, oldHeader))
        assertTrue(EditingRule.raise(false, LiveHeader.editing(focused.meta, oldHeader)))
        // the field lost focus: the next meta line says so even though an old header said editing
        val gone = LiveMessages.parse("""{"type":"meta","url":"https://a.b/","title":"T","editing":false}""") as LiveIn.Meta
        assertFalse(LiveHeader.editing(gone.meta, FrameHeader(editing = true)))
        // no meta (frames on): the frame header decides, as before
        assertTrue(LiveHeader.editing(null, FrameHeader(editing = true)))
        assertFalse(LiveHeader.editing(null, null))
        // a server from before `editing` leaves it out of the meta line: no keyboard, and no crash
        assertFalse((LiveMessages.parse("""{"type":"meta","url":"https://a.b/","title":"T"}""") as LiveIn.Meta).meta.editing)
    }

    @Test fun `text for the page comes from the phone clipboard only when there is some and is cut at one entry`() {
        assertNull(ClipboardRules.pasteText(null))
        assertNull(ClipboardRules.pasteText(""))
        assertEquals("  hi \n", ClipboardRules.pasteText("  hi \n"))    // typed as it is (a space or a newline may matter)
        assertEquals(LiveMessages.MAX_TEXT, ClipboardRules.pasteText("x".repeat(LiveMessages.MAX_TEXT + 500))!!.length)
    }

    @Test fun `server messages are sorted by type`() {
        val f = LiveMessages.parse("""{"type":"frame","seq":7,"w":720,"h":1280,"ts":1.5,"url":"https://a.b/c","title":"T","bytes":900,"css_w":390,"css_h":693}""")
        assertTrue(f is LiveIn.Frame)
        f as LiveIn.Frame
        assertEquals(7L, f.header.seq)
        assertEquals(390, f.header.cssW)
        val s = LiveMessages.parse(
            """{"type":"status","id":"owner","controller":"user","controlled_by":"5","you_control":false,"label":"직접 조작 중",""" +
                """"agent":{"busy":true,"status":"사이트 보는 중","task_id":3,"title":"x","waiting_approval":false},"viewers":2}""",
        ) as LiveIn.Status
        assertTrue(s.status.someoneElse)
        assertEquals(2, s.status.viewers)
        assertEquals("사이트 보는 중", s.status.agent.status)
        assertEquals(LiveIn.Ended("tab_closed"), LiveMessages.parse("""{"type":"ended","reason":"tab_closed"}"""))
        assertEquals(LiveIn.Error("not_controller"), LiveMessages.parse("""{"type":"error","code":"not_controller","message":"m","for":"tap"}"""))
        assertEquals(LiveIn.Pong, LiveMessages.parse("""{"type":"pong","ts":1}"""))
        assertEquals(LiveIn.Meta(LiveMeta("https://a.b/x", "새 제목")), LiveMessages.parse("""{"type":"meta","url":"https://a.b/x","title":"새 제목"}"""))
        assertEquals(LiveIn.Other, LiveMessages.parse("not json"))
        assertEquals(LiveIn.Other, LiveMessages.parse("""[1,2]"""))
        assertEquals(LiveIn.Other, LiveMessages.parse("""{"type":"something_new"}"""))   // input_ok is read now (takeover-shared)
    }

    @Test fun `pointer messages are parsed and unknown fields are ignored`() {
        val p = LiveMessages.parse("""{"type":"pointer","x":120.5,"y":240.0,"down":true,"extra":"ignored"}""")
        assertTrue(p is LiveIn.Pointer)
        p as LiveIn.Pointer
        assertEquals(120.5f, p.pointer.x, 1e-4f)
        assertEquals(240.0f, p.pointer.y, 1e-4f)
        assertTrue(p.pointer.down)
    }

    @Test fun `status carries pointer, page_busy, and stale`() {
        val s = LiveMessages.parse(
            """{"type":"status","pointer":{"x":50.0,"y":60.5,"down":false},"page_busy":true,"stale":true,"unknown":123}""",
        ) as LiveIn.Status
        assertEquals(50f, s.status.pointer?.x ?: 0f, 1e-4f)
        assertEquals(60.5f, s.status.pointer?.y ?: 0f, 1e-4f)
        assertFalse(s.status.pointer?.down ?: true)
        assertTrue(s.status.pageBusy)
        assertTrue(s.status.stale)

        val s2 = LiveMessages.parse("""{"type":"status"}""") as LiveIn.Status
        assertNull(s2.status.pointer)
        assertFalse(s2.status.pageBusy)
        assertFalse(s2.status.stale)
    }

    @Test fun `my own control shows as mine`() {
        val s = LiveMessages.parse("""{"type":"status","controller":"user","controlled_by":"9","you_control":true}""") as LiveIn.Status
        assertTrue(s.status.youControl)
        assertFalse(s.status.someoneElse)
    }

    @Test fun `input carries the frame size it refers to`() {
        val t = LiveMessages.tap(FramePoint(10, 20), 720, 1280)
        assertEquals("tap", t["type"]!!.jsonPrimitive.content)
        assertEquals(10, t["x"]!!.jsonPrimitive.int)
        assertEquals(720, t["frame_w"]!!.jsonPrimitive.int)
        assertEquals("long_press", LiveMessages.longPress(FramePoint(1, 1), 2, 2)["type"]!!.jsonPrimitive.content)
        val sc = LiveMessages.scroll(FramePoint(0, 120), null, 720, 1280)
        assertEquals(120, sc["dy"]!!.jsonPrimitive.int)
        assertNull(sc["x"])
        assertEquals("Enter", LiveMessages.key("Enter")["key"]!!.jsonPrimitive.content)
        assertEquals("back", LiveMessages.nav("back")["type"]!!.jsonPrimitive.content)
        assertEquals(JsonPrimitive(true), LiveMessages.control(true)["take"])
    }

    @Test fun `long text goes in pieces the server takes`() {
        assertTrue(LiveMessages.chunks("").isEmpty())
        assertEquals(listOf("abc"), LiveMessages.chunks("abc"))
        val parts = LiveMessages.chunks("x".repeat(4500))
        assertEquals(listOf(2000, 2000, 500), parts.map { it.length })
    }

    @Test fun `the control answer updates who holds the tab`() {
        val before = LiveStatus()
        val o = kotlinx.serialization.json.buildJsonObject {
            put("id", JsonPrimitive("owner")); put("controller", JsonPrimitive("user")); put("controlled_by", JsonPrimitive("4")); put("you_control", JsonPrimitive(true))
        }
        val after = LiveMessages.controlAnswer(o, before)
        assertTrue(after.youControl)
        assertEquals("user", after.controller)
        assertEquals(before, LiveMessages.controlAnswer(null, before))
    }
}

class LiveMachineTest {
    @Test fun `starts with a fresh ticket`() {
        val m = LiveMachine()
        assertEquals(LiveStep.OpenSocket(freshTicket = true), m.start())
        assertEquals(LiveMode.CONNECTING, m.mode)
    }

    @Test fun `two failed connects fall back to one frame at a time`() {
        val m = LiveMachine()
        m.start()
        assertEquals(LiveStep.OpenSocket(true, LiveMachine.RETRY_MS), m.connectFailed())
        assertEquals(LiveStep.Poll(), m.connectFailed())
        assertEquals(LiveMode.POLLING, m.mode)
    }

    @Test fun `a close before it ever opened counts as a failed connect`() {
        val m = LiveMachine()
        m.start()
        assertTrue(m.closed(1006) is LiveStep.OpenSocket)
        assertEquals(LiveStep.Poll(), m.closed(1006))
    }

    @Test fun `a working connection resets the failure count`() {
        val m = LiveMachine()
        m.start()
        m.connectFailed()
        m.opened()
        assertEquals(0, m.connectFailures)
        assertEquals(LiveMode.LIVE, m.mode)
    }

    @Test fun `4401 gets one new ticket, then frames one at a time`() {
        val m = LiveMachine()
        m.start(); m.opened()
        assertEquals(LiveStep.OpenSocket(freshTicket = true), m.closed(4401))
        assertEquals(LiveStep.Poll(), m.closed(4401))
        assertEquals(LiveMode.POLLING, m.mode)
    }

    @Test fun `4404 and ended are the end`() {
        val a = LiveMachine(); a.start(); a.opened()
        assertEquals(LiveStep.End(LiveEnd.NO_TAB), a.closed(4404))
        assertEquals(LiveMode.ENDED, a.mode)
        // nothing restarts an ended view
        assertEquals(LiveStep.End(LiveEnd.NO_TAB), a.closed(1006))
        assertEquals(LiveStep.End(LiveEnd.NO_TAB), a.connectFailed())

        val b = LiveMachine(); b.start(); b.opened()
        assertEquals(LiveStep.End(LiveEnd.TAB_CLOSED), b.ended("tab_closed"))
        assertEquals(LiveStep.End(LiveEnd.TAB_CLOSED), b.closed(1000))
        val c = LiveMachine(); c.start()
        assertEquals(LiveStep.End(LiveEnd.NO_TAB), c.ended("no_tab"))
    }

    @Test fun `4408 reconnects at once, 4429 waits`() {
        val m = LiveMachine(); m.start(); m.opened()
        assertEquals(LiveStep.OpenSocket(freshTicket = true), m.closed(4408))
        m.opened()
        assertEquals(LiveStep.OpenSocket(true, LiveMachine.TOO_MANY_MS), m.closed(4429))
        assertEquals(LiveMode.WAITING, m.mode)
    }

    @Test fun `a dropped connection waits a little longer each time`() {
        val m = LiveMachine(); m.start(); m.opened()
        assertEquals(LiveStep.OpenSocket(true, 1_000), m.closed(1006))
        assertEquals(LiveStep.OpenSocket(true, 2_000), m.closed(1006))
        assertEquals(LiveStep.OpenSocket(true, 4_000), m.closed(1011))
        m.opened()
        assertEquals(LiveStep.OpenSocket(true, 1_000), m.closed(1006))
    }

    @Test fun `polling goes on, waits on errors and ends on 404`() {
        val m = LiveMachine(); m.start(); m.connectFailed(); m.connectFailed()
        assertEquals(LiveStep.Poll(), m.polled(200, 0))
        assertEquals(LiveStep.Poll(), m.polled(204, 0))
        assertEquals(LiveStep.Poll(2_000), m.polled(502, 2))
        assertEquals(LiveStep.Poll(1_000), m.polled(0, 1))
        assertEquals(LiveStep.End(LiveEnd.NO_TAB), m.polled(404, 0))
    }

    @Test fun `no browser at all ends with the picture`() {
        val m = LiveMachine(); m.start()
        assertEquals(LiveStep.End(LiveEnd.NO_BROWSER), m.noBrowser())
    }

    @Test fun `backoff is capped`() {
        assertEquals(0L, LiveMachine.backoff(0))
        assertEquals(1_000L, LiveMachine.backoff(1))
        assertEquals(8_000L, LiveMachine.backoff(4))
        assertEquals(15_000L, LiveMachine.backoff(5))
        assertEquals(15_000L, LiveMachine.backoff(50))
    }
}

class LookbackRulesTest {
    private fun r(id: Long, status: String, conf: Double = 0.5, date: String = "2026-11-0$id", rule: String = "규칙 $id") =
        LearnedRule(id, rule, "", date, conf, status)

    @Test fun `the last run becomes a plain sentence from its fields, never the server's log text`() {
        val log = "2 new, 0 updated, 0 dropped; 사용자가 승인을 3회 거절함"
        assertEquals(RunLine(LookbackLine.DONE, newRules = 2), LookbackRules.runLine(LookbackRun(1.0, "done", added = 2, reason = log)))
        assertEquals(RunLine(LookbackLine.DONE, newRules = 0), LookbackRules.runLine(LookbackRun(1.0, "done")))
        assertEquals(RunLine(LookbackLine.NO_CHAT), LookbackRules.runLine(LookbackRun(1.0, "skipped", code = "no_messages")))
        assertEquals(RunLine(LookbackLine.OFF), LookbackRules.runLine(LookbackRun(1.0, "skipped", code = "switched_off")))
        assertEquals(RunLine(LookbackLine.BUSY), LookbackRules.runLine(LookbackRun(1.0, "skipped", code = "in_use")))
        assertEquals(RunLine(LookbackLine.SKIPPED), LookbackRules.runLine(LookbackRun(1.0, "skipped")))
        assertEquals(RunLine(LookbackLine.FAILED_WHY, why = LookbackFail.MODEL, failed = true),
            LookbackRules.runLine(LookbackRun(1.0, "failed", code = "model_error", reason = "RuntimeError: boom")))
        assertEquals(RunLine(LookbackLine.FAILED_WHY, why = LookbackFail.ANSWER, failed = true),
            LookbackRules.runLine(LookbackRun(1.0, "failed", code = "bad_output")))
        assertEquals(RunLine(LookbackLine.FAILED, failed = true), LookbackRules.runLine(LookbackRun(1.0, "failed")))
        assertEquals(RunLine(LookbackLine.NO_MODEL, withWhen = false), LookbackRules.runLine(LookbackRun(1.0, "pending", code = "no_model")))
        assertNull(LookbackRules.runLine(LookbackRun(1.0, "busy")))
    }

    @Test fun `when the run happened, in words`() {
        val zone = java.time.ZoneId.of("Asia/Seoul")
        val now = java.time.ZonedDateTime.of(2026, 11, 8, 21, 0, 0, 0, zone)
        fun at(d: Int, h: Int) = java.time.ZonedDateTime.of(2026, 11, d, h, 30, 0, 0, zone).toEpochSecond().toDouble()
        assertEquals(RunWhen.LAST_NIGHT, LookbackRules.runWhen(at(8, 3), now))     // 03:30 today, 18 hours ago
        assertEquals(RunWhen.LAST_NIGHT, LookbackRules.runWhen(at(7, 23), now))
        assertEquals(RunWhen.TODAY, LookbackRules.runWhen(at(8, 14), now))
        assertEquals(RunWhen.DAY, LookbackRules.runWhen(at(7, 10), now))
        assertEquals(RunWhen.DAY, LookbackRules.runWhen(at(5, 3), now))
    }

    @Test fun `a rule just kept or discarded stays among the suggestions for the undo`() {
        val rules = listOf(r(1, "accepted", 0.9), r(2, "proposed", 0.6), r(3, "rejected"))
        val g = LookbackRules.group(rules, setOf(1L, 3L))
        assertEquals(listOf(1L, 2L, 3L), g.proposed.map { it.id }.sorted())
        assertTrue(g.accepted.isEmpty() && g.rejected.isEmpty())
        assertEquals(listOf(1L), LookbackRules.group(rules, emptySet()).accepted.map { it.id })
        assertEquals(5_000L, LookbackRules.UNDO_MILLIS)
    }

    @Test fun `the server answer decodes, older servers count as on`() {
        val l = chat.kelo.app.core.KeloApi.json.decodeFromString<Lookback>(
            """{"enabled":false,"rules":[{"id":3,"rule":"답은 짧게","evidence":"길다고 두 번 말함","date":"2026-11-06","confidence":0.9,"status":"proposed"}],""" +
                """"last_run":{"at":2.0,"status":"skipped","reason":"no new messages","changed":false},"guide":"- 답은 짧게"}""",
        )
        assertFalse(l.enabled)
        assertEquals("skipped", l.lastRun!!.status)
        assertEquals("", l.lastRun!!.code)   // an older server: no code, the line falls back to "skipped"
        val run = chat.kelo.app.core.KeloApi.json.decodeFromString<LookbackRun>(
            """{"at":2.0,"status":"done","code":"","added":2,"updated":0,"dropped":0,"goals":1,"held":1,"reason":"2 new","changed":true}""")
        assertEquals(2, run.added)
        assertEquals(1, run.held)
        assertEquals(1, l.rules.size)
        assertEquals(0.9, l.rules[0].confidence, 0.0)
        assertTrue(l.available)
        assertTrue(chat.kelo.app.core.KeloApi.json.decodeFromString<Lookback>("""{"last_run":null}""").enabled)
    }

    @Test fun `rules fall into proposed, accepted and rejected`() {
        val g = LookbackRules.group(listOf(
            r(1, "accepted"), r(2, "proposed", 0.4), r(3, "proposed", 0.9), r(4, "rejected", date = "2026-11-01"),
            r(5, "rejected", date = "2026-11-05"), r(6, "weird"), r(7, "proposed", rule = "  "),
        ))
        assertEquals(listOf(3L, 2L), g.proposed.map { it.id })
        assertEquals(listOf(1L), g.accepted.map { it.id })
        assertEquals(listOf(5L, 4L), g.rejected.map { it.id })
        assertFalse(g.empty)
        assertTrue(LookbackRules.group(emptyList()).empty)
    }

    @Test fun `in use means accepted or a sure proposal`() {
        assertTrue(LookbackRules.inUse(r(1, "accepted", 0.1)))
        assertTrue(LookbackRules.inUse(r(2, "proposed", 0.8)))
        assertFalse(LookbackRules.inUse(r(3, "proposed", 0.79)))
        assertFalse(LookbackRules.inUse(r(4, "rejected", 1.0)))
    }

    @Test fun `a status change shows at once and the server copy replaces it`() {
        val list = listOf(r(1, "proposed"), r(2, "proposed"))
        val moved = LookbackRules.withStatus(list, 2, "accepted")
        assertEquals(listOf("proposed", "accepted"), moved.map { it.status })
        val back = LookbackRules.replace(moved, r(2, "rejected", rule = "새 문장"))
        assertEquals("새 문장", back[1].rule)
        assertEquals(3, LookbackRules.replace(list, r(9, "accepted")).size)
    }
}

class LiveScreenGuardTest {
    private fun header(json: String): FrameHeader = (LiveMessages.parse(json) as LiveIn.Frame).header

    @Test fun `the live view is capturable whatever the picture says`() {
        assertFalse(LiveRules.guardScreen())
    }

    @Test fun `the hint of a frame is still read, and still does not guard the live view`() {
        val yes = header("""{"type":"frame","seq":1,"w":720,"h":450,"ts":1.0,"url":"","title":"","bytes":9,"secure":true}""")
        val no = header("""{"type":"frame","seq":1,"w":720,"h":450,"ts":1.0,"url":"","title":"","bytes":9,"secure":false}""")
        val none = header("""{"type":"frame","seq":1,"w":720,"h":450,"ts":1.0,"url":"","title":"","bytes":9}""")
        assertEquals(true, yes.secure); assertEquals(false, no.secure); assertNull(none.secure)
        assertFalse(LiveRules.guardScreen())
    }

    @Test fun `the live page does not ask for a secure window`() {
        val src = java.io.File("src/commonMain/kotlin/chat/kelo/app/agent/LivePage.kt").readText()
        assertFalse(src.contains("LocalSecureWindow"))
    }
}

/** The owner's hold (owner decision 2026-11-18): a button hold ends only by hand back; a hand-over hold (the app took the tab because
 *  Kelo asked) gives back when the owner leaves the page. An older server says neither, so nothing is given back on leaving. */
class OwnerHoldRulesTest {
    private fun status(json: String): LiveStatus = (LiveMessages.parse(json) as LiveIn.Status).status

    @Test fun aHandoverTakeSaysSoOnlyWhenTakingForAHandover() {
        assertEquals("handover", LiveMessages.control(true, handover = true)["by"]?.jsonPrimitive?.content)
        assertNull(LiveMessages.control(true)["by"])
        assertNull(LiveMessages.control(false, handover = true)["by"])
    }

    @Test fun theControlAnswerCarriesTheHoldAndWhetherItsHolderIsAway() {
        val before = LiveStatus()
        val after = LiveMessages.controlAnswer(
            kotlinx.serialization.json.Json.parseToJsonElement("""{"controller":"user","controlled_by":"owner","you_control":true,"hold":"button","holder_away":true}""").let { it as kotlinx.serialization.json.JsonObject },
            before,
        )
        assertEquals("button", after.hold)
        assertTrue(after.holderAway)
        assertTrue(after.youControl)
    }

    @Test fun leavingGivesBackOnlyAHandoverHold() {
        assertFalse(LiveRules.giveBackOnLeave(status("""{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"hold":"button"}""")))
        assertTrue(LiveRules.giveBackOnLeave(status("""{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"hold":"handover"}""")))
        assertFalse(LiveRules.giveBackOnLeave(status("""{"type":"status","controller":"user","controlled_by":"owner","you_control":true}""")))   // an older server
        assertFalse(LiveRules.giveBackOnLeave(status("""{"type":"status","controller":"agent","you_control":false,"hold":"handover"}""")))
    }

    @Test fun theWsStatusCarriesTheHoldAndTheAway() {
        val s = status("""{"type":"status","id":"owner","controller":"user","controlled_by":"owner","you_control":true,"hold":"handover","holder_away":false}""")
        assertEquals("handover", s.hold)
        assertFalse(s.holderAway)
        assertTrue(s.handoverHold)
    }

    @Test fun aStaleInputAnswerIsDroppedAndSaysSo() {
        val reply = (LiveMessages.parse("""{"type":"input_ok","input":"tap","ok":false,"busy":false,"stale":true}""") as LiveIn.InputOk).reply
        assertTrue(reply.stale)
        assertTrue(reply.notReached)
        assertFalse((LiveMessages.parse("""{"type":"input_ok","input":"tap","ok":true}""") as LiveIn.InputOk).reply.stale)
    }
}

/** Kelo's work browser (scheduled and background jobs) apart from the owner's own browser (server 2026-11-18). An older server says no browser: main. */
class WorkBrowserTest {
    private fun status(json: String): LiveStatus = (LiveMessages.parse(json) as LiveIn.Status).status
    private fun tab(json: String): BrowserTab = chat.kelo.app.core.KeloApi.json.decodeFromString(BrowserTab.serializer(), json)

    @Test fun aTabSaysWhichBrowserItRunsIn() {
        assertTrue(tab("""{"id":"task:4","browser":"work"}""").inWorkBrowser)
        assertFalse(tab("""{"id":"chat:1","browser":"main"}""").inWorkBrowser)
        assertFalse(tab("""{"id":"chat:1"}""").inWorkBrowser)   // an older server
    }

    @Test fun aStatusSaysWhichBrowserItsTabIsIn() {
        assertTrue(status("""{"type":"status","browser":"work"}""").inWorkBrowser)
        assertFalse(status("""{"type":"status","browser":"main"}""").inWorkBrowser)
        assertFalse(status("""{"type":"status"}""").inWorkBrowser)
    }

    @Test fun theWorkNoteShowsOnlyWhileTheOwnerHoldsAWorkTab() {
        assertTrue(LiveRules.showWorkNote(status("""{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"browser":"work"}""")))
        assertFalse(LiveRules.showWorkNote(status("""{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"browser":"main"}""")))
        assertFalse(LiveRules.showWorkNote(status("""{"type":"status","controller":"agent","you_control":false,"browser":"work"}""")))
        assertFalse(LiveRules.showWorkNote(status("""{"type":"status","controller":"user","controlled_by":"owner","you_control":true}""")))
    }
}
