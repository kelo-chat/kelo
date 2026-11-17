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
    private let lock = NSLock()
    private var log: [URLRequest] = []
    init(_ status: Int, _ body: String, server: String? = nil) { self.status = status; self.body = body; self.server = server }
    var requests: [URLRequest] { lock.withLock { log } }
    func send(_ request: URLRequest) async throws -> (Int, Data) { let r = try await exchange(request); return (r.status, r.body) }
    func exchange(_ request: URLRequest) async throws -> HTTPReply {
        lock.withLock { log.append(request) }
        return HTTPReply(status: status, body: Data(body.utf8), headers: (server.map { ["x-kelo-server": $0] } ?? [:]).merging(appUpdate.map { ["x-kelo-app-update": $0] } ?? [:]) { a, _ in a })
    }
    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let r = try await exchange(request)
        return (r.status, AsyncThrowingStream { c in for b in r.body { c.yield(b) }; c.finish() })
    }
}

@MainActor private func app(_ t: HTTPTransport, pin: KE2EPin? = nil, notices: AppNoticeStore? = nil) -> AppModel {
    AppModel(store: MemorySessionStore(Session(serverUrl: "https://kelo.test", token: "tok", e2e: pin)),
             prefs: Prefs(defaults: UserDefaults(suiteName: "kelo-gate-\(UUID().uuidString)")!), transport: t,
             cache: ScreenCache(directory: nil, key: { nil }), noticeStore: notices ?? AppNoticeStore(load: { nil }, save: { _ in }))
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
