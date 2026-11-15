import Foundation
import CryptoKit
import Testing
@testable import KeloCore

/// docs/security/e2e-vectors.json (made by the server's tests/e2e_vectors_gen.py; format in e2e-vectors.md): the same fixed keys,
/// envelopes and frames every platform must pass. KELO_E2E_VECTORS=<path> points at another copy. When the file is missing (a copy of
/// ios/ without docs/) the suite is skipped, not failed.
enum E2EVectors {
    static var url: URL {
        if let p = ProcessInfo.processInfo.environment["KELO_E2E_VECTORS"], !p.isEmpty { return URL(fileURLWithPath: p) }
        var u = URL(fileURLWithPath: #filePath)
        for _ in 0..<6 { u.deleteLastPathComponent() }   // E2E, KeloCoreTests, Tests, KeloCore, ios -> the repository
        return u.appendingPathComponent("docs/security/e2e-vectors.json")
    }

    static var available: Bool { FileManager.default.fileExists(atPath: url.path) }

    static func load() throws -> JSON { try JSON.parse(Data(contentsOf: url)) }
}

struct VectorFieldMissing: Error, CustomStringConvertible { let description: String }

extension JSON {
    func hexField(_ key: String) throws -> Data {
        guard let s = self[key]?.stringValue else { throw VectorFieldMissing(description: "missing \(key)") }
        return hex(s)
    }
    func list(_ key: String) throws -> [JSON] {
        guard let a = self[key]?.arrayValue else { throw VectorFieldMissing(description: "missing \(key)") }
        return a
    }
    func named(_ key: String, _ name: String) throws -> JSON {
        guard let n = try list(key).first(where: { $0["name"]?.stringValue == name }) else {
            throw VectorFieldMissing(description: "no \(key) named \(name)")
        }
        return n
    }
}

/// The error names the vectors use (e2e-vectors.md, negative[].expect).
func vectorName(_ e: Error) -> String {
    switch e as? KE2EError {
    case .badFrame, .trailingData, .frameTooLarge, .badHead: "bad_frame"
    case .truncated: "truncated"
    default: "\(e)"
    }
}

@Suite(.enabled(if: E2EVectors.available, "docs/security/e2e-vectors.json not found"))
struct E2EVectorsTests {
    let v: JSON
    init() throws { v = try E2EVectors.load() }

    var serverSK: Curve25519.KeyAgreement.PrivateKey { get throws { try .init(rawRepresentation: try v["server"]!.hexField("sk")) } }

    @Test func fileVersionAndLabels() throws {
        #expect(v["version"]?.intValue == 1)
        let l = try #require(v["labels"])
        #expect(l["info_prefix"]?.stringValue == KE2E.infoPrefix)
        #expect(l["aad_request"]?.stringValue.map { Data($0.utf8) } == KE2E.requestAAD)
        #expect(l["response"]?.stringValue.map { Data($0.utf8) } == KE2E.responseLabel)
        #expect(l["ws_c2s"]?.stringValue.map { Data($0.utf8) } == KE2E.webSocketC2S)
        #expect(l["ws_s2c"]?.stringValue.map { Data($0.utf8) } == KE2E.webSocketS2C)
        #expect(l["aad_ws"]?.stringValue.map { Data($0.utf8) } == KE2E.webSocketAAD)
        #expect(l["envelope_version"]?.intValue == Int(KE2E.envelopeVersion))
        #expect(l["envelope_version_stream"]?.intValue == Int(KE2E.envelopeVersionStream))
        #expect(l["req_body"]?.stringValue.map { Data($0.utf8) } == KE2E.requestBodyLabel)
        #expect(v["revision"].flatMap(\.intValue) ?? 0 >= 2)
        #expect(l["max_frame_plaintext"]?.intValue == KE2E.maxBodyFrame)
        let s = try #require(v["suite"])
        #expect(s["mode"]?.intValue == 0 && s["kem_id"]?.intValue == 0x20 && s["kdf_id"]?.intValue == 1 && s["aead_id"]?.intValue == 3)
    }

    @Test func serverKeyAndWhatComesFromIt() throws {
        let s = try #require(v["server"])
        #expect(try serverSK.publicKey.rawRepresentation == (try s.hexField("pk")))
        let key = try KE2EServerKey(base64url: try #require(s["pk_b64url"]?.stringValue))
        #expect(key.publicKey == (try s.hexField("pk")))
        #expect(key.kid == s["kid"]?.stringValue)
        #expect(key.fingerprint == s["fingerprint"]?.stringValue)
        #expect(KE2E.info(kid: key.kid) == (try s.hexField("info")))
        #expect(try KE2EInfo.parse(try #require(s["e2e_info"])) == key)
    }

    @Test func pairingLinkCarriesTheKey() throws {
        let s = try #require(v["server"])
        let link = try #require(s["pair_link"]?.stringValue)
        let key = try KE2EServerKey(base64url: try #require(s["pk_b64url"]?.stringValue))
        let parsed = try #require(PairingLinks.parse(link))
        #expect(PairingLinks.codeLength.contains(parsed.code.count))
        #expect(link.contains("#pair=\(parsed.code)&k="))
        #expect(parsed.serverKey == key)
        #expect(parsed.serverUrl == "https://example.kelo.chat")
    }

    @Test func requestsOpenOnTheServerSide() throws {
        let s = try #require(v["server"])
        let key = try KE2EServerKey(publicKey: try s.hexField("pk"))
        for req in try v.list("requests") {
            let name = req["name"]?.stringValue ?? "?"
            let enc = try req.hexField("enc")
            var r = try HPKE.Recipient(privateKey: try serverSK, ciphersuite: KE2E.ciphersuite, info: try s.hexField("info"), encapsulatedKey: enc)
            let inner = try r.open(try req.hexField("ct"), authenticating: try req.hexField("aad"))
            #expect(inner == (try req.hexField("inner")), "\(name)")
            #expect(try req.hexField("aad") == KE2E.requestAAD)
            #expect(keyBytes(try r.exportSecret(context: KE2E.responseLabel, outputByteCount: 32)) == (try req.hexField("response_key")), "\(name)")
            #expect(keyBytes(try r.exportSecret(context: KE2E.webSocketC2S, outputByteCount: 32)) == (try req.hexField("ws_c2s_key")), "\(name)")
            #expect(keyBytes(try r.exportSecret(context: KE2E.webSocketS2C, outputByteCount: 32)) == (try req.hexField("ws_s2c_key")), "\(name)")

            // the envelope layout, and the inner request as this app reads and builds it
            let env = try KE2EEnvelope.parse(try req.hexField("envelope"))
            let ct = try req.hexField("ct")
            #expect(env.kid == key.kidBytes && env.enc == enc && env.ciphertext == ct, "\(name)")
            let (head, body) = try KE2EInner.split(inner)
            #expect(String(decoding: body, as: UTF8.self) == req["body_text"]?.stringValue, "\(name)")
            let given = try KE2ERequestHead.parse(Data(try #require(req["head_json"]?.stringValue).utf8))
            #expect(head == given, "\(name)")
            #expect(try KE2EInner.split(KE2EInner.build(head: given, body: body)) == (given, body), "\(name)")
        }
    }

    /// CryptoKit picks its own ephemeral key: the app's sealing is checked by a round trip with the vectors' server key.
    @Test func thisAppsEnvelopeOpensWithTheVectorsServerKey() throws {
        let s = try #require(v["server"])
        let key = try KE2EServerKey(publicKey: try s.hexField("pk"))
        let req = try v.named("requests", "talk")
        let head = try KE2ERequestHead.parse(Data(try #require(req["head_json"]?.stringValue).utf8))
        let body = Data(try #require(req["body_text"]?.stringValue).utf8)
        let ex = try KE2EExchange.seal(head: head, body: body, to: key)
        let env = try KE2EEnvelope.parse(ex.outerBody)
        #expect(env.kid == key.kidBytes)
        var r = try HPKE.Recipient(privateKey: try serverSK, ciphersuite: KE2E.ciphersuite, info: try s.hexField("info"), encapsulatedKey: env.enc)
        let (h, b) = try KE2EInner.split(try r.open(env.ciphertext, authenticating: KE2E.requestAAD))
        #expect(h == head && b == body)
        #expect(keyBytes(try ex.responseKey()) == keyBytes(try r.exportSecret(context: KE2E.responseLabel, outputByteCount: 32)))
    }

    @Test func responsesRead() throws {
        for resp in try v.list("responses") {
            let name = resp["name"]?.stringValue ?? "?"
            let key = SymmetricKey(data: try resp.hexField("key"))
            let frames = try resp.list("frames")
            // each frame alone, with its own nonce / aad as given
            var opener = KE2EFrameOpener(key: key, aad: KE2E.responseLabel)
            for f in frames {
                let counter = UInt64(try #require(f["counter"]?.int64Value))
                #expect(KE2ENonce.make(counter).withUnsafeBytes { Data($0) } == (try f.hexField("nonce")))
                let flagValue = try #require(f["flag"]?.intValue)
                let flag = try #require(KE2EFlag(rawValue: UInt8(flagValue)))
                #expect(try f.hexField("aad") == KE2E.responseLabel + [flag.rawValue])
                let framed = try f.hexField("frame")
                let (pt, last) = try opener.openEither(framed.subdata(in: 4..<framed.count))
                #expect(pt == (try f.hexField("pt")) && last == (flag == .last), "\(name) frame \(counter)")
            }
            // the whole stream, byte by byte as a slow network gives it
            var reader = KE2EResponseReader(key: key)
            var parts: [KE2EResponsePart] = []
            for b in try resp.hexField("stream") { parts += try reader.feed([b]) }
            try reader.finish()
            let head = try KE2EResponseHead.parse(try frames[0].hexField("pt"))
            #expect(parts.first == .head(head), "\(name)")
            let bodies = try frames.dropFirst().map { KE2EResponsePart.body(try $0.hexField("pt")) }
            #expect(Array(parts.dropFirst()) == bodies, "\(name)")
        }
        let simple = try v.named("responses", "simple")
        let (head, body) = try KE2EResponseReader.readWhole(try simple.hexField("stream"), key: SymmetricKey(data: try simple.hexField("key")))
        #expect(head.status == 200 && head.headers["content-type"] == "application/json")
        #expect(String(decoding: body, as: UTF8.self) == "{\"ok\":true,\"reply\":\"네\"}")
    }

    /// A streamed upload (envelope 0x02): the server side opens the head and the body frames; this app's own 0x02 sealing opens the
    /// same way (CryptoKit picks its own ephemeral key, so by round trip).
    @Test func streamedRequests() throws {
        let s = try #require(v["server"])
        let key = try KE2EServerKey(publicKey: try s.hexField("pk"))
        for req in try v.list("stream_requests") {
            let name = req["name"]?.stringValue ?? "?"
            #expect(req["version"]?.intValue == 2)
            let wire = try req.hexField("envelope")
            #expect(wire.prefix(try req.hexField("head").count) == (try req.hexField("head")), "\(name)")
            let (head, body, keys) = try openStreamed(wire, serverInfo: try s.hexField("info"))
            let given = try KE2EInner.split(try req.hexField("inner"))
            #expect(given.0 == head && given.1.isEmpty, "\(name)")
            #expect(head == (try KE2ERequestHead.parse(Data(try #require(req["head_json"]?.stringValue).utf8))), "\(name)")
            #expect(String(decoding: body, as: UTF8.self) == req["body_text"]?.stringValue, "\(name)")
            let bodyKey = try req.hexField("body_key"), responseKey = try req.hexField("response_key")
            #expect(keys.body == bodyKey && keys.response == responseKey, "\(name)")
            #expect(try req.hexField("body_aad_prefix") == KE2E.requestAAD)
            // each listed frame, byte-exact: ChaCha20-Poly1305 is deterministic for key and counter
            var w = KE2ERequestBodyWriter(key: SymmetricKey(data: try req.hexField("body_key")))
            for f in try req.list("body_frames") {
                #expect(try w.frame(try f.hexField("pt"), last: f["flag"]?.intValue == 1) == (try f.hexField("frame")), "\(name)")
            }

            // this app's 0x02 sealing of the same head and body
            let ex = try KE2EExchange.streamedWire(head: head, body: body, key: key)
            let (h2, b2, k2) = try openStreamed(ex.wire, serverInfo: try s.hexField("info"))
            let mine = keyBytes(try ex.exchange.responseKey())
            #expect(h2 == head && b2 == body && k2.response == mine)
        }
    }

    @Test func webSocketMessages() throws {
        let ws = try #require(v["websocket"])
        let req = try v.named("requests", try #require(ws["request"]?.stringValue))
        let c2s = SymmetricKey(data: try req.hexField("ws_c2s_key")), s2c = SymmetricKey(data: try req.hexField("ws_s2c_key"))
        #expect(try ws.hexField("aad") == KE2E.webSocketAAD)
        #expect(ws["types"]?["text"]?.intValue == Int(KE2EWebSocketMessage.textType))
        #expect(ws["types"]?["binary"]?.intValue == Int(KE2EWebSocketMessage.binaryType))
        #expect(ws["close_codes"]?["bad_envelope"]?.intValue == KE2E.closeBadEnvelope)
        #expect(ws["close_codes"]?["unauthorized"]?.intValue == KE2E.closeUnauthorized)
        #expect(ws["close_codes"]?["key_changed"]?.intValue == KE2E.closeKeyChanged)

        func message(_ j: JSON) throws -> KE2EWebSocketMessage {
            if j["type"]?.intValue == 1 { return .text(try #require(j["payload_text"]?.stringValue)) }
            return .binary(try j.hexField("payload"))
        }
        let c2sFirst = try #require(ws["c2s_first"]), s2cFirst = try #require(ws["s2c_first"]), s2cSecond = try #require(ws["s2c_second"])
        for j in [c2sFirst, s2cFirst, s2cSecond] { #expect(try message(j).plaintext == (try j.hexField("pt"))) }

        var phone = KE2EWebSocketCipher(send: c2s, receive: s2c)
        #expect(try phone.open(try s2cFirst.hexField("message")) == (try message(s2cFirst)))
        #expect(try phone.open(try s2cSecond.hexField("message")) == (try message(s2cSecond)))
        // ChaCha20-Poly1305 is deterministic for a key and nonce: the phone's first message is byte-exact
        #expect(try phone.seal(try message(c2sFirst)) == (try c2sFirst.hexField("message")))

        var server = KE2EWebSocketCipher.serverSide(c2s: c2s, s2c: s2c)
        #expect(try server.open(try c2sFirst.hexField("message")) == (try message(c2sFirst)))
        #expect(try server.seal(try message(s2cFirst)) == (try s2cFirst.hexField("message")))
        #expect(try server.seal(try message(s2cSecond)) == (try s2cSecond.hexField("message")))
    }

    @Test func negativeCasesFail() throws {
        var checked = 0
        for n in try v.list("negative") {
            let name = n["name"]?.stringValue ?? "?"
            let expect = n["expect"]?.stringValue ?? ""
            switch n["kind"]?.stringValue {
            case "response":
                var reader = KE2EResponseReader(key: SymmetricKey(data: try n.hexField("key")))
                var got = "opened"
                do { _ = try reader.feed(try n.hexField("stream")); try reader.finish() } catch { got = vectorName(error) }
                #expect(got == expect, "\(name)")
                checked += 1
            case "ws_c2s":
                let key = SymmetricKey(data: try n.hexField("key"))
                var server = KE2EWebSocketCipher.serverSide(c2s: key, s2c: SymmetricKey(size: .bits256))
                var got = "opened"
                do { _ = try server.open(try n.hexField("message")) } catch { got = vectorName(error) }
                #expect(got == expect, "\(name)")
                checked += 1
            case "request":
                // server-side checks (kid, replay, clock) are the server's; the envelope parser and a 0x02 body are checked here
                if name == "bad_version" {
                    #expect(throws: KE2EError.self) { try KE2EEnvelope.parse(try n.hexField("envelope")) }
                    checked += 1
                } else if name.hasPrefix("stream_body") {
                    #expect(throws: (any Error).self, "\(name)") { try openStreamed(try n.hexField("envelope"), serverInfo: try v["server"]!.hexField("info")) }
                    checked += 1
                }
            default:
                Issue.record("unknown negative kind in \(name)")
            }
        }
        #expect(checked >= 10)
    }
}

/// The server's side of a 0x02 request (as src/assistant/e2e.py): open the head with the server key, then the body frames.
private func openStreamed(_ wire: Data, serverInfo: Data) throws -> (KE2ERequestHead, Data, (body: Data, response: Data)) {
    let v = try E2EVectors.load()
    let sk = try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: try v["server"]!.hexField("sk"))
    let env = try KE2EEnvelope.parse(wire)
    guard env.version == KE2E.envelopeVersionStream else { throw KE2EError.badFrame }
    var r = try HPKE.Recipient(privateKey: sk, ciphersuite: KE2E.ciphersuite, info: serverInfo, encapsulatedKey: env.enc)
    let (head, empty) = try KE2EInner.split(try r.open(env.ciphertext, authenticating: KE2E.requestAAD))
    guard empty.isEmpty else { throw KE2EError.badHead }
    let bodyKey = try r.exportSecret(context: KE2E.requestBodyLabel, outputByteCount: 32)
    var reader = KE2EFrameStreamReader(key: bodyKey, aad: KE2E.requestAAD)
    let body = try reader.feed(env.bodyFrames).reduce(Data(), +)
    try reader.finish()
    return (head, body, (keyBytes(bodyKey), keyBytes(try r.exportSecret(context: KE2E.responseLabel, outputByteCount: 32))))
}

extension KE2EExchange {
    /// This app's 0x02 request for `head` and `body`: the head part and the body frames, as they go on the wire.
    static func streamedWire(head: KE2ERequestHead, body: Data, key: KE2EServerKey) throws -> (wire: Data, exchange: KE2EExchange) {
        let ex = try KE2EExchange.sealStreamed(head: head, to: key)
        var w = try ex.bodyWriter()
        return (ex.outerBody + (try w.whole(body)), ex)
    }
}
