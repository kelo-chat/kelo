import Foundation
import CryptoKit
import Testing
@testable import KeloCore

/// A Kelo server in the test process, as src/assistant/api/e2e_gate.py answers: /e2e opened with its key (0x01 and 0x02), the inner
/// request handed to `route`, the answer sealed; plaintext calls go to `plain`. Every request is kept, so a test can prove what
/// crossed the wire.
final class FakeE2EServer: HTTPTransport, @unchecked Sendable {
    struct Inner { var head: KE2ERequestHead; var body: Data }
    let server = TestServer()
    let lock = NSLock()
    private var _wire: [URLRequest] = []
    private var _inner: [Inner] = []
    /// The inner request -> (status, headers, body pieces: one frame each).
    var route: @Sendable (Inner) -> (Int, [String: String], [Data]) = { _ in (200, ["content-type": "application/json"], [Data("{}".utf8)]) }
    /// A plaintext request -> (status, body).
    var plain: @Sendable (URLRequest) -> (Int, Data) = { _ in (404, Data(#"{"detail":"Not Found"}"#.utf8)) }
    var offersKE2E = true
    /// Refuse the next sealed call with a clock answer carrying this `now` (spec 5.13).
    var clockRefusals: [Int64] = []
    var cutLastFrame = false

    var wire: [URLRequest] { lock.withLock { _wire } }
    var inner: [Inner] { lock.withLock { _inner } }
    var key: KE2EServerKey { server.key }

    func handle(_ r: URLRequest, upload: Data? = nil) throws -> (Int, Data) {
        lock.withLock { _wire.append(r) }
        let path = r.url?.path ?? ""
        if path == KE2E.infoPath {
            guard offersKE2E else { return (404, Data(#"{"detail":"Not Found"}"#.utf8)) }
            let k = server.key
            return (200, Data(#"{"v": 1, "kid": "\#(k.kid)", "pk": "\#(k.base64url)", "fingerprint": "\#(k.fingerprint)"}"#.utf8))
        }
        guard path == KE2E.path else { return plain(r) }
        guard offersKE2E else { return (404, Data(#"{"detail":"Not Found"}"#.utf8)) }
        let outer = upload ?? r.httpBody ?? Data()
        let env = try KE2EEnvelope.parse(outer)
        guard env.kid == server.key.kidBytes else { return (409, Data(#"{"detail": "pair again", "code": "e2e_key_changed"}"#.utf8)) }
        var rec = try HPKE.Recipient(privateKey: server.sk, ciphersuite: KE2E.ciphersuite, info: KE2E.info(kid: server.key.kid), encapsulatedKey: env.enc)
        var (head, body) = try KE2EInner.split(try rec.open(env.ciphertext, authenticating: KE2E.requestAAD))
        if env.version == KE2E.envelopeVersionStream {
            var reader = KE2EFrameStreamReader(key: try rec.exportSecret(context: KE2E.requestBodyLabel, outputByteCount: 32), aad: KE2E.requestAAD)
            body = try reader.feed(env.bodyFrames).reduce(Data(), +)
            try reader.finish()
        }
        var w = KE2EResponseWriter(key: try rec.exportSecret(context: KE2E.responseLabel, outputByteCount: 32))
        if let now = lock.withLock({ clockRefusals.isEmpty ? nil : clockRefusals.removeFirst() }) {
            return (200, try w.whole(head: KE2EResponseHead(status: 400, headers: ["content-type": "application/json"]),
                                     body: Data(#"{"code": "e2e_clock", "now": \#(now)}"#.utf8)))
        }
        let request = Inner(head: head, body: body)
        lock.withLock { _inner.append(request) }
        let (status, headers, pieces) = route(request)
        var out = try w.frame(KE2EResponseHead(status: status, headers: headers).json(), last: pieces.isEmpty)
        for (i, p) in pieces.enumerated() { out += try w.frame(p, last: i == pieces.count - 1) }
        if cutLastFrame { out = out.dropLast(3) }
        return (200, out)
    }

    func send(_ request: URLRequest) async throws -> (Int, Data) { try handle(request) }

    func stream(_ request: URLRequest) async throws -> (Int, AsyncThrowingStream<UInt8, Error>) {
        let (s, d) = try handle(request)
        return (s, AsyncThrowingStream { c in for b in d { c.yield(b) }; c.finish() })
    }

    func upload(_ request: URLRequest, body: HTTPUploadBody, progress: (@Sendable (Int64, Int64) -> Void)?) async throws -> HTTPReply {
        let data: Data
        switch body {
        case .data(let d): data = d
        case .file(let u): data = try Data(contentsOf: u)
        }
        progress?(Int64(data.count), Int64(data.count))
        let (s, d) = try handle(request, upload: data)
        return HTTPReply(status: s, body: d)
    }

    func api(pin: KE2EPin?, token: String = "tok-secret-1", onFailure: @escaping @Sendable (ApiError) -> Void = { _ in },
             onServerVersion: @escaping @Sendable (String?) -> Void = { _ in }, onPin: @escaping @Sendable (KE2EPin) -> Void = { _ in }) -> KeloApi {
        let state = KE2EState(pin: pin, onPin: onPin)
        return KeloApi(baseUrl: "https://kelo.test", token: token, transport: KE2ETransport(base: self, baseUrl: "https://kelo.test", state: state),
                       acceptLanguage: { "ko" }, appHeader: "ios/0.9.0+57", onFailure: onFailure, onServerVersion: onServerVersion)
    }
}

@Suite struct KE2ETransportTests {
    @Test func aPinnedCallGoesOnlyThroughE2E() async throws {
        let s = FakeE2EServer()
        s.route = { _ in (200, ["content-type": "application/json", "x-kelo-server": "0.9.0+1"], [Data(#"{"agent_name": "Kelo", "user_name": "민지"}"#.utf8)]) }
        let versions = Box<[String?]>([])
        let api = s.api(pin: KE2EPin(key: s.key, verified: true), onServerVersion: { v in versions.update { $0.append(v) } })
        let p: Profile = try await api.get("/api/profile?x=1")
        #expect(p.agentName == "Kelo" && p.userName == "민지")
        // on the wire: one POST /e2e, no token, no path, no query
        let w = try #require(s.wire.first)
        #expect(s.wire.count == 1 && w.url?.path == "/e2e" && w.url?.query == nil && w.httpMethod == "POST")
        #expect(w.value(forHTTPHeaderField: "Authorization") == nil && w.value(forHTTPHeaderField: "Content-Type") == KE2E.contentType)
        #expect(w.httpBody?.range(of: Data("tok-secret-1".utf8)) == nil && w.httpBody?.range(of: Data("/api/profile".utf8)) == nil)
        // inside: the route, the token, the language, the app version
        let inner = try #require(s.inner.first)
        #expect(inner.head.method == "GET" && inner.head.path == "/api/profile?x=1")
        #expect(inner.head.headers["authorization"] == "Bearer tok-secret-1")
        #expect(inner.head.headers["accept-language"] == "ko" && inner.head.headers["x-kelo-app"] == "ios/0.9.0+57")
        // the server version from the answer head reaches the app
        #expect(versions.value == ["0.9.0+1"])
    }

    @Test func innerErrorsAreTheServersErrors() async throws {
        let s = FakeE2EServer()
        s.route = { _ in (426, ["content-type": "application/json"], [Data(#"{"code": "app_too_old", "detail": "Update Kelo", "min_app": "0.9.1"}"#.utf8)]) }
        let seen = Box<[ApiError]>([])
        let api = s.api(pin: KE2EPin(key: s.key, verified: true), onFailure: { e in seen.update { $0.append(e) } })
        await #expect(throws: ApiError.self) { _ = try await api.request("GET", "/api/profile") }
        #expect(seen.value.first?.status == 426 && seen.value.first?.code == "app_too_old" && seen.value.first?.detail == "Update Kelo")
    }

    @Test func aCutOffAnswerIsAnError() async throws {
        let s = FakeE2EServer()
        s.cutLastFrame = true
        s.route = { _ in (200, ["content-type": "application/json"], [Data("{}".utf8)]) }
        let api = s.api(pin: KE2EPin(key: s.key, verified: true))
        await #expect(throws: ApiError.self) { _ = try await api.request("GET", "/api/profile") }
    }

    @Test func aChangedKeyMeansPairAgainAndNothingInPlaintext() async throws {
        let s = FakeE2EServer()
        let seen = Box<[ApiError]>([])
        let api = s.api(pin: KE2EPin(key: TestServer().key, verified: true), onFailure: { e in seen.update { $0.append(e) } })
        do { _ = try await api.request("GET", "/api/profile"); Issue.record("no error") } catch let e as ApiError {
            #expect(e.isKeyChanged && e.status == 409)
        }
        #expect(seen.value.first?.isKeyChanged == true)
        #expect(s.wire.allSatisfy { $0.url?.path == "/e2e" })
    }

    @Test func aPinnedServerWithoutE2EIsNotUsedInPlaintext() async throws {
        let s = FakeE2EServer()
        let pin = KE2EPin(key: s.key, verified: true)
        s.offersKE2E = false
        s.plain = { _ in (200, Data(#"{"agent_name": "plain"}"#.utf8)) }
        let api = s.api(pin: pin)
        do { _ = try await api.request("GET", "/api/profile"); Issue.record("no error") } catch let e as ApiError {
            #expect(e.code == KE2ETransport.unsupportedCode)
        }
        #expect(s.wire.count == 1 && s.wire[0].url?.path == "/e2e")
    }

    @Test func theClockIsCorrectedOnceAndTheCallSentAgain() async throws {
        let s = FakeE2EServer()
        let serverNow = Int64(Date().timeIntervalSince1970) + 1000
        s.clockRefusals = [serverNow]
        let api = s.api(pin: KE2EPin(key: s.key, verified: true))
        _ = try await api.request("GET", "/api/profile")
        #expect(s.wire.count == 2)
        let ts = try #require(s.inner.first?.head.ts)
        #expect(abs(ts - serverNow) < 5)
        // twice in a row: the second refusal is the answer
        s.clockRefusals = [serverNow + 5000, serverNow + 9000]
        await #expect(throws: ApiError.self) { _ = try await api.request("GET", "/api/profile") }
    }

    @Test func anUnpinnedSessionTakesTheKeyFirstAndNeverSendsInTheClear() async throws {
        let s = FakeE2EServer()
        let pins = Box<[KE2EPin]>([])
        let api = s.api(pin: nil, onPin: { p in pins.update { $0.append(p) } })
        _ = try await api.request("GET", "/api/profile")
        #expect(s.wire.map { $0.url?.path ?? "" } == [KE2E.infoPath, "/e2e"])
        #expect(s.wire.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == nil })
        #expect(pins.value == [KE2EPin(key: s.key, verified: false)])
        #expect(api.e2e?.state.pin?.verified == false)
    }

    @Test func aServerWithoutE2EIsNeverUsedInPlaintext() async throws {
        // spec 5.18: no plaintext mode, so hiding /api/e2e/info on the path cannot make the app fall back
        for status in [404, 401, 200] {
            let s = FakeE2EServer()
            s.offersKE2E = false
            s.plain = { _ in (status, Data(status == 200 ? "<html>kelo</html>".utf8 : #"{"detail": "no"}"#.utf8)) }
            let seen = Box<[ApiError]>([])
            let api = s.api(pin: nil, onFailure: { e in seen.update { $0.append(e) } })
            do { _ = try await api.request("GET", "/api/profile"); Issue.record("no error") } catch let e as ApiError {
                #expect(e.code == KE2ETransport.unsupportedCode, "\(status)")
            }
            #expect(s.wire.map { $0.url?.path ?? "" } == [KE2E.infoPath], "\(status)")
            #expect(seen.value.first?.code == KE2ETransport.unsupportedCode)
        }
    }

    @Test func aFileGoesUpAsAStreamedRequest() async throws {
        let s = FakeE2EServer()
        s.route = { r in (200, ["content-type": "application/json"], [Data(#"{"path": "uploads/a.bin", "n": \#(r.body.count)}"#.utf8)]) }
        let api = s.api(pin: KE2EPin(key: s.key, verified: true))
        let content = Data((0..<200_003).map { UInt8(truncatingIfNeeded: $0 &* 31) })
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("ke2e-up-\(UUID().uuidString).bin")
        try content.write(to: file)
        defer { try? FileManager.default.removeItem(at: file) }
        let sent = Box<Int64>(0)
        let path = try await api.upload(name: "a b.bin", body: .file(file), contentType: "application/octet-stream", progress: { s, _ in sent.value = s })
        #expect(path == "uploads/a.bin")
        let inner = try #require(s.inner.first)
        #expect(inner.head.method == "PUT" && inner.head.path == "/api/uploads/a%20b.bin" && inner.body == content)
        #expect(inner.head.headers["content-type"] == "application/octet-stream")
        #expect(s.wire.first?.url?.path == "/e2e" && sent.value > Int64(content.count))
        // nothing of the file on the wire, and no temporary file left behind
        let leftovers = (try? FileManager.default.contentsOfDirectory(atPath: FileManager.default.temporaryDirectory.path)) ?? []
        #expect(!leftovers.contains { $0.hasSuffix(".ke2e") })
    }

    @Test func liveFrameHeadersComeFromTheAnswerHead() async throws {
        let s = FakeE2EServer()
        s.route = { _ in (200, ["content-type": "image/jpeg", "x-frame-seq": "9", "x-frame-w": "390", "x-you-control": "1"], [Data([0xff, 0xd8])]) }
        let api = s.api(pin: KE2EPin(key: s.key, verified: true))
        let f = try #require(try await api.liveFrame(tabId: "owner", after: 3, quality: LiveQuality.wifi))
        #expect(f.seq == 9 && f.w == 390 && f.youControl && f.jpeg == Data([0xff, 0xd8]))
    }

    @Test func plaintextPathsStayPlaintext() throws {
        let t = KE2ETransport(base: FakeE2EServer(), baseUrl: "https://h.example/kelo", state: KE2EState())
        #expect(t.isPlaintext(URLRequest(url: URL(string: "https://h.example/kelo/api/e2e/info")!)))
        #expect(t.isPlaintext(URLRequest(url: URL(string: "https://h.example/kelo/healthz")!)))
        #expect(!t.isPlaintext(URLRequest(url: URL(string: "https://h.example/kelo/api/profile")!)))
        #expect(t.innerPath(URL(string: "https://h.example/kelo/api/goals?a=%20b")) == "/api/goals?a=%20b")
        #expect(try t.outer(URLRequest(url: URL(string: "https://h.example/kelo/api/x")!)).url?.absoluteString == "https://h.example/kelo/e2e")
    }

    @Test func scanningTheSameKeyVerifies() {
        let k = TestServer().key
        let pins = Box<[KE2EPin]>([])
        let state = KE2EState(pin: KE2EPin(key: k, verified: false), onPin: { p in pins.update { $0.append(p) } })
        #expect(!state.verify(scanned: TestServer().key))
        #expect(state.pin?.verified == false)
        #expect(state.verify(scanned: k))
        #expect(state.pin?.verified == true && pins.value.count == 1)
    }
}

@Suite struct KE2EPairingTests {
    @Test func aLinkWithTheKeyPairsThroughE2EAndPinsItVerified() async throws {
        let s = FakeE2EServer()
        s.route = { r in r.head.path == "/api/pair" ? (200, ["content-type": "application/json"], [Data(#"{"token": "new-device-token", "device_id": 4}"#.utf8)])
                                                     : (404, [:], []) }
        let link = PairingLink(serverUrl: "https://kelo.test", code: "PAIRCODE-SECRET-123", serverKey: s.key)
        let session = try await PairingClient(transport: s, appHeader: "ios/0.9.0+57").pair(link, deviceName: "iPhone")
        #expect(session.token == "new-device-token" && session.e2e == KE2EPin(key: s.key, verified: true))
        #expect(s.wire.count == 1 && s.wire[0].url?.path == "/e2e")
        #expect(s.wire[0].httpBody?.range(of: Data("PAIRCODE-SECRET-123".utf8)) == nil)
        #expect(String(decoding: s.inner[0].body, as: UTF8.self).contains("PAIRCODE-SECRET-123"))
    }

    @Test func aTypedCodeTakesTheKeyOnFirstUseNotVerified() async throws {
        let s = FakeE2EServer()
        s.route = { _ in (200, ["content-type": "application/json"], [Data(#"{"token": "t2"}"#.utf8)]) }
        let session = try await PairingClient(transport: s, appHeader: "ios/0.9.0+57").pair(PairingLink(serverUrl: "https://kelo.test", code: "ABCDEFGHJKMNPQRS"), deviceName: "iPhone")
        #expect(session.e2e == KE2EPin(key: s.key, verified: false))
        #expect(s.wire.map { $0.url?.path ?? "" } == [KE2E.infoPath, "/e2e"])
    }

    @Test func anOlderServerIsNotPairedWith() async throws {
        let s = FakeE2EServer()
        s.offersKE2E = false
        s.plain = { _ in (200, Data(#"{"token": "t3"}"#.utf8)) }
        do {
            _ = try await PairingClient(transport: s, appHeader: "").pair(PairingLink(serverUrl: "https://kelo.test", code: "ABCDEFGHJKMNPQRS"), deviceName: "iPhone")
            Issue.record("paired with a server without KE2E")
        } catch let e as ApiError { #expect(e.code == KE2ETransport.unsupportedCode) }
        #expect(s.wire.map { $0.url?.path ?? "" } == [KE2E.infoPath])
    }

    @Test func anOlderServerThatAsksForATokenOnTheInfoAddressMustBeUpdated() async throws {
        // older servers (and a plain mock) refuse unknown addresses with 401: "update your server", not "wrong key"
        let t = FakeTransport(.ok(401, #"{"detail":"invalid or missing bearer token"}"#))
        do {
            _ = try await PairingClient(transport: t, appHeader: "").checkKey(serverUrl: "https://k.example", key: "key")
            Issue.record("signed in without KE2E")
        } catch let e as ApiError { #expect(e.code == KE2ETransport.unsupportedCode && !e.isUnauthorized) }
        #expect(t.requests.value.count == 1)
    }

    @Test func aSessionSavedBeforeKE2EStillReads() throws {
        let old = Data(#"{"serverUrl": "https://kelo.test", "token": "t"}"#.utf8)
        let s = try JSONDecoder().decode(Session.self, from: old)
        #expect(s.e2e == nil && s.token == "t")
        var pinned = s
        pinned.e2e = KE2EPin(key: TestServer().key, verified: false)
        #expect(try JSONDecoder().decode(Session.self, from: JSONEncoder().encode(pinned)) == pinned)
    }
}

@Suite struct VersioningTests {
    @Test func versionsCompare() {
        #expect(Versioning.parse("0.9.0+1")! == (0, 9, 0))
        #expect(Versioning.parse("0.10.2-beta.1")! == (0, 10, 2))
        #expect(Versioning.parse("1.0") == nil && Versioning.parse("") == nil && Versioning.parse(nil) == nil)
        #expect(Versioning.older("0.9.0", than: "0.10.0") && !Versioning.older("0.10.0", than: "0.9.9"))
        #expect(!Versioning.older("0.9.0", than: "0.9.0"))
    }

    @Test func serverTooOld() {
        #expect(Versioning.serverTooOld(header: "0.8.9+3", minServer: "0.9.0"))
        #expect(!Versioning.serverTooOld(header: "0.9.0+1", minServer: "0.9.0"))
        #expect(!Versioning.serverTooOld(header: nil, minServer: "0.9.0"))       // older than 0.9.0: plaintext, shown as such
        #expect(!Versioning.serverTooOld(header: "garbage", minServer: "0.9.0"))
    }

    @Test func appHeader() {
        #expect(Versioning.appHeaderValue(version: "0.9.0", build: "26111011") == "ios/0.9.0+26111011")
        #expect(Versioning.appHeaderValue(version: "0.9.0", build: nil) == "ios/0.9.0")
        #expect(Versioning.appHeaderValue(version: "0.9.0", build: "1.2") == "ios/0.9.0")
        #expect(KeloVersion.version == "0.9.0" || Versioning.parse(KeloVersion.version) != nil)
    }

    @Test func everyPlaintextRequestCarriesTheAppVersion() async throws {
        let t = FakeTransport(.ok(200, "{}"))
        let api = KeloApi(baseUrl: "https://k.example", token: "x", transport: t, appHeader: "ios/0.9.0+57")
        _ = try await api.request("GET", "/api/profile")
        #expect(t.last.value(forHTTPHeaderField: "X-Kelo-App") == "ios/0.9.0+57")
    }

    @Test func theCompiledVersionMatchesVersionJson() throws {
        var u = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { u.deleteLastPathComponent() }
        let file = u.appendingPathComponent("version.json")
        guard FileManager.default.fileExists(atPath: file.path) else { return }
        let j = try JSON.parse(Data(contentsOf: file))
        #expect(j["version"]?.stringValue == KeloVersion.version, "run python3 apps/ios/tools/gen_version.py")
        #expect(j["min_server"]?.stringValue == KeloVersion.minServer)
    }
}
