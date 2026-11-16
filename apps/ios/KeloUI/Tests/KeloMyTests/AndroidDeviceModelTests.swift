import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private func json(configured: Bool = true, connected: Bool = true, model: String = "Pixel 7", version: String = "15",
                  screen: String = #"{"width":720,"height":1280}"#, play: Bool = true, korean: Bool = false) -> String {
    #"{"configured":\#(configured),"connected":\#(connected),"model":"\#(model)","android_version":"\#(version)","screen":\#(screen),"play_store":\#(play),"korean_input":\#(korean),"checked_at":1794744000.0}"#
}

private let unset = json(configured: false, connected: false, model: "", version: "", screen: "null", play: false)

/// 마이 → 휴대폰 기기: the row shows only for a configured device, the status mapping, the check button, the live view key.
@MainActor @Suite struct AndroidDeviceModelTests {
    private func overview(_ t: RoutedTransport) -> MyOverviewModel {
        MyOverviewModel(api: t.api(), cache: memoryCache(), onSignedOut: {}, onHistoryCleared: {})
    }

    @Test func theRowIsHiddenWhenTheServerHasNoDevice() async {
        let t = RoutedTransport()
        t.on("GET /api/android", unset)
        let m = overview(t)
        await m.load()
        #expect(t.count("GET /api/android") == 1)           // one call when My opens
        #expect(m.android != nil)
        #expect(AndroidDeviceRules.shown(m.android) == false)
    }

    @Test func theRowIsHiddenWhenTheReadFailsAndOnAnOlderServer() async {
        let t = RoutedTransport()
        t.on("GET /api/android", 500, #"{"detail":"boom"}"#)
        let m = overview(t)
        await m.load()
        #expect(AndroidDeviceRules.shown(m.android) == false)
        let older = RoutedTransport()                       // no such route: 404
        let m2 = overview(older)
        await m2.load()
        #expect(m2.android == nil)
        #expect(AndroidDeviceRules.shown(m2.android) == false)
    }

    @Test func aConfiguredDeviceShowsTheRowAndMapsItsFields() async {
        let t = RoutedTransport()
        t.on("GET /api/android", json())
        let m = overview(t)
        await m.load()
        #expect(AndroidDeviceRules.shown(m.android))
        let info = m.android!
        #expect(info.connected)
        #expect(info.model == "Pixel 7")
        #expect(info.androidVersion == "15")
        #expect(AndroidDeviceRules.screenSize(info) == "720 × 1280")
        #expect(info.playStore)
        #expect(info.koreanInput == false)
    }

    @Test func aDeviceThatIsNotConnectedKeepsTheRowWithoutFacts() async {
        let t = RoutedTransport()
        t.on("GET /api/android", json(connected: false, model: "", version: "", screen: "null", play: false))
        let model = AndroidDeviceModel(api: t.api())
        await model.load()
        let info = model.info!
        #expect(AndroidDeviceRules.shown(info))
        #expect(info.connected == false)
        #expect(AndroidDeviceRules.screenSize(info) == nil)
        #expect(AndroidDeviceRules.detail(info) == nil)
    }

    @Test func theCheckButtonIsBusyThenTheNewReadingShows() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/android", json(connected: false, model: "", version: "", screen: "null", play: false))
        t.on("POST /api/android/check") { _, _ in await gate.wait(); return (200, json(korean: true)) }
        let model = AndroidDeviceModel(api: t.api())
        await model.load()
        #expect(model.info?.connected == false)

        let first = Task { await model.check() }
        let busy = await eventually { model.checking }
        #expect(busy)
        await model.check()                                 // a second tap while busy does nothing
        await gate.release()
        await first.value
        #expect(t.count("POST /api/android/check") == 1)
        #expect(model.checking == false)
        #expect(model.info?.connected == true)
        #expect(model.info?.koreanInput == true)            // the new reading replaced the old one
    }

    @Test func aFailedCheckEndsBusyAndSaysNotConnected() async {
        let t = RoutedTransport()
        t.on("GET /api/android", json())
        t.on("POST /api/android/check", 500, #"{"detail":"boom"}"#)
        let model = AndroidDeviceModel(api: t.api())
        await model.load()
        await model.check()
        #expect(model.checking == false)
        #expect(AndroidDeviceRules.shown(model.info))
        #expect(model.info?.connected == false)
    }

    @Test func theScreenOpensTheLiveViewWithTheKeyAndroidAndTheReadmeLinkIsTheOneInTheBrief() {
        #expect(AndroidDeviceRules.liveKey == "android")
        #expect(AndroidDeviceRules.readmeUrl == "https://github.com/kelo-chat/kelo#android")
        #expect(LiveKinds.isDevice(AndroidDeviceRules.liveKey))
    }

    @Test func theDetailLineSaysVersionAndSize() {
        let both = AndroidDeviceInfo(configured: true, connected: true, androidVersion: "15", screen: AndroidScreen(width: 720, height: 1280))
        #expect(AndroidDeviceRules.detail(both) == "Android 15 · 720 × 1280")
        let versionOnly = AndroidDeviceInfo(configured: true, connected: true, androidVersion: "15")
        #expect(AndroidDeviceRules.detail(versionOnly) == "Android 15")
    }
}
