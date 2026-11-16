import Foundation
import CryptoKit
import Testing
@testable import KeloCore

/// A server's side of one exchange, as src/assistant/e2e.py does it: open the envelope, then answer with frames.
struct TestServer {
    let sk = Curve25519.KeyAgreement.PrivateKey()
    var key: KE2EServerKey { try! KE2EServerKey(publicKey: sk.publicKey.rawRepresentation) }

    func open(_ outer: Data) throws -> (KE2ERequestHead, Data, HPKE.Recipient) {
        let env = try KE2EEnvelope.parse(outer)
        guard env.kid == key.kidBytes else { throw KE2EError.keyChanged }
        var r = try HPKE.Recipient(privateKey: sk, ciphersuite: KE2E.ciphersuite, info: KE2E.info(kid: key.kid), encapsulatedKey: env.enc)
        let (h, b) = try KE2EInner.split(try r.open(env.ciphertext, authenticating: KE2E.requestAAD))
        return (h, b, r)
    }

    func writer(_ r: HPKE.Recipient) throws -> KE2EResponseWriter {
        KE2EResponseWriter(key: try r.exportSecret(context: KE2E.responseLabel, outputByteCount: 32))
    }
}

@Suite struct KE2EKeyTests {
    @Test func base64urlWithAndWithoutPadding() {
        let d = Data([0xfb, 0xff, 0xfe, 0x01])
        #expect(Base64URL.encode(d) == "-__-AQ")
        #expect(Base64URL.decode("-__-AQ") == d)
        #expect(Base64URL.decode("-__-AQ==") == d)
        #expect(Base64URL.decode("+//+AQ") == nil)   // standard base64 is not base64url
        #expect(Base64URL.decode("a") == nil)
        #expect(Base64URL.decode("") == nil)
    }

    @Test func base32() {
        // RFC 4648 §10
        #expect(Base32.encode(Data("foobar".utf8)) == "MZXW6YTBOI")
        #expect(Base32.encode(Data("f".utf8)) == "MY")
        #expect(Base32.encode(Data()) == "")
    }

    @Test func keyIdsAndFingerprint() throws {
        let key = TestServer().key
        #expect(key.kid.count == 16 && key.kid == key.kid.lowercased())
        #expect(key.kidBytes.count == 8)
        let fp = key.fingerprint
        #expect(fp.count == 24 + 5)
        #expect(fp.split(separator: "-").allSatisfy { $0.count == 4 })
        #expect(try KE2EServerKey(base64url: key.base64url) == key)
    }

    @Test func refusesWhatIsNotAKey() {
        #expect(throws: KE2EError.badKey) { try KE2EServerKey(publicKey: Data(repeating: 1, count: 31)) }
        #expect(throws: KE2EError.badKey) { try KE2EServerKey(base64url: "not a key!") }
    }

    @Test func keepsAsText() throws {
        let pin = KE2EPin(key: TestServer().key, verified: false)
        let data = try JSONEncoder().encode(pin)
        #expect(String(decoding: data, as: UTF8.self).contains(pin.key.base64url))
        #expect(try JSONDecoder().decode(KE2EPin.self, from: data) == pin)
    }

    @Test func infoMustAgreeWithItsKey() throws {
        let key = TestServer().key
        let good: JSON = ["v": 1, "kid": .string(key.kid), "pk": .string(key.base64url), "fingerprint": .string(key.fingerprint)]
        #expect(try KE2EInfo.parse(good) == key)
        var badKid = good.objectValue!; badKid["kid"] = "0000000000000000"
        #expect(throws: KE2EError.badKey) { try KE2EInfo.parse(.object(badKid)) }
        var badFp = good.objectValue!; badFp["fingerprint"] = "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA"
        #expect(throws: KE2EError.badKey) { try KE2EInfo.parse(.object(badFp)) }
        var v2 = good.objectValue!; v2["v"] = 2
        #expect(throws: KE2EError.badKey) { try KE2EInfo.parse(.object(v2)) }
    }

    @Test func scanningThePinnedKeyVerifiesIt() {
        let a = TestServer().key, b = TestServer().key
        let pin = KE2EPin(key: a, verified: false)
        #expect(pin.scanned(a) == KE2EPin(key: a, verified: true))
        #expect(pin.scanned(b) == nil)
    }
}

@Suite struct KE2EPairingLinkTests {
    let code = "AbCdEfGh_ijkl-MNOP1234"

    @Test func linkWithKey() throws {
        let key = TestServer().key
        let link = try #require(PairingLinks.parse("https://kelo.example.com/#pair=\(code)&k=\(key.base64url)"))
        #expect(link.code == code)
        #expect(link.serverKey == key)
    }

    @Test func unknownParametersAreIgnoredAndOldLinksStillWork() throws {
        let key = TestServer().key
        #expect(PairingLinks.parse("https://k.example/#pair=\(code)&v=2&k=\(key.base64url)&x=y")?.serverKey == key)
        #expect(PairingLinks.parse("https://k.example/#pair=\(code)&v=2")?.code == code)
        let old = try #require(PairingLinks.parse("https://k.example/#pair=\(code)"))
        #expect(old.serverKey == nil && old.code == code)
    }

    @Test func aBrokenKeyRefusesTheLink() {
        #expect(PairingLinks.parse("https://k.example/#pair=\(code)&k=short") == nil)
        #expect(PairingLinks.parse("https://k.example/#pair=\(code)&k=") == nil)
    }
}

@Suite struct KE2EEnvelopeTests {
    @Test func headCarriesOnlyTheInsideHeaders() throws {
        var r = URLRequest(url: URL(string: "https://k.example/api/uploads/a%20b?inline=1&x=%2F")!)
        r.httpMethod = "put"
        r.setValue("Bearer t", forHTTPHeaderField: "Authorization")
        r.setValue("image/png", forHTTPHeaderField: "Content-Type")
        r.setValue("ko", forHTTPHeaderField: "Accept-Language")
        r.setValue("d1", forHTTPHeaderField: "X-Kelo-Device")
        r.setValue("secret-cookie", forHTTPHeaderField: "Cookie")
        r.setValue("ua", forHTTPHeaderField: "User-Agent")
        let h = try #require(KE2ERequestHead.of(r, now: Date(timeIntervalSince1970: 1731200000.7)))
        #expect(h.method == "PUT")
        #expect(h.path == "/api/uploads/a%20b?inline=1&x=%2F")
        #expect(h.headers == ["authorization": "Bearer t", "content-type": "image/png", "accept-language": "ko", "x-kelo-device": "d1"])
        #expect(h.ts == 1731200000)
        #expect(Base64URL.decode(h.nonce)?.count == 16)
        #expect(KE2ERequestHead.of(r)?.nonce != h.nonce)
        #expect(try KE2ERequestHead.parse(h.json()) == h)
    }

    @Test func outerLayoutAndServerOpensIt() throws {
        let server = TestServer()
        let head = KE2ERequestHead.make(method: "POST", path: "/api/talk", headers: ["Authorization": "Bearer tok"])
        let body = Data("{\"text\":\"안녕\"}".utf8)
        let ex = try KE2EExchange.seal(head: head, body: body, to: server.key)
        #expect(ex.outerBody[0] == 0x01)
        #expect(ex.outerBody.subdata(in: 1..<9) == server.key.kidBytes)
        #expect(ex.outerBody.count == KE2E.envelopeHeaderBytes + 4 + head.json().count + body.count + KE2E.tagBytes)
        // nothing readable on the wire
        #expect(ex.outerBody.range(of: Data("Bearer".utf8)) == nil && ex.outerBody.range(of: body) == nil)
        let (h, b, _) = try server.open(ex.outerBody)
        #expect(h == head && b == body)
    }

    @Test func anotherServerCannotOpen() throws {
        let ex = try KE2EExchange.seal(head: .make(method: "GET", path: "/api/profile", headers: [:]), body: Data(), to: TestServer().key)
        #expect(throws: KE2EError.keyChanged) { try TestServer().open(ex.outerBody) }
    }

    @Test func eachSealIsFresh() throws {
        let key = TestServer().key
        let head = KE2ERequestHead(method: "GET", path: "/", headers: [:], ts: 1, nonce: "AAAAAAAAAAAAAAAAAAAAAA")
        let a = try KE2EExchange.seal(head: head, body: Data(), to: key), b = try KE2EExchange.seal(head: head, body: Data(), to: key)
        #expect(a.outerBody != b.outerBody)
        #expect(keyBytes(try a.responseKey()) != keyBytes(try b.responseKey()))
    }
}

@Suite struct KE2EResponseTests {
    func exchange() throws -> (KE2EExchange, KE2EResponseWriter) {
        let server = TestServer()
        let ex = try KE2EExchange.seal(head: .make(method: "GET", path: "/api/turns/1/events", headers: [:]), body: Data(), to: server.key)
        let (_, _, r) = try server.open(ex.outerBody)
        return (ex, try server.writer(r))
    }

    @Test func wholeAnswer() throws {
        var (ex, w) = try exchange()
        let body = Data("{\"ok\":true}".utf8)
        let wire = try w.whole(head: KE2EResponseHead(status: 201, headers: ["Content-Type": "application/json", "ETag": "\"7\""]), body: body)
        let (head, got) = try KE2EResponseReader.readWhole(wire, key: try ex.responseKey())
        #expect(head == KE2EResponseHead(status: 201, headers: ["content-type": "application/json", "etag": "\"7\""]))
        #expect(got == body)
    }

    @Test func headOnlyIsTheLastFrame() throws {
        var (ex, w) = try exchange()
        let (head, body) = try KE2EResponseReader.readWhole(try w.whole(head: KE2EResponseHead(status: 204), body: Data()), key: try ex.responseKey())
        #expect(head.status == 204 && body.isEmpty)
    }

    @Test func largeBodiesComeIn64KiBFrames() throws {
        var (ex, w) = try exchange()
        let body = Data((0..<(KE2E.maxBodyFrame * 2 + 5)).map { UInt8(truncatingIfNeeded: $0) })
        let wire = try w.whole(head: KE2EResponseHead(status: 200), body: body)
        var r = try ex.responseReader()
        let parts = try r.feed(wire)
        try r.finish()
        #expect(parts.count == 4)   // head + 3 pieces
        let pieces = parts.compactMap { if case .body(let d) = $0 { d } else { nil } }
        #expect(pieces.map(\.count) == [KE2E.maxBodyFrame, KE2E.maxBodyFrame, 5])
        #expect(pieces.reduce(Data(), +) == body)
    }

    @Test func eventStreamFramesComeOutAsTheyArrive() throws {
        var (ex, w) = try exchange()
        var r = try ex.responseReader()
        let head = try w.frame(KE2EResponseHead(status: 200, headers: ["content-type": "text/event-stream"]).json(), last: false)
        let e1 = try w.frame(Data("data: {\"t\":\"a\"}\n\n".utf8), last: false)
        let e2 = try w.frame(Data("data: {}\n\n".utf8), last: true)
        // half a frame gives nothing yet
        #expect(try r.feed(head.prefix(10)).isEmpty)
        #expect(try r.feed(head.dropFirst(10)) == [.head(KE2EResponseHead(status: 200, headers: ["content-type": "text/event-stream"]))])
        #expect(!r.complete)
        #expect(throws: KE2EError.truncated) { try r.finish() }
        #expect(try r.feed(e1 + e2) == [.body(Data("data: {\"t\":\"a\"}\n\n".utf8)), .body(Data("data: {}\n\n".utf8))])
        #expect(r.complete)
        try r.finish()
    }

    @Test func cutOffMissingReorderedOrChangedFramesFail() throws {
        var (ex, w) = try exchange()
        let f0 = try w.frame(KE2EResponseHead(status: 200).json(), last: false)
        let f1 = try w.frame(Data("one".utf8), last: false)
        let f2 = try w.frame(Data("two".utf8), last: true)
        let key = try ex.responseKey()

        func read(_ wire: Data) -> KE2EError? {
            var r = KE2EResponseReader(key: key)
            do { _ = try r.feed(wire); try r.finish(); return nil } catch { return error as? KE2EError }
        }
        #expect(read(f0 + f1 + f2) == nil)
        #expect(read(f0 + f1) == .truncated)                     // no last frame
        #expect(read(f0 + f1 + f2.dropLast()) == .truncated)     // inside the last frame
        #expect(read(f0 + f2) == .badFrame)                      // counter gap
        #expect(read(f1 + f0 + f2) == .badFrame)                 // out of order
        #expect(read(f0 + f1 + f2 + f0) == .trailingData)        // after the last
        var flipped = f0 + f1 + f2
        flipped[flipped.count - 1] ^= 1
        #expect(read(flipped) == .badFrame)
        #expect(read(Data([0xff, 0xff, 0xff, 0xff])) == .frameTooLarge)
        // the right frames under the other answer's key
        var other = try self.exchange().1
        #expect(read(try other.frame(KE2EResponseHead(status: 200).json(), last: true)) == .badFrame)
    }

    @Test func lastFlagCannotBeMovedEarlier() throws {
        // a stream cut after frame 1 whose frame 1 claims to be last: frames are sealed with their flag, so a cut stream never reads
        // as complete
        var (ex, w) = try exchange()
        let f0 = try w.frame(KE2EResponseHead(status: 200).json(), last: false)
        let f1 = try w.frame(Data("x".utf8), last: false)
        var r = try ex.responseReader()
        _ = try r.feed(f0 + f1)
        #expect(!r.complete)
    }

    @Test func badHead() throws {
        var (ex, w) = try exchange()
        var r = try ex.responseReader()
        #expect(throws: KE2EError.badHead) { try r.feed(try w.frame(Data("not json".utf8), last: true)) }
    }
}

@Suite struct KE2EWebSocketTests {
    @Test func bothDirectionsWithTheirOwnCountersAndTypes() throws {
        let server = TestServer()
        let ex = try KE2EExchange.seal(head: .make(method: "GET", path: "/api/browser/sessions/t1/live?ticket=x", headers: [:]), body: Data(),
                                       to: server.key)
        let (h, _, r) = try server.open(ex.outerBody)
        #expect(h.path == "/api/browser/sessions/t1/live?ticket=x")
        var phone = try ex.webSocketCipher()
        var srv = KE2EWebSocketCipher.serverSide(c2s: try r.exportSecret(context: KE2E.webSocketC2S, outputByteCount: 32),
                                                 s2c: try r.exportSecret(context: KE2E.webSocketS2C, outputByteCount: 32))
        let m1 = try phone.seal(.text("{\"type\":\"ping\"}"))
        let m2 = try phone.seal(.text("{\"type\":\"ping\"}"))
        #expect(m1 != m2)   // counter moved
        #expect(m1.count == 1 + 15 + KE2E.tagBytes)   // type byte + text + tag
        // the live view: a text header, then the JPEG as binary
        let s1 = try srv.seal(.text("{\"type\":\"frame\",\"seq\":3}"))
        let s2 = try srv.seal(.binary(Data([0xff, 0xd8, 0xff])))
        #expect(try phone.open(s1) == .text("{\"type\":\"frame\",\"seq\":3}"))
        #expect(try phone.open(s2) == .binary(Data([0xff, 0xd8, 0xff])))
        #expect(try srv.open(m1) == .text("{\"type\":\"ping\"}"))
        #expect(try srv.open(m2) == .text("{\"type\":\"ping\"}"))
        // a repeated or own-direction message does not open
        #expect(throws: KE2EError.badFrame) { try srv.open(m2) }
        #expect(throws: KE2EError.badFrame) { try phone.open(m1) }
    }

    @Test func typeByte() throws {
        #expect(KE2EWebSocketMessage.text("é").plaintext == Data([0x01, 0xc3, 0xa9]))
        #expect(KE2EWebSocketMessage.binary(Data([9])).plaintext == Data([0x02, 9]))
        #expect(try KE2EWebSocketMessage.parse(Data([0x02])) == .binary(Data()))
        #expect(throws: KE2EError.badFrame) { try KE2EWebSocketMessage.parse(Data([0x03, 1])) }
        #expect(throws: KE2EError.badFrame) { try KE2EWebSocketMessage.parse(Data()) }
        #expect(throws: KE2EError.badFrame) { try KE2EWebSocketMessage.parse(Data([0x01, 0xff])) }   // not UTF-8
    }
}

@Suite struct KE2EStreamedRequestTests {
    func open(_ server: TestServer, _ wire: Data) throws -> (KE2ERequestHead, Data) {
        let env = try KE2EEnvelope.parse(wire)
        #expect(env.version == 0x02)
        var r = try HPKE.Recipient(privateKey: server.sk, ciphersuite: KE2E.ciphersuite, info: KE2E.info(kid: server.key.kid), encapsulatedKey: env.enc)
        let (h, empty) = try KE2EInner.split(try r.open(env.ciphertext, authenticating: KE2E.requestAAD))
        #expect(empty.isEmpty)
        var reader = KE2EFrameStreamReader(key: try r.exportSecret(context: KE2E.requestBodyLabel, outputByteCount: 32), aad: KE2E.requestAAD)
        let body = try reader.feed(env.bodyFrames).reduce(Data(), +)
        try reader.finish()
        return (h, body)
    }

    @Test func largeBodyInFrames() throws {
        let server = TestServer()
        let head = KE2ERequestHead.make(method: "PUT", path: "/api/uploads/movie.mov", headers: ["Content-Type": "video/quicktime"])
        let ex = try KE2EExchange.sealStreamed(head: head, to: server.key)
        #expect(ex.outerBody[0] == 0x02 && ex.outerBody.subdata(in: 1..<9) == server.key.kidBytes)
        let body = Data((0..<(KE2E.maxBodyFrame * 3 + 1)).map { UInt8(truncatingIfNeeded: $0 &* 7) })
        var w = try ex.bodyWriter()
        // as an uploader does it: piece by piece from a file, the last flagged
        var wire = ex.outerBody
        var offset = 0
        while offset < body.count {
            let end = min(offset + KE2E.maxBodyFrame, body.count)
            wire += try w.frame(body.subdata(in: offset..<end), last: end == body.count)
            offset = end
        }
        #expect(wire.range(of: body.prefix(64)) == nil)
        let (h, b) = try open(server, wire)
        #expect(h == head && b == body)
    }

    @Test func emptyBodyIsOneEmptyLastFrame() throws {
        let server = TestServer()
        let ex = try KE2EExchange.sealStreamed(head: .make(method: "PUT", path: "/api/uploads/empty", headers: [:]), to: server.key)
        var w = try ex.bodyWriter()
        let frames = try w.whole(Data())
        #expect(frames.count == 4 + KE2E.tagBytes)
        #expect(try open(server, ex.outerBody + frames).1.isEmpty)
    }

    @Test func cutOrChangedBodyFails() throws {
        let server = TestServer()
        let ex = try KE2EExchange.sealStreamed(head: .make(method: "PUT", path: "/api/uploads/x", headers: [:]), to: server.key)
        var w = try ex.bodyWriter()
        let f0 = try w.frame(Data("abc".utf8), last: false)
        let f1 = try w.frame(Data("def".utf8), last: true)
        #expect(throws: KE2EError.truncated) { try open(server, ex.outerBody + f0) }
        var changed = ex.outerBody + f0 + f1
        changed[changed.count - 1] ^= 1
        #expect(throws: KE2EError.badFrame) { try open(server, changed) }
        // the answer key is the same as for 0x01
        #expect(keyBytes(try ex.responseKey()).count == 32)
    }
}

@Suite struct KE2EClockAndErrorTests {
    @Test func clockCorrectsOnceFromTheServersNow() {
        var c = KE2EClock()
        let phone = Date(timeIntervalSince1970: 1_731_200_000)
        #expect(KE2EClock.serverNow(in: Data(#"{"code": "e2e_clock", "now": 1731200400}"#.utf8)) == 1_731_200_400)
        #expect(KE2EClock.serverNow(in: Data(#"{"code": "other", "now": 1}"#.utf8)) == nil)
        let corrected = c.correct(serverNow: 1_731_200_400, phone: phone)
        #expect(corrected)
        #expect(c.offset == 400)
        #expect(c.now(phone).timeIntervalSince1970 == 1_731_200_400)
        let none = c.correct(serverNow: nil, phone: phone)
        #expect(!none)
        let head = KE2ERequestHead.make(method: "GET", path: "/", headers: [:], now: c.now(phone))
        #expect(head.ts == 1_731_200_400)
    }

    @Test func topLevelCode() {
        let locked = ApiError.from(status: 423, body: Data(#"{"code": "locked", "detail": "Kelo is locked"}"#.utf8))
        let changed = ApiError.from(status: 409, body: Data(#"{"code": "e2e_key_changed"}"#.utf8))
        #expect(changed.isKeyChanged && changed.detail == "HTTP 409")
        let inDetail = ApiError.from(status: 409, body: Data(#"{"detail": {"code": "secret_in_message", "spans": []}}"#.utf8))
        #expect(inDetail.code == "secret_in_message")
        #expect(ApiError.from(status: 500, body: Data("oops".utf8)).code == nil)
    }
}
