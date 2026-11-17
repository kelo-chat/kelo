import Testing
import Foundation
@testable import KeloCore

/// Take-over touch rules (brief takeover-touch-1117, shared protocol): live_v 2, view, frame_seq, mapped_with, picture-box pan clamp,
/// the inverse mapping for touch marks. Vectors mirror the Kotlin LiveGeometry/LiveMessages tests of the shared run.
@Suite struct LiveTouchTests {
    private func near(_ a: Double, _ b: Double) -> Bool { abs(a - b) < 0.001 }

    @Test func frameHeaderReadsTheNewFlags() {
        guard case .frame(let f) = LiveMessages.parse(#"{"type":"frame","seq":9,"w":360,"h":780,"live_v":2,"editing":true,"unknown":1}"#) else { Issue.record("not a frame"); return }
        #expect(f.liveV == 2 && f.editing && f.seq == 9)
        guard case .frame(let old) = LiveMessages.parse(#"{"type":"frame","seq":1,"w":2,"h":2}"#) else { Issue.record("not a frame"); return }
        #expect(old.liveV == 0 && !old.editing)
    }

    @Test func viewMessageCarriesPhysicalSizeAndDensity() {
        let m = LiveMessages.view(width: 1080, height: 1800, density: 3)
        #expect(m["type"]?.stringValue == "view")
        #expect(m["width"] == .number(1080) && m["height"] == .number(1800) && m["density"] == .number(3))
    }

    @Test func frameSeqIsAddedOnlyForAV2Server() {
        let tap = LiveMessages.tap(FramePoint(x: 1, y: 2), frameW: 10, frameH: 10)
        #expect(LiveMessages.stamp(tap, frameSeq: 7, liveV: 2)["frame_seq"] == .number(7))
        #expect(LiveMessages.stamp(tap, frameSeq: 7, liveV: 0)["frame_seq"] == nil)
        #expect(LiveMessages.stamp(tap, frameSeq: 7, liveV: 1)["frame_seq"] == nil)
        #expect(LiveMessages.stamp(tap, frameSeq: nil, liveV: 2)["frame_seq"] == nil)
        #expect(LiveMessages.stamp(tap, frameSeq: 0, liveV: 2)["frame_seq"] == nil)
        // an explicit seq is never overwritten
        let own = LiveMessages.stamp(tap, frameSeq: 3, liveV: 2)
        #expect(LiveMessages.stamp(own, frameSeq: 9, liveV: 2)["frame_seq"] == .number(3))
        // every kind of input can carry it
        for e in [LiveMessages.key("Enter"), LiveMessages.text("a"), LiveMessages.scroll(FramePoint(x: 0, y: 5), at: nil, frameW: 1, frameH: 1),
                  LiveMessages.swipe(from: FramePoint(x: 0, y: 0), to: FramePoint(x: 1, y: 1), frameW: 2, frameH: 2, ms: 100),
                  LiveMessages.longPress(FramePoint(x: 0, y: 0), frameW: 1, frameH: 1)] {
            #expect(LiveMessages.stamp(e, frameSeq: 4, liveV: 2)["frame_seq"] == .number(4))
        }
    }

    @Test func ackedAsksForTheAnswer() {
        #expect(LiveMessages.acked(LiveMessages.tap(FramePoint(x: 1, y: 1), frameW: 2, frameH: 2))["ack"] == .bool(true))
    }

    @Test func theAnswerToAnInputIsParsedAndUnknownFieldsAreIgnored() {
        let r = LiveMessages.parse(#"{"type":"input_ok","input":"tap","ok":true,"x":120.5,"y":40,"busy":false,"mapped_with":"frame","future":{"a":1}}"#)
        #expect(r == .inputReply(InputReply(ok: true, busy: false, x: 120.5, y: 40, mappedWith: "frame")))
        let b = LiveMessages.parse(#"{"type":"input_ok","ok":false,"busy":true,"mapped_with":"latest"}"#)
        #expect(b == .inputReply(InputReply(ok: false, busy: true, mappedWith: "latest")))
        #expect(InputReply(json: ["busy": .bool(true), "ok": .bool(false)]).busy)
    }

    @Test func viewIsReportedOnlyWhenControllingToAV2ServerAndOnlyOnChange() throws {
        var r = ViewReporter()
        #expect(r.offer(widthPoints: 400, heightPoints: 700, scale: 3, liveV: 2, controlling: false) == nil)   // watching
        #expect(r.offer(widthPoints: 400, heightPoints: 700, scale: 3, liveV: 0, controlling: true) == nil)    // older server
        let firstOffer = r.offer(widthPoints: 400, heightPoints: 700, scale: 3, liveV: 2, controlling: true)
        let first = try #require(firstOffer)
        #expect(first["width"] == .number(1200) && first["height"] == .number(2100) && first["density"] == .number(3))
        #expect(r.offer(widthPoints: 400, heightPoints: 700, scale: 3, liveV: 2, controlling: true) == nil)    // same size
        #expect(r.offer(widthPoints: 400, heightPoints: 420, scale: 3, liveV: 2, controlling: true) != nil)    // keyboard opened
        #expect(r.offer(widthPoints: 700, heightPoints: 400, scale: 3, liveV: 2, controlling: true) != nil)    // rotation
        r.reset()
        #expect(r.offer(widthPoints: 700, heightPoints: 400, scale: 3, liveV: 2, controlling: true) != nil)    // a new take-over reports again
        #expect(r.offer(widthPoints: 0, heightPoints: 400, scale: 3, liveV: 2, controlling: true) == nil)
    }

    @Test func aViewOfferBeforeTheServerSaysV2IsNotLost() {
        var r = ViewReporter()
        #expect(r.offer(widthPoints: 400, heightPoints: 700, scale: 2, liveV: 0, controlling: true) == nil)
        #expect(r.offer(widthPoints: 400, heightPoints: 700, scale: 2, liveV: 2, controlling: true) != nil)
    }

    @Test func panStaysInsideThePictureNotTheView() {
        // 720x450 picture in a 360x800 view: drawn 360x225, letterboxed. At 2x it is 720 wide: room sideways only.
        let a = LiveGeometry.clampPan(panX: 500, panY: 500, zoom: 1, viewW: 360, viewH: 800, frameW: 720, frameH: 450)
        #expect(a.0 == 0 && a.1 == 0)
        let b = LiveGeometry.clampPan(panX: 500, panY: 500, zoom: 2, viewW: 360, viewH: 800, frameW: 720, frameH: 450)
        #expect(b.0 == 180)       // (720 - 360) / 2
        #expect(b.1 == 0)         // the zoomed picture (450) is still shorter than the view (800): no blank bars panned in
        // a picture that fills the view behaves like the view-based clamp
        let c = LiveGeometry.clampPan(panX: 900, panY: -900, zoom: 2, viewW: 400, viewH: 800, frameW: 400, frameH: 800)
        #expect(c.0 == 200 && c.1 == -400)
        let z = LiveGeometry.clampPan(panX: 5, panY: 5, zoom: 2, viewW: 0, viewH: 0, frameW: 1, frameH: 1)
        #expect(z.0 == 0 && z.1 == 0)
    }

    @Test func frameToViewUndoesToFrameWithZoomAndPan() throws {
        let p = FramePoint(x: 300, y: 200)
        let v = try #require(LiveGeometry.frameToView(p, viewW: 360, viewH: 800, frameW: 720, frameH: 450, zoom: 2, panX: 30, panY: -20))
        let back = try #require(LiveGeometry.toFrame(x: v.x, y: v.y, viewW: 360, viewH: 800, frameW: 720, frameH: 450, zoom: 2, panX: 30, panY: -20))
        #expect(back == p)
        let c = try #require(LiveGeometry.frameToView(FramePoint(x: 499, y: 499), viewW: 1000, viewH: 1000, frameW: 1000, frameH: 1000))
        #expect(near(c.x, 499.5) && near(c.y, 499.5))
        #expect(LiveGeometry.frameToView(p, viewW: 0, viewH: 0, frameW: 1, frameH: 1) == nil)
    }

    @Test func aTouchesOutcome() {
        let p = FramePoint(x: 5, y: 6)
        #expect(TouchOutcome.of(mapped: p, reply: nil) == .sent(p))
        #expect(TouchOutcome.of(mapped: p, reply: InputReply(mappedWith: "frame")) == .sent(p))
        #expect(TouchOutcome.of(mapped: nil, reply: nil) == .outsidePicture)
        #expect(TouchOutcome.of(mapped: p, reply: InputReply(ok: false, busy: true)) == .notReached(p))
        #expect(TouchOutcome.of(mapped: p, reply: InputReply(ok: false)) == .notReached(p))
        #expect(!TouchOutcome.sent(p).missed && TouchOutcome.outsidePicture.missed && TouchOutcome.notReached(nil).missed)
    }

    @Test func slopDecidesStillnessAndHold() {
        #expect(TouchRules.isStill(dx: 4, dy: -9))
        #expect(!TouchRules.isStill(dx: 11, dy: 0))
        #expect(TouchRules.isLongPress(heldSeconds: 0.5) && !TouchRules.isLongPress(heldSeconds: 0.49))
    }
}
