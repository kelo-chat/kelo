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

    @Test func cssToFrameMapsCssPixelsToFramePixels() throws {
        let p = try #require(LiveGeometry.cssToFrame(cssX: 100, cssY: 200, frameW: 720, frameH: 1280, cssW: 360, cssH: 640))
        #expect(p.x == 200)
        #expect(p.y == 400)
    }

    @Test func pointerToViewMapsPointerToViewThroughLetterboxingAndZoom() throws {
        let v = try #require(LiveGeometry.pointerToView(
            cssX: 180, cssY: 320,
            viewW: 360, viewH: 800,
            frameW: 720, frameH: 1280,
            cssW: 360, cssH: 640
        ))
        #expect(near(v.x, 180))
        #expect(near(v.y, 400))
    }
}

@Suite struct LiveMessagesTests {
    @Test func qualityFollowsTheNetwork() {
        #expect(LiveQuality.pick(metered: false) == LiveQuality(maxWidth: 720, quality: 55, fps: 10))
        #expect(LiveQuality.pick(metered: true) == LiveQuality(maxWidth: 540, quality: 45, fps: 5))
        #expect(LiveQuality.wifi.query() == "max_width=720&quality=55&fps=10")
    }

    @Test func serverMessagesAreSortedByType() throws {
        let f = LiveMessages.parse(#"{"type":"frame","seq":7,"w":720,"h":1280,"ts":1.5,"url":"https://a.b/c","title":"T","bytes":900,"css_w":390,"css_h":693}"#)
        guard case .frame(let header) = f else { Issue.record("not a frame: \(f)"); return }
        #expect(header.seq == 7)
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
        #expect(LiveMessages.parse(#"{"type":"input_ok"}"#) == .inputReply(InputReply()))
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

    @Test func pointerMessageIsParsed() throws {
        let msg = LiveMessages.parse(#"{"type":"pointer","x":123.5,"y":456.0,"down":true}"#)
        guard case .pointer(let p) = msg else { Issue.record("expected pointer: \(msg)"); return }
        #expect(p.x == 123.5)
        #expect(p.y == 456.0)
        #expect(p.down == true)
    }

    @Test func statusPointerAndPageBusyAreParsed() throws {
        let msg = LiveMessages.parse(#"{"type":"status","pointer":{"x":10.0,"y":20.0,"down":false},"page_busy":true,"stale":true}"#)
        guard case .status(let s) = msg else { Issue.record("expected status: \(msg)"); return }
        #expect(s.pageBusy == true)
        #expect(s.stale == true)
        let p = try #require(s.pointer)
        #expect(p.x == 10.0)
        #expect(p.y == 20.0)
        #expect(p.down == false)
    }

    @Test func frameHeaderReadsCssDimensions() throws {
        let f = LiveMessages.parse(#"{"type":"frame","seq":7,"w":720,"h":1280,"css_w":390,"css_h":693}"#)
        guard case .frame(let header) = f else { Issue.record("not a frame: \(f)"); return }
        #expect(header.cssW == 390)
        #expect(header.cssH == 693)
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

/// The live page is always capturable (owner decision 2026-11-16): there is no guard rule any more; the frame's "secure" flag is
/// still read from the wire (a server that sends it, or not, both parse).
@Suite struct LiveSecureRuleTests {
    @Test func theSecureFlagStillParsesAndAbsenceIsNil() {
        for (json, want) in [(#"{"type":"frame","seq":3,"w":720,"h":1280,"secure":false}"#, Bool?.some(false)),
                             (#"{"type":"frame","seq":3,"w":720,"h":1280,"secure":true}"#, Bool?.some(true)),
                             (#"{"type":"frame","seq":3,"w":720,"h":1280}"#, Bool?.none)] {
            guard case .frame(let header) = LiveMessages.parse(json) else { Issue.record("not a frame"); continue }
            #expect(header.secure == want)
        }
    }
}

@Suite struct LiveMetaTests {
    @Test func aMetaLineIsParsedAndWinsOverTheLastPicturesHeaderUntilAPictureComes() {
        #expect(LiveMessages.parse(#"{"type":"meta","url":"https://a.b/x","title":"새 제목"}"#) == .meta(LiveMeta(url: "https://a.b/x", title: "새 제목")))
        let meta = LiveMeta(url: "https://new.example/pay", title: "Pay")
        #expect(LiveHeader.title(meta: nil, frame: "Old") == "Old")
        #expect(LiveHeader.title(meta: meta, frame: "Old") == "Pay")
        #expect(LiveHeader.url(meta: meta, frame: "https://old.example/") == "https://new.example/pay")
        #expect(LiveHeader.url(meta: nil, frame: "https://old.example/") == "https://old.example/")     // the model clears the meta when a picture arrives
        #expect(LiveHeader.title(meta: LiveMeta(url: "https://a.b/", title: ""), frame: nil) == nil)     // a page without a title: the caller falls back to the host
        #expect(LiveHeader.title(meta: nil, frame: nil) == nil)
    }

    @Test func theKeyboardRisesOnTheMetaLineWhileFramesAreOffNotOnTheOldPicturesHeader() {
        // frames are off while the VNC screen shows: the meta line says whether a field has focus, the last picture's header is old
        #expect(LiveMessages.parse(#"{"type":"meta","url":"https://a.b/","title":"T","editing":true}"#)
                == .meta(LiveMeta(url: "https://a.b/", title: "T", editing: true)))
        #expect(LiveHeader.editing(meta: LiveMeta(editing: true), frame: false) == true)
        #expect(LiveHeader.editing(meta: LiveMeta(editing: false), frame: true) == false)
        #expect(LiveHeader.editing(meta: nil, frame: true) == true)                  // no meta yet: the picture's own flag
        #expect(LiveHeader.editing(meta: nil, frame: nil) == false)
        // a server from before `editing` leaves it out: the meta says false, the picture's flag does not count
        #expect(LiveMessages.parse(#"{"type":"meta","url":"https://a.b/","title":"T"}"#)
                == .meta(LiveMeta(url: "https://a.b/", title: "T", editing: false)))
        #expect(LiveHeader.editing(meta: LiveMeta(url: "https://a.b/", title: "T"), frame: true) == false)
    }

    @Test func textForThePageComesFromThePhoneClipboardOnlyWhenThereIsSomeAndIsCutAtOneEntry() {
        #expect(ClipboardRules.pasteText(nil) == nil)
        #expect(ClipboardRules.pasteText("") == nil)
        #expect(ClipboardRules.pasteText("  hi \n") == "  hi \n")         // spaces and newlines are kept
        let long = String(repeating: "a", count: LiveMessages.maxText + 500)
        #expect(ClipboardRules.pasteText(long) == String(repeating: "a", count: LiveMessages.maxText))
    }
}

/// Owner decision 2026-11-18: a button hold ends only by hand back; a hand-over hold (Kelo asked for it) gives back when the page is left.
/// Port of android OwnerHoldRulesTest (shared/desktopTest agent/LiveRulesTest.kt).
@Suite struct OwnerHoldRulesTests {
    private func status(_ json: String) -> LiveStatus? {
        if case .status(let s) = LiveMessages.parse(json) { return s }
        return nil
    }

    @Test func aHandoverTakeSaysSoOnlyWhenTakingForAHandover() {
        #expect(LiveMessages.control(take: true, handover: true)["by"] == .string("handover"))
        #expect(LiveMessages.control(take: true)["by"] == nil)
        #expect(LiveMessages.control(take: false, handover: true)["by"] == nil)
    }

    @Test func leavingGivesBackOnlyAHandoverHold() throws {
        let button = try #require(status(#"{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"hold":"button"}"#))
        let handover = try #require(status(#"{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"hold":"handover"}"#))
        let older = try #require(status(#"{"type":"status","controller":"user","controlled_by":"owner","you_control":true}"#))
        #expect(!LiveRules.giveBackOnLeave(button))
        #expect(LiveRules.giveBackOnLeave(handover))
        #expect(!LiveRules.giveBackOnLeave(older))          // an older server: nothing is given back on leaving
        #expect(LiveRules.leaveNotice(button))
        #expect(!LiveRules.leaveNotice(handover))
    }

    @Test func aStaleInputAnswerIsDroppedAndSaysSo() {
        let stale = LiveMessages.parse(#"{"type":"input_ok","input":"tap","ok":false,"busy":false,"stale":true}"#)
        #expect(stale == .inputReply(InputReply(ok: false, stale: true, input: "tap")))
    }
}

private enum LiveTestError: Error { case notStatus }

/// Kelo's work browser (scheduled and background jobs) apart from the owner's own browser. An older server says no browser: main.
@Suite struct WorkBrowserTests {
    private func status(_ json: String) throws -> LiveStatus {
        guard case .status(let s) = LiveMessages.parse(json) else { throw LiveTestError.notStatus }
        return s
    }

    private func tab(_ json: String) throws -> BrowserTab {
        try JSONDecoder().decode(BrowserTab.self, from: Data(json.utf8))
    }

    @Test func aTabSaysWhichBrowserItRunsIn() throws {
        #expect(try tab(#"{"id":"task:4","browser":"work"}"#).inWorkBrowser)
        #expect(try !tab(#"{"id":"chat:1","browser":"main"}"#).inWorkBrowser)
        #expect(try !tab(#"{"id":"chat:1"}"#).inWorkBrowser)   // an older server
    }

    @Test func aStatusSaysWhichBrowserItsTabIsIn() throws {
        let work = try status(#"{"type":"status","browser":"work"}"#)
        #expect(work.inWorkBrowser)
        let main = try status(#"{"type":"status","browser":"main"}"#)
        #expect(!main.inWorkBrowser)
        let old = try status(#"{"type":"status"}"#)
        #expect(!old.inWorkBrowser)
    }

    @Test func theWorkNoteShowsOnlyWhileTheOwnerHoldsAWorkTab() throws {
        let mine = try status(#"{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"browser":"work"}"#)
        #expect(LiveRules.showWorkNote(mine))
        let main = try status(#"{"type":"status","controller":"user","controlled_by":"owner","you_control":true,"browser":"main"}"#)
        #expect(!LiveRules.showWorkNote(main))
        let agent = try status(#"{"type":"status","controller":"agent","you_control":false,"browser":"work"}"#)
        #expect(!LiveRules.showWorkNote(agent))
    }
}
