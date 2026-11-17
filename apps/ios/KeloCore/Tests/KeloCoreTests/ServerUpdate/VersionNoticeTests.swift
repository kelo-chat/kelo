import Testing
import Foundation
@testable import KeloCore

// Android shell/ServerOldUpdateTest.kt and update/AppNoticeTest.kt: docs/version-notices.md parts 2 and 3.
@Suite struct VersionNoticeTests {
    private func st(_ json: String) throws -> ServerUpdateStatus { try JSONDecoder().decode(ServerUpdateStatus.self, from: Data(json.utf8)) }
    private func available() throws -> ServerUpdateStatus { try st(#"{"state":"ok","current":"0.8.0","latest":{"version":"0.10.2"},"update_available":true}"#) }
    private func none() throws -> ServerUpdateStatus { try st(#"{"state":"ok","current":"0.8.0","update_available":false}"#) }
    private func job(_ state: String, step: String = "restart", progress: Int = 50, error: String? = nil) throws -> ServerUpdateStatus {
        try st("""
        {"state":"ok","current":"0.8.0","latest":{"version":"0.10.2"},"update_available":true,
         "job":{"id":"j1","action":"update","state":"\(state)","step":"\(step)","progress":\(progress),"error":\(error.map { "\"\($0)\"" } ?? "null")}}
        """)
    }

    final class Seen: @unchecked Sendable {
        var phases: [ServerOldRules.Phase] = []
    }

    // ---- part 2: the button ----

    @Test func theButtonShowsOnlyWhenTheServerAnswersForThisDevice() async throws {
        let a = try available()
        #expect(await ServerOldRules.probe { .shown(a) } == .ready(version: "0.10.2"))
        #expect(await ServerOldRules.probe { .shown(try! self.none()) } == .ready(version: nil))   // the check runs when it is pressed
        #expect(await ServerOldRules.probe { .hidden } == .manual)                                    // 403 / 404 / unavailable
        #expect(await ServerOldRules.probe { .failed(ServerUpdateFailure(status: ApiError.noResponse, detail: "x")) } == .manual)
        let local = try st(#"{"state":"local","message":"from source"}"#)
        #expect(ServerOldRules.phase(of: local) == .manual)
        if case .running = ServerOldRules.phase(of: try job("running")) {} else { Issue.record("a running update is followed") }
    }

    @Test func anUpdateChecksStartsFollowsAndEndsDone() async throws {
        let seen = Seen()
        let a = try available(), running = try job("running", step: "download", progress: 10), done = try job("done", step: "done", progress: 100)
        let end = await ServerOldRules.run(
            check: { a }, start: { running },
            follow: { _, on in
                await on(ServerUpdateRules.Progress(status: try! self.job("running", step: "restart", progress: 60), reconnecting: true))
                return .finished(done)
            },
            onPhase: { seen.phases.append($0) })
        #expect(end == .done)
        #expect(seen.phases.contains(.running(fraction: 0.1, step: "download", reconnecting: false)))
        #expect(seen.phases.contains(.running(fraction: 0.6, step: "restart", reconnecting: true)))
        #expect(seen.phases.last == .done)
    }

    @Test func nothingNewerARefusalOrAFailedRunIsFailedNotDone() async throws {
        let n = try none(), a = try available(), running = try job("running")
        let noop = await ServerOldRules.run(check: { n }, start: { n }, follow: { s, _ in .finished(s) }, onPhase: { _ in })
        #expect(noop == .failed(detail: nil))
        let refused = await ServerOldRules.run(check: { a }, start: { throw ServerUpdateFailure(status: 409, detail: "an update is already running") },
                                               follow: { s, _ in .finished(s) }, onPhase: { _ in })
        #expect(refused == .failed(detail: "an update is already running"))
        let rolledBack = try job("rolled_back", error: "health check failed")
        let failed = await ServerOldRules.run(check: { a }, start: { running }, follow: { _, _ in .finished(rolledBack) }, onPhase: { _ in })
        #expect(failed == .failed(detail: "health check failed"))
        let gaveUp = await ServerOldRules.run(check: { a }, start: { running }, follow: { s, _ in .gaveUp(s) }, onPhase: { _ in })
        #expect(gaveUp == .failed(detail: nil))
    }

    // ---- part 3: the banner ----

    @Test func theBannerShowsOnlyAnOfferedVersionThatWasNotDismissed() {
        #expect(!AppNoticeRules.show(nil, dismissed: []))
        #expect(!AppNoticeRules.show("  ", dismissed: []))
        #expect(AppNoticeRules.show("0.10.3", dismissed: []))
        #expect(!AppNoticeRules.show("0.10.3", dismissed: ["0.10.3"]))
        #expect(AppNoticeRules.show("0.10.4", dismissed: ["0.10.3"]))   // a newer one comes again
    }

    @Test func dismissedVersionsAreKeptOnTheDevice() {
        final class Box: @unchecked Sendable { var text: String? }
        let box = Box()
        let store = AppNoticeStore(load: { box.text }, save: { box.text = $0 })
        #expect(store.dismissed().isEmpty)
        _ = store.add("0.10.3")
        let after = store.add("0.10.4")
        #expect(after == ["0.10.3", "0.10.4"] && box.text == "0.10.3,0.10.4")
        #expect(AppNoticeStore(load: { box.text }, save: { _ in }).dismissed() == ["0.10.3", "0.10.4"])
    }

    @Test func storeChannelsOpenTheStoreOthersUseTheirOwnUpdater() {
        for c in ["play", "appstore", "msstore"] { #expect(AppNoticeRules.action(channel: c) == .store) }
        for c in ["download", ""] { #expect(AppNoticeRules.action(channel: c) == .updater) }
        #expect(AppNoticeRules.storeURL(appStoreId: "123").absoluteString == "itms-apps://apps.apple.com/app/id123")
        #expect(Versioning.channel(receiptName: "receipt") == "appstore")
        #expect(Versioning.channel(receiptName: "sandboxReceipt") == "" && Versioning.channel(receiptName: nil) == "")
    }

    private struct HeaderTransport: HTTPTransport {
        let headers: [String: String]
        func send(_ request: URLRequest) async throws -> (Int, Data) { (200, Data("{}".utf8)) }
        func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) { (200, AsyncThrowingStream { $0.finish() }) }
        func exchange(_ request: URLRequest) async throws -> HTTPReply { HTTPReply(status: 200, body: Data("{}".utf8), headers: headers) }
    }

    final class Got: @unchecked Sendable { var values: [String?] = [] }

    @Test func theChannelGoesOutAndANewerAppComesBackOnAboutAndProfileOnly() async throws {
        let got = Got()
        let t = HeaderTransport(headers: ["x-kelo-app-update": " 0.10.3 "])
        let api = KeloApi(baseUrl: "https://k.example", token: "x", transport: t, acceptLanguage: { "" }, appHeader: "ios/0.10.0+5",
                          channelHeader: "appstore", onAppUpdate: { got.values.append($0) })
        let r = try api.makeRequest("/api/about")
        #expect(r.value(forHTTPHeaderField: "X-Kelo-App-Channel") == "appstore")
        _ = try await api.request("GET", "/api/about")
        _ = try await api.request("GET", "/api/goals")        // another call says nothing about it
        _ = try await api.request("GET", "/api/profile")
        #expect(got.values == ["0.10.3", "0.10.3"])
        let quiet = KeloApi(baseUrl: "https://k.example", token: "x", transport: HeaderTransport(headers: [:]), acceptLanguage: { "" },
                            onAppUpdate: { got.values.append($0) })
        got.values = []
        _ = try await quiet.request("GET", "/api/profile")     // no header on the profile: no newer app any more
        #expect(got.values == [nil])
        let about = try JSONDecoder().decode(About.self, from: Data(#"{"version":"0.10.2","build":"18","app_update":"0.10.3"}"#.utf8))
        #expect(about.appUpdate == "0.10.3")
    }
}

/// The launch/foreground popup for a newer app (owner 2026-11-16): at most once in 24 hours, whatever the answer.
@Suite struct AppPopupTests {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    func ago(_ h: Double) -> Date { now.addingTimeInterval(-h * 3600) }

    @Test func neverShownBeforeShowsForANamedVersion() {
        #expect(AppNoticeRules.popup("0.10.3", lastShown: nil, now: now))
    }

    @Test func nothingNamedMeansTheAppIsOnTheNewestVersion() {
        #expect(!AppNoticeRules.popup(nil, lastShown: nil, now: now))
        #expect(!AppNoticeRules.popup(" ", lastShown: ago(100), now: now))
    }

    @Test func shown23HoursAgoWaits() { #expect(!AppNoticeRules.popup("0.10.3", lastShown: ago(23), now: now)) }

    @Test func shown25HoursAgoShowsAgain() {
        #expect(AppNoticeRules.popup("0.10.3", lastShown: ago(25), now: now))
        #expect(AppNoticeRules.popup("0.10.3", lastShown: ago(24), now: now))
    }

    @Test func aNewerVersionThanTheOneShownStillWaitsForTheDay() {
        // the last popup (inside 24 h) was about 0.10.3; 0.10.4 is out now: nothing is remembered per version
        #expect(!AppNoticeRules.popup("0.10.4", lastShown: ago(2), now: now))
        #expect(AppNoticeRules.popup("0.10.4", lastShown: ago(30), now: now))
    }

    @Test func theServerIsAskedAgainAfterMoreThanAnHourAway() {
        #expect(!AppNoticeRules.recheck(away: 3600))
        #expect(AppNoticeRules.recheck(away: 3601))
    }

    @Test func storeKeepsTheLastShownTime() {
        final class Box: @unchecked Sendable { var v: String? }
        let b = Box()
        let store = AppNoticeStore(load: { nil }, save: { _ in }, loadShown: { b.v }, saveShown: { b.v = $0 })
        #expect(store.lastShown() == nil)
        store.shown(now: now)
        #expect(store.lastShown() == now)
    }
}
