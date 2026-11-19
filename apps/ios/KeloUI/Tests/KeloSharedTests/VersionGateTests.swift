import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared

/// A server that answers every call with `status` and `body`, and `X-Kelo-Server: server` when given.
private final class GateTransport: HTTPTransport, @unchecked Sendable {
    let status: Int, body: String, server: String?
    /// `X-Kelo-App-Update` answered on /api/profile
    var appUpdate: String?
    /// `X-Kelo-Server-Update` answered on /api/profile
    var serverUpdate: String?
    private let lock = NSLock()
    private var log: [URLRequest] = []
    init(_ status: Int, _ body: String, server: String? = nil) { self.status = status; self.body = body; self.server = server }
    var requests: [URLRequest] { lock.withLock { log } }
    func send(_ request: URLRequest) async throws -> (Int, Data) { let r = try await exchange(request); return (r.status, r.body) }
    func exchange(_ request: URLRequest) async throws -> HTTPReply {
        lock.withLock { log.append(request) }
        var h = server.map { ["x-kelo-server": $0] } ?? [:]
        if let v = appUpdate { h["x-kelo-app-update"] = v }
        if let v = serverUpdate { h["x-kelo-server-update"] = v }
        return HTTPReply(status: status, body: Data(body.utf8), headers: h)
    }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let r = try await exchange(request)
        return (r.status, AsyncThrowingStream { c in for b in r.body { c.yield(b) }; c.finish() })
    }
}

@MainActor private func app(_ t: HTTPTransport, pin: KE2EPin? = nil, notices: AppNoticeStore? = nil, storeLookup: AppStoreLookup? = nil, running: String? = nil) -> AppModel {
    AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok", e2e: pin)),
             prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-gate-\(UUID().uuidString)")!), transport: t,
             cache: ScreenCache(directory: nil, key: { nil }), noticeStore: notices ?? AppNoticeStore(load: { nil }, save: { _ in }),
             storeLookup: storeLookup, runningVersion: running)
}

@MainActor private func until(_ cond: () -> Bool) async -> Bool {
    for _ in 0..<300 { if cond() { return true }; try? await Task.sleep(nanoseconds: 10_000_000) }
    return cond()
}

@Suite(.serialized) struct VersionGateTests {
    @MainActor @Test func aServerThatSaysTheAppIsTooOldCoversTheApp() async {
        let a = app(GateTransport(426, #"{"code": "app_too_old", "detail": "Update", "min_app": "0.9.1"}"#, server: "0.9.1+2"))
        #expect(await until { a.appTooOld })
        #expect(a.serverTooOld == nil)
    }

    @MainActor @Test func aServerOlderThanTheAppNeedsShowsTheUpdateScreen() async {
        let a = app(GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "0.8.0+9"))
        #expect(await until { a.serverTooOld == "0.8.0+9" })
        // a server without the header (older than 0.9.0) is not judged by it
        let b = app(GateTransport(200, #"{"agent_name": "Kelo"}"#))
        #expect(await until { b.profile != nil })
        #expect(b.serverTooOld == nil)
    }

    @MainActor @Test func everyCallSaysWhichAppItIs() async {
        let t = GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "0.9.0+1")
        let a = app(t)
        #expect(await until { a.profile != nil })
        let header = t.requests.first?.value(forHTTPHeaderField: "X-Kelo-App") ?? ""
        #expect(header.hasPrefix("ios/\(KeloVersion.version)"))
    }

    @MainActor @Test func aNewerCompatibleAppShowsOncePerVersionAndStaysClosedOnThisPhone() async {
        let t = GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "0.10.2+18")
        t.appUpdate = "0.10.3"
        final class Saved: @unchecked Sendable { var text: String? }
        let saved = Saved()
        let store = AppNoticeStore(load: { saved.text }, save: { saved.text = $0 })
        let a = app(t, notices: store)
        #expect(await until { a.appNotice == "0.10.3" })
        a.dismissAppNotice("0.10.3")
        #expect(a.appNotice == nil && saved.text == "0.10.3")
        // the same version does not come back after a restart; a newer one does
        let b = app(t, notices: store)
        #expect(await until { b.appUpdate == "0.10.3" })
        #expect(b.appNotice == nil)
        t.appUpdate = "0.10.4"
        b.retryProfile()
        #expect(await until { b.appNotice == "0.10.4" })
        b.signOut()
        #expect(b.appUpdate == nil)
    }

    @MainActor @Test func anAppStoreBuildShowsANewerAppOnlyWhenTheStoreSaysNewer() async {
        // the store names 1.0.3 (this build 1.0.2) and the server names 1.0.3: the banner and the popup
        let t = GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "1.0.2+26111613")
        t.appUpdate = "1.0.3"
        let newer = app(t, storeLookup: AppStoreLookup { "1.0.3" }, running: "1.0.2")
        #expect(await until { newer.appUpdate == "1.0.3" })
        let now = Date()
        await newer.checkStore(now: now)
        #expect(newer.appNotice == "1.0.3")
        #expect(newer.appPopup(now: now) == "1.0.3")
        // the store names this build's own version: nothing, although the server names one
        let same = app(t, storeLookup: AppStoreLookup { "1.0.2" }, running: "1.0.2")
        #expect(await until { same.appUpdate == "1.0.3" })
        await same.checkStore(now: now)
        #expect(same.appNotice == nil)
        #expect(same.appPopup(now: now) == nil)
        // the store has not answered yet: nothing
        let unknown = app(t, storeLookup: AppStoreLookup { nil }, running: "1.0.2")
        #expect(await until { unknown.appUpdate == "1.0.3" })
        await unknown.checkStore(now: now)
        #expect(unknown.appPopup(now: now) == nil)
    }

    @MainActor @Test func aStoreAnswerNamedNoNewerNameOnTheServerShowsNothing() async {
        // the server names none (its signed store row needs a newer server): nothing, even though the store is newer
        let t = GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "0.9.0+1")
        let a = app(t, storeLookup: AppStoreLookup { "1.0.3" }, running: "1.0.2")
        #expect(await until { a.profile != nil })
        await a.checkStore()
        #expect(a.appNotice == nil)
        #expect(a.appPopup() == nil)
    }

    @MainActor @Test func theStoreIsAskedOnlyEverySixHours() async {
        final class Asks: @unchecked Sendable { var count = 0 }
        let asks = Asks()
        final class Saved: @unchecked Sendable { var text: String? }
        let saved = Saved()
        let store = AppNoticeStore(load: { nil }, save: { _ in }, loadStoreLookup: { saved.text }, saveStoreLookup: { saved.text = $0 })
        let lookup = AppStoreLookup { asks.count += 1; return "1.0.3" }
        let now = Date()
        let a = app(GateTransport(200, #"{"agent_name": "Kelo"}"#), notices: store, storeLookup: lookup, running: "1.0.2")
        await a.checkStore(now: now)
        await a.checkStore(now: now.addingTimeInterval(3600))
        #expect(asks.count == 1)
        #expect(a.storeVersion == "1.0.3")
        await a.checkStore(now: now.addingTimeInterval(6 * 3600))
        #expect(asks.count == 2)
        // a restart keeps the cached answer and waits too
        let b = app(GateTransport(200, #"{"agent_name": "Kelo"}"#), notices: store, storeLookup: lookup, running: "1.0.2")
        #expect(b.storeVersion == "1.0.3")
        await b.checkStore(now: now.addingTimeInterval(7 * 3600))
        #expect(asks.count == 2)
    }

    @MainActor @Test func aBuildThatIsNotFromTheStoreIsNotGated() async {
        // no store lookup (TestFlight, sideload): the server's answer alone decides, as before
        let t = GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "0.10.2+18")
        t.appUpdate = "0.10.3"
        let a = app(t)
        #expect(await until { a.appUpdate == "0.10.3" })
        #expect(a.appPopup() == "0.10.3")
    }

    @MainActor @Test func serverPopupWaitsForTheAppPopupAndADay() async {
        let t = GateTransport(200, #"{"agent_name": "Kelo"}"#, server: "0.10.5+3")
        t.serverUpdate = "0.10.6"
        let a = app(t)
        #expect(await until { a.serverUpdate == "0.10.6" })
        let now = Date()
        #expect(a.serverPopup(now: now) == "0.10.6")
        a.serverPopupAnswered(now: now)
        #expect(a.serverPopup(now: now) == nil)
        #expect(a.serverPopup(now: now.addingTimeInterval(24 * 3600 - 60)) == nil)
        #expect(a.serverPopup(now: now.addingTimeInterval(24 * 3600)) == "0.10.6")
        // an app popup answered 30 minutes ago holds the server notice back for an hour
        let b = app(t)
        #expect(await until { b.serverUpdate == "0.10.6" })
        b.popupAnswered(now: now)
        #expect(b.serverPopup(now: now.addingTimeInterval(30 * 60)) == nil)
        #expect(b.serverPopup(now: now.addingTimeInterval(61 * 60)) == "0.10.6")
        // a newer app is due now: the server notice waits for the app popup
        let c = app(t)
        #expect(await until { c.serverUpdate == "0.10.6" })
        t.appUpdate = "0.10.7"
        c.retryProfile()
        #expect(await until { c.appUpdate == "0.10.7" })
        #expect(c.appPopup(now: now) == "0.10.7")
        #expect(c.serverPopup(now: now) == nil)
        c.signOut()
        #expect(c.serverUpdate == nil)
    }

    @MainActor @Test func aChangedKeyAsksToPairAgain() async {
        let t = GateTransport(409, #"{"detail": "pair again", "code": "e2e_key_changed"}"#)
        let k = try! KE2EServerKey(publicKey: Data(base64Encoded: "OFJ52Ces7PFBXbc107iFdZtEkPYt3bDrO1FM3Msw01k=")!)
        let a = app(t, pin: KE2EPin(key: k, verified: true))
        #expect(await until { a.pairAgain })
        #expect(a.e2ePin?.key == k)
        // only /e2e went out: no plaintext call around the encryption
        #expect(t.requests.allSatisfy { $0.url?.path == "/e2e" })
        a.signOut()
        #expect(!a.pairAgain && a.e2ePin == nil)
    }
}
