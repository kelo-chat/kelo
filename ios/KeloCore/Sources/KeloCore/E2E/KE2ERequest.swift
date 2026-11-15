import Foundation
import CryptoKit

/// The inner request head `H` (spec §1.4): method, path with query, the few headers that go inside, a timestamp and a random nonce
/// (the server refuses a `ts` more than 300 s off and a nonce seen in the last 10 minutes).
public struct KE2ERequestHead: Equatable, Sendable {
    /// The only headers that go inside (lower case); everything else the URLRequest carries is dropped. `x-kelo-*` are allowed too.
    public static let insideHeaders: Set<String> = ["authorization", "content-type", "accept", "accept-language", "if-none-match", "range"]

    public static func goesInside(_ name: String) -> Bool {
        let n = name.lowercased()
        return insideHeaders.contains(n) || n.hasPrefix("x-kelo-")
    }

    public var method: String
    /// Path and query as the server routes them, e.g. "/api/talk?x=1".
    public var path: String
    public var headers: [String: String]
    public var ts: Int64
    /// 16 random bytes, base64url.
    public var nonce: String

    public init(method: String, path: String, headers: [String: String], ts: Int64, nonce: String) {
        self.method = method.uppercased()
        self.path = path
        var h: [String: String] = [:]
        for (k, v) in headers where KE2ERequestHead.goesInside(k) { h[k.lowercased()] = v }
        self.headers = h
        self.ts = ts
        self.nonce = nonce
    }

    /// A head for a request made now, with a fresh nonce.
    public static func make(method: String, path: String, headers: [String: String], now: Date = Date()) -> KE2ERequestHead {
        var bytes = [UInt8](repeating: 0, count: 16)
        var rng = SystemRandomNumberGenerator()
        for i in bytes.indices { bytes[i] = UInt8.random(in: 0...255, using: &rng) }
        return KE2ERequestHead(method: method, path: path, headers: headers, ts: Int64(now.timeIntervalSince1970.rounded(.down)),
                               nonce: Base64URL.encode(Data(bytes)))
    }

    /// The head for a URLRequest the API client built (its path + query, the headers that go inside).
    public static func of(_ request: URLRequest, now: Date = Date()) -> KE2ERequestHead? {
        guard let url = request.url, let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        var path = c.percentEncodedPath.isEmpty ? "/" : c.percentEncodedPath
        if let q = c.percentEncodedQuery { path += "?" + q }
        return make(method: request.httpMethod ?? "GET", path: path, headers: request.allHTTPHeaderFields ?? [:], now: now)
    }

    /// UTF-8 JSON, keys sorted (any order is fine for the server; sorted keeps tests stable).
    public func json() -> Data {
        let obj: [String: Any] = ["m": method, "p": path, "h": headers, "ts": ts, "n": nonce]
        return (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data()
    }

    public static func parse(_ data: Data) throws -> KE2ERequestHead {
        guard let j = try? JSON.parse(data), let m = j["m"]?.stringValue, let p = j["p"]?.stringValue, let ts = j["ts"]?.int64Value,
              let n = j["n"]?.stringValue else { throw KE2EError.badHead }
        var h: [String: String] = [:]
        for (k, v) in j["h"]?.objectValue ?? [:] { if let s = v.stringValue { h[k] = s } }
        return KE2ERequestHead(method: m, path: p, headers: h, ts: ts, nonce: n)
    }
}

/// The inner request = u32 BE length of H ‖ H ‖ body.
public enum KE2EInner {
    public static func build(head: KE2ERequestHead, body: Data) -> Data {
        let h = head.json()
        var out = KE2EBytes.u32(UInt32(h.count))
        out.reserveCapacity(4 + h.count + body.count)
        out.append(h)
        out.append(body)
        return out
    }

    public static func split(_ inner: Data) throws -> (KE2ERequestHead, Data) {
        let d = Data(inner)
        guard d.count >= 4 else { throw KE2EError.badHead }
        let n = Int(KE2EBytes.readU32(d, at: 0))
        guard d.count >= 4 + n else { throw KE2EError.badHead }
        return (try KE2ERequestHead.parse(d.subdata(in: 4..<(4 + n))), d.subdata(in: (4 + n)..<d.count))
    }
}

/// One sealed request and what the phone needs afterwards to read its answer. Keeps the HPKE sender context (for the exporter only);
/// one exchange per HTTP call or WebSocket, never reused.
public struct KE2EExchange: Sendable {
    /// The outer request body: 0x01 ‖ kid (8) ‖ enc (32) ‖ ct. POST it to `KE2E.path` with `Content-Type: KE2E.contentType`, no
    /// Authorization header, no query.
    public let outerBody: Data
    public let serverKey: KE2EServerKey
    private let sender: HPKE.Sender

    /// Seals `head` and `body` to the server's key. The inner copy is wiped after sealing (a body may carry card digits); the caller
    /// wipes its own `body`.
    public static func seal(head: KE2ERequestHead, body: Data, to key: KE2EServerKey) throws -> KE2EExchange {
        var sender = try HPKE.Sender(recipientKey: key.hpkeKey, ciphersuite: KE2E.ciphersuite, info: KE2E.info(kid: key.kid))
        var inner = KE2EInner.build(head: head, body: body)
        defer { inner.resetBytes(in: 0..<inner.count) }
        let ct = try sender.seal(inner, authenticating: KE2E.requestAAD)
        var outer = Data([KE2E.envelopeVersion])
        outer.reserveCapacity(KE2E.envelopeHeaderBytes + ct.count)
        outer.append(key.kidBytes)
        outer.append(sender.encapsulatedKey)
        outer.append(ct)
        return KE2EExchange(outerBody: outer, serverKey: key, sender: sender)
    }

    /// A streamed request (envelope 0x02, spec 5.2) for a body too large to hold in memory: `outerBody` is only the head part,
    /// 0x02 ‖ kid ‖ enc ‖ u32 BE len(ct) ‖ ct, with `ct` sealing the head and an empty body. The body follows on the same HTTP body
    /// as frames from `bodyWriter()`; the answer is read as for 0x01.
    public static func sealStreamed(head: KE2ERequestHead, to key: KE2EServerKey) throws -> KE2EExchange {
        var sender = try HPKE.Sender(recipientKey: key.hpkeKey, ciphersuite: KE2E.ciphersuite, info: KE2E.info(kid: key.kid))
        let ct = try sender.seal(KE2EInner.build(head: head, body: Data()), authenticating: KE2E.requestAAD)
        var outer = Data([KE2E.envelopeVersionStream])
        outer.append(key.kidBytes)
        outer.append(sender.encapsulatedKey)
        outer.append(KE2EBytes.u32(UInt32(ct.count)))
        outer.append(ct)
        return KE2EExchange(outerBody: outer, serverKey: key, sender: sender)
    }

    /// The body frames of a streamed request: `Export("kelo-e2e v1 req body", 32)`, aad "kelo-e2e v1 req" ‖ flag.
    public func bodyWriter() throws -> KE2ERequestBodyWriter {
        KE2ERequestBodyWriter(key: try sender.exportSecret(context: KE2E.requestBodyLabel, outputByteCount: 32))
    }

    /// `ctx.Export("kelo-e2e v1 res", 32)`.
    public func responseKey() throws -> SymmetricKey {
        try sender.exportSecret(context: KE2E.responseLabel, outputByteCount: 32)
    }

    /// A reader for this exchange's answer.
    public func responseReader() throws -> KE2EResponseReader { KE2EResponseReader(key: try responseKey()) }

    /// The live view's two directions, when this exchange opened `KE2E.webSocketPath` (its first message is `outerBody`).
    public func webSocketCipher() throws -> KE2EWebSocketCipher {
        KE2EWebSocketCipher(send: try sender.exportSecret(context: KE2E.webSocketC2S, outputByteCount: 32),
                            receive: try sender.exportSecret(context: KE2E.webSocketS2C, outputByteCount: 32))
    }
}

/// A streamed request's body as frames, at most `KE2E.maxBodyFrame` plaintext each, the last flagged; an empty body is one empty
/// last frame.
public struct KE2ERequestBodyWriter: Sendable {
    private var sealer: KE2EFrameSealer

    init(key: SymmetricKey) { sealer = KE2EFrameSealer(key: key, aad: KE2E.requestAAD) }

    /// One frame (u32 BE length ‖ ciphertext ‖ tag). `piece` must be at most `KE2E.maxBodyFrame` bytes.
    public mutating func frame(_ piece: Data, last: Bool) throws -> Data {
        precondition(piece.count <= KE2E.maxBodyFrame, "a body frame holds at most 64 KiB")
        return try sealer.sealPrefixed(piece, flag: last ? .last : .more)
    }

    /// A body held in memory as all its frames (tests, small streamed bodies).
    public mutating func whole(_ body: Data) throws -> Data {
        let pieces = KE2EBytes.chunks(body, size: KE2E.maxBodyFrame)
        if pieces.isEmpty { return try frame(Data(), last: true) }
        var out = Data()
        for (i, p) in pieces.enumerated() { out += try frame(p, last: i == pieces.count - 1) }
        return out
    }
}

/// The outer envelope's parts (for the server side in tests and for checking what goes on the wire). Version 0x01: `ciphertext`
/// holds head and body, `bodyFrames` is empty. Version 0x02: `ciphertext` holds the head only, `bodyFrames` the bytes after it.
public struct KE2EEnvelope: Equatable, Sendable {
    public let version: UInt8
    public let kid: Data
    public let enc: Data
    public let ciphertext: Data
    public let bodyFrames: Data

    public static func parse(_ outer: Data) throws -> KE2EEnvelope {
        let d = Data(outer)
        guard d.count > KE2E.envelopeHeaderBytes + KE2E.tagBytes else { throw KE2EError.badFrame }
        let kid = d.subdata(in: 1..<9), enc = d.subdata(in: 9..<41)
        switch d[0] {
        case KE2E.envelopeVersion:
            return KE2EEnvelope(version: d[0], kid: kid, enc: enc, ciphertext: d.subdata(in: 41..<d.count), bodyFrames: Data())
        case KE2E.envelopeVersionStream:
            guard d.count >= 45 else { throw KE2EError.badFrame }
            let n = Int(KE2EBytes.readU32(d, at: 41))
            guard n >= KE2E.tagBytes, d.count >= 45 + n else { throw KE2EError.badFrame }
            return KE2EEnvelope(version: d[0], kid: kid, enc: enc, ciphertext: d.subdata(in: 45..<(45 + n)),
                                bodyFrames: d.subdata(in: (45 + n)..<d.count))
        default:
            throw KE2EError.badFrame
        }
    }
}

/// One live-view WebSocket message inside KE2E. The first plaintext byte is its type (spec 5.8): 0x01 text (UTF-8 follows), 0x02
/// binary (a JPEG of the live view). Any other type is an error.
public enum KE2EWebSocketMessage: Equatable, Sendable {
    case text(String)
    case binary(Data)

    public static let textType: UInt8 = 0x01
    public static let binaryType: UInt8 = 0x02

    public var plaintext: Data {
        switch self {
        case .text(let t): return Data([Self.textType]) + Data(t.utf8)
        case .binary(let d): return Data([Self.binaryType]) + d
        }
    }

    public static func parse(_ plaintext: Data) throws -> KE2EWebSocketMessage {
        let p = Data(plaintext)
        guard let type = p.first else { throw KE2EError.badFrame }
        switch type {
        case textType:
            guard let t = String(data: p.dropFirst(), encoding: .utf8) else { throw KE2EError.badFrame }
            return .text(t)
        case binaryType: return .binary(Data(p.dropFirst()))
        default: throw KE2EError.badFrame
        }
    }
}

/// The live view over KE2E: after the request envelope (the first WebSocket message, binary), every message both ways is one
/// binary frame, phone → server with the c2s key, server → phone with the s2c key, each with its own counter. If the envelope
/// fails the server closes with `KE2E.closeBadEnvelope` / `closeUnauthorized` / `closeKeyChanged`.
public struct KE2EWebSocketCipher: Sendable {
    private var sealer: KE2EFrameSealer
    private var opener: KE2EFrameOpener

    public init(send: SymmetricKey, receive: SymmetricKey) {
        sealer = KE2EFrameSealer(key: send, aad: KE2E.webSocketAAD)
        opener = KE2EFrameOpener(key: receive, aad: KE2E.webSocketAAD)
    }

    /// The server's view of the same keys (tests, reference server): it sends with s2c and reads with c2s.
    public static func serverSide(c2s: SymmetricKey, s2c: SymmetricKey) -> KE2EWebSocketCipher {
        KE2EWebSocketCipher(send: s2c, receive: c2s)
    }

    /// The binary WebSocket message to send for `message`.
    public mutating func seal(_ message: KE2EWebSocketMessage) throws -> Data { try sealer.seal(message.plaintext, flag: nil) }

    /// A received binary WebSocket message. A frame that does not open or an unknown type is `.badFrame` (close the socket).
    public mutating func open(_ data: Data) throws -> KE2EWebSocketMessage { try KE2EWebSocketMessage.parse(try opener.open(data, flag: nil)) }
}
