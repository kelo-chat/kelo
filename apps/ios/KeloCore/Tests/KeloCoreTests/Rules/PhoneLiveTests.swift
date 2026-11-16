import Foundation
import Testing
@testable import KeloCore

/// The Android device as a live source (the key "android") and its phone card: the device event, the live view's kinds and keys.
@Suite struct PhoneLiveTests {
    @Test func aDeviceEventIsParsedWithItsKeyAndOnlyDataPictures() throws {
        let ev = TurnEvent.parse(try JSON.parse(#"{"type":"device","id":"c1","key":"android","status":"휴대폰에서 누르는 중","image":"data:image/jpeg;base64,AQ==","width":360,"height":780,"bytes":3,"at":1}"#))
        guard case .device(let d) = ev else { Issue.record("not a device card: \(ev)"); return }
        #expect(d.key == "android" && d.status == "휴대폰에서 누르는 중" && d.image == "data:image/jpeg;base64,AQ==")
        #expect(d.width == 360 && d.height == 780)
        guard case .device(let bare) = TurnEvent.parse(try JSON.parse(#"{"type":"device","id":"c2","status":"s","image":"https://x/y.jpg"}"#)) else {
            Issue.record("not a device card"); return
        }
        #expect(bare.image == nil && bare.key == "android")   // the key defaults; a picture that is not a data: URL is left out
    }

    @Test func theStatusLineAndTheMoodFollowThePhoneLikeTheBrowser() {
        let ev = TurnEvent.device(.init(id: "c1", status: "휴대폰에 입력하는 중"))
        #expect(AgentActivityRules.next(nil, ev) == "휴대폰에 입력하는 중")
        #expect(AgentActivityRules.next("keep", .device(.init(id: "c2", status: ""))) == "keep")
        #expect(AgentActivityRules.turnKind(.thinking, ev) == .browser)
        #expect(AgentActivityRules.toolKind("android_tap") == .browser)
    }

    @Test func onlyTheKeyAndroidIsTheDevice() {
        #expect(LiveKinds.isDevice("android"))
        #expect(!LiveKinds.isDevice("owner") && !LiveKinds.isDevice("task:w1") && !LiveKinds.isDevice("3"))
    }

    @Test func theThreeKeysAreKeyEventsInTheServersWords() {
        #expect(LiveKinds.phoneKeys == ["back", "home", "recents"])
        for k in LiveKinds.phoneKeys {
            let m = LiveMessages.deviceKey(k)
            #expect(m["type"]?.stringValue == "key" && m["key"]?.stringValue == k)
        }
    }

    @Test func aDragIsOneSwipeInFramePixelsWithItsDuration() {
        let m = LiveMessages.swipe(from: FramePoint(x: 180, y: 600), to: FramePoint(x: 180, y: 100), frameW: 360, frameH: 780, ms: 250)
        #expect(m["type"]?.stringValue == "swipe")
        #expect([m["x1"], m["y1"], m["x2"], m["y2"], m["ms"], m["frame_w"], m["frame_h"]].map { $0?.intValue } == [180, 600, 180, 100, 250, 360, 780])
        #expect(LiveKinds.swipeMs(5) == 120 && LiveKinds.swipeMs(300) == 300 && LiveKinds.swipeMs(9000) == 800)
    }

    @Test func aTapOnThePortraitFrameIsMappedThroughTheLetterbox() {
        // a 360 x 780 frame in a 400 x 800 view
        let p = LiveGeometry.toFrame(x: 200, y: 400, viewW: 400, viewH: 800, frameW: 360, frameH: 780)
        #expect(p == FramePoint(x: 180, y: 390))
    }
}
