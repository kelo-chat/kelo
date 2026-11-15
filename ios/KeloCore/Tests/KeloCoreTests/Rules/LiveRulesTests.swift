import Foundation
import Testing
@testable import KeloCore

/// Port of android agent/LiveRulesTest.kt (LiveGeometryTest, LiveMessagesTest, LiveMachineTest). Its LookbackRulesTest is in
/// AgentRulesTests.swift.
@Suite struct LiveGeometryTests {
    private func near(_ a: Double, _ b: Double) -> Bool { abs(a - b) < 1e-4 }

    @Test func aWideFrameInATallViewIsLetterboxedTopAndBottom() throws {
        // 720x450 in 360x800: scale 0.5, picture 360x225, centred vertically
        let box = try #require(LiveGeometry.fit(viewW: 360, viewH: 800, frameW: 720, frameH: 450))
        #expect(near(box.scale, 0.5))
        #expect(near(box.left, 0))
        #expect(near(box.top, (800 - 225) / 2))
    }

    @Test func aTallFrameInAWideViewIsLetterboxedLeftAndRight() throws {
        let box = try #require(LiveGeometry.fit(viewW: 1000, viewH: 500, frameW: 500, frameH: 1000))
        #expect(near(box.scale, 0.5))
        #expect(near(box.left, 375))
        #expect(near(box.top, 0))
    }

    @Test func unknownSizesGiveNoBox() {
        #expect(LiveGeometry.fit(viewW: 0, viewH: 100, frameW: 10, frameH: 10) == nil)
        #expect(LiveGeometry.fit(viewW: 100, viewH: 100, frameW: 0, frameH: 10) == nil)
        #expect(LiveGeometry.toFrame(x: 5, y: 5, viewW: 100, viewH: 100, frameW: 0, frameH: 0) == nil)
    }

    @Test func aTapMapsToFramePixelsThroughTheLetterbox() throws {
        // picture starts at y = 287.5 and is half size
        let p = try #require(LiveGeometry.toFrame(x: 180, y: 287.5 + 100, viewW: 360, viewH: 800, frameW: 720, frameH: 450))
        #expect(p.x == 360)
        #expect(p.y == 200)
    }

    @Test func aTapOnTheBarsSendsNothing() {
        #expect(LiveGeometry.toFrame(x: 180, y: 10, viewW: 360, viewH: 800, frameW: 720, frameH: 450) == nil)
        #expect(LiveGeometry.toFrame(x: 180, y: 790, viewW: 360, viewH: 800, frameW: 720, frameH: 450) == nil)
    }

    @Test func zoomAroundTheCentreIsUndone() {
        // a square frame filling a square view, zoomed 2x: the view's centre is still the frame's centre
        #expect(LiveGeometry.toFrame(x: 500, y: 500, viewW: 1000, viewH: 1000, frameW: 1000, frameH: 1000, zoom: 2) == FramePoint(x: 500, y: 500))
        // at 2x the view's top-left corner shows the frame's point (250, 250)
        #expect(LiveGeometry.toFrame(x: 0, y: 0, viewW: 1000, viewH: 1000, frameW: 1000, frameH: 1000, zoom: 2) == FramePoint(x: 250, y: 250))
    }

    @Test func panIsUndoneAfterZoom() {
        // zoomed 2x and moved 200 px right: the view centre now shows a point 100 frame px to the left of the middle
        #expect(LiveGeometry.toFrame(x: 500, y: 500, viewW: 1000, viewH: 1000, frameW: 1000, frameH: 1000, zoom: 2, panX: 200) == FramePoint(x: 400, y: 500))
    }

    @Test func framePixelsStayInsideTheFrame() throws {
        let p = try #require(LiveGeometry.toFrame(x: 1000, y: 1000, viewW: 1000, viewH: 1000, frameW: 1000, frameH: 1000))
        #expect(p.x == 999)
        #expect(p.y == 999)
    }

    @Test func aDragScrollsTheOtherWayInFramePixels() {
        // half-size picture: 100 view px = 200 frame px; finger up = page down
        let d = LiveGeometry.scrollDelta(dx: 0, dy: -100, viewW: 360, viewH: 800, frameW: 720, frameH: 450)
        #expect(d.x == 0)
        #expect(d.y == 200)
        // zoomed in 2x the same finger move is half as far on the page
        #expect(LiveGeometry.scrollDelta(dx: 0, dy: -100, viewW: 360, viewH: 800, frameW: 720, frameH: 450, zoom: 2).y == 100)
    }

    /// Not in the Android test: Kotlin's roundToInt rounds a half up (-2.5 -> -2), which the Swift port now does too.
    @Test func aHalfPixelScrollRoundsLikeKotlin() {
        #expect(LiveGeometry.scrollDelta(dx: -1.25, dy: 1.25, viewW: 360, viewH: 800, frameW: 720, frameH: 450) == FramePoint(x: 3, y: -2))
    }

    @Test func panStaysInsideTheZoomedPicture() {
        let a = LiveGeometry.clampPan(panX: 50, panY: -50, zoom: 1, viewW: 400, viewH: 800)
        #expect(a.0 == 0 && a.1 == 0)
        #expect(a.0.sign == .plus && a.1.sign == .plus)   // never -0.0
        let b = LiveGeometry.clampPan(panX: 900, panY: -900, zoom: 2, viewW: 400, viewH: 800)
        #expect(b.0 == 200 && b.1 == -400)
        #expect(LiveGeometry.clampZoom(0.3) == 1)
        #expect(LiveGeometry.clampZoom(9) == 5)
    }
}

@Suite struct LiveMessagesTests {
    @Test func qualityFollowsTheNetwork() {
        #expect(LiveQuality.pick(metered: false) == LiveQuality(maxWidth: 720, quality: 55, fps: 10))
        #expect(LiveQuality.pick(metered: true) == LiveQuality(maxWidth: 540, quality: 45, fps: 5))
        let m = LiveQuality.metered.message()
        #expect(m["type"]?.stringValue == "quality")
        #expect(m["max_width"]?.intValue == 540)
        #expect(LiveQuality.wifi.query() == "max_width=720&quality=55&fps=10")
    }

    @Test func serverMessagesAreSortedByType() throws {
        let f = LiveMessages.parse(#"{"type":"frame","seq":7,"w":720,"h":1280,"ts":1.5,"url":"https://a.b/c","title":"T","bytes":900,"css_w":390,"css_h":693}"#)
        guard case .frame(let header) = f else { Issue.record("not a frame: \(f)"); return }
        #expect(header.seq == 7)
        #expect(header.cssW == 390)
        let s = LiveMessages.parse(
            #"{"type":"status","id":"owner","controller":"user","controlled_by":"5","you_control":false,"label":"직접 조작 중","# +
                #""agent":{"busy":true,"status":"사이트 보는 중","task_id":3,"title":"x","waiting_approval":false},"viewers":2}"#)
        guard case .status(let status) = s else { Issue.record("not a status: \(s)"); return }
        #expect(status.someoneElse)
        #expect(status.viewers == 2)
        #expect(status.agent.status == "사이트 보는 중")
        #expect(LiveMessages.parse(#"{"type":"ended","reason":"tab_closed"}"#) == .ended(reason: "tab_closed"))
        #expect(LiveMessages.parse(#"{"type":"error","code":"not_controller","message":"m","for":"tap"}"#) == .error(code: "not_controller", forType: "tap"))
        #expect(LiveMessages.parse(#"{"type":"pong","ts":1}"#) == .pong)
        #expect(LiveMessages.parse("not json") == .other)
        #expect(LiveMessages.parse("[1,2]") == .other)
        #expect(LiveMessages.parse(#"{"type":"input_ok"}"#) == .other)
    }

    @Test func myOwnControlShowsAsMine() {
        let s = LiveMessages.parse(#"{"type":"status","controller":"user","controlled_by":"9","you_control":true}"#)
        guard case .status(let status) = s else { Issue.record("not a status: \(s)"); return }
        #expect(status.youControl)
        #expect(!status.someoneElse)
    }

    @Test func inputCarriesTheFrameSizeItRefersTo() {
        let t = LiveMessages.tap(FramePoint(x: 10, y: 20), frameW: 720, frameH: 1280)
        #expect(t["type"]?.stringValue == "tap")
        #expect(t["x"]?.intValue == 10)
        #expect(t["frame_w"]?.intValue == 720)
        #expect(LiveMessages.longPress(FramePoint(x: 1, y: 1), frameW: 2, frameH: 2)["type"]?.stringValue == "long_press")
        let sc = LiveMessages.scroll(FramePoint(x: 0, y: 120), at: nil, frameW: 720, frameH: 1280)
        #expect(sc["dy"]?.intValue == 120)
        #expect(sc["x"] == nil)
        #expect(LiveMessages.key("Enter")["key"]?.stringValue == "Enter")
        #expect(LiveMessages.nav("back")["type"]?.stringValue == "back")
        #expect(LiveMessages.control(take: true)["take"] == .bool(true))
    }

    @Test func longTextGoesInPiecesTheServerTakes() {
        #expect(LiveMessages.chunks("").isEmpty)
        #expect(LiveMessages.chunks("abc") == ["abc"])
        let parts = LiveMessages.chunks(String(repeating: "x", count: 4500))
        #expect(parts.map(\.count) == [2000, 2000, 500])
    }

    @Test func theControlAnswerUpdatesWhoHoldsTheTab() {
        let before = LiveStatus()
        let o: JSON = ["id": "owner", "controller": "user", "controlled_by": "4", "you_control": true]
        let after = LiveMessages.controlAnswer(o, before: before)
        #expect(after.youControl)
        #expect(after.controller == "user")
        #expect(LiveMessages.controlAnswer(nil, before: before) == before)
    }
}

@Suite struct LiveMachineTests {
    @Test func startsWithAFreshTicket() {
        var m = LiveMachine()
        #expect(m.start() == .openSocket(freshTicket: true))
        #expect(m.mode == .connecting)
    }

    @Test func twoFailedConnectsFallBackToOneFrameAtATime() {
        var m = LiveMachine()
        _ = m.start()
        #expect(m.connectFailed() == .openSocket(freshTicket: true, delayMs: LiveMachine.retryMs))
        #expect(m.connectFailed() == .poll())
        #expect(m.mode == .polling)
    }

    @Test func aCloseBeforeItEverOpenedCountsAsAFailedConnect() {
        var m = LiveMachine()
        _ = m.start()
        guard case .openSocket = m.closed(1006) else { Issue.record("expected openSocket"); return }
        #expect(m.closed(1006) == .poll())
    }

    @Test func aWorkingConnectionResetsTheFailureCount() {
        var m = LiveMachine()
        _ = m.start()
        _ = m.connectFailed()
        m.opened()
        #expect(m.connectFailures == 0)
        #expect(m.mode == .live)
    }

    @Test func ticket4401GetsOneNewTicketThenFramesOneAtATime() {
        var m = LiveMachine()
        _ = m.start(); m.opened()
        #expect(m.closed(4401) == .openSocket(freshTicket: true))
        #expect(m.closed(4401) == .poll())
        #expect(m.mode == .polling)
    }

    @Test func closed4404AndEndedAreTheEnd() {
        var a = LiveMachine(); _ = a.start(); a.opened()
        #expect(a.closed(4404) == .end(.noTab))
        #expect(a.mode == .ended)
        // nothing restarts an ended view
        #expect(a.closed(1006) == .end(.noTab))
        #expect(a.connectFailed() == .end(.noTab))

        var b = LiveMachine(); _ = b.start(); b.opened()
        #expect(b.ended("tab_closed") == .end(.tabClosed))
        #expect(b.closed(1000) == .end(.tabClosed))
        var c = LiveMachine(); _ = c.start()
        #expect(c.ended("no_tab") == .end(.noTab))
    }

    @Test func closed4408ReconnectsAtOnce4429Waits() {
        var m = LiveMachine(); _ = m.start(); m.opened()
        #expect(m.closed(4408) == .openSocket(freshTicket: true))
        m.opened()
        #expect(m.closed(4429) == .openSocket(freshTicket: true, delayMs: LiveMachine.tooManyMs))
        #expect(m.mode == .waiting)
    }

    @Test func aDroppedConnectionWaitsALittleLongerEachTime() {
        var m = LiveMachine(); _ = m.start(); m.opened()
        #expect(m.closed(1006) == .openSocket(freshTicket: true, delayMs: 1_000))
        #expect(m.closed(1006) == .openSocket(freshTicket: true, delayMs: 2_000))
        #expect(m.closed(1011) == .openSocket(freshTicket: true, delayMs: 4_000))
        m.opened()
        #expect(m.closed(1006) == .openSocket(freshTicket: true, delayMs: 1_000))
    }

    @Test func pollingGoesOnWaitsOnErrorsAndEndsOn404() {
        var m = LiveMachine(); _ = m.start(); _ = m.connectFailed(); _ = m.connectFailed()
        #expect(m.polled(status: 200, failures: 0) == .poll())
        #expect(m.polled(status: 204, failures: 0) == .poll())
        #expect(m.polled(status: 502, failures: 2) == .poll(delayMs: 2_000))
        #expect(m.polled(status: 0, failures: 1) == .poll(delayMs: 1_000))
        #expect(m.polled(status: 404, failures: 0) == .end(.noTab))
    }

    @Test func noBrowserAtAllEndsWithThePicture() {
        var m = LiveMachine(); _ = m.start()
        #expect(m.noBrowser() == .end(.noBrowser))
    }

    @Test func backoffIsCapped() {
        #expect(LiveMachine.backoff(0) == 0)
        #expect(LiveMachine.backoff(1) == 1_000)
        #expect(LiveMachine.backoff(4) == 8_000)
        #expect(LiveMachine.backoff(5) == 15_000)
        #expect(LiveMachine.backoff(50) == 15_000)
    }
}
