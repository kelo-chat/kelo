import Foundation
import CryptoKit

// KE2E frames (spec §1.4): ChaCha20-Poly1305 with a key from HPKE's exporter, nonce = 4 zero bytes ‖ u64 BE counter (from 0), one
// counter per direction. A frame on the wire is ciphertext ‖ tag; the nonce is never sent.
//
// Answers: each frame is `u32 BE length ‖ frame`, aad = "kelo-e2e v1 res" ‖ flag, flag 0x00 = more follow, 0x01 = last. The flag is
// not on the wire either: it is only in the aad, so a reader tries "more" and then "last", and a cut-off stream (no last frame) is
// always noticed. WebSocket messages: one message = one frame, aad = "kelo-e2e v1 ws", no flag, no length prefix.

public enum KE2EFlag: UInt8, Sendable {
    case more = 0x00
    case last = 0x01
}

enum KE2ENonce {
    static func make(_ counter: UInt64) -> ChaChaPoly.Nonce {
        var bytes = [UInt8](repeating: 0, count: 12)
        for i in 0..<8 { bytes[4 + i] = UInt8(truncatingIfNeeded: counter >> (56 - 8 * UInt64(i))) }
        return try! ChaChaPoly.Nonce(data: bytes)   // 12 bytes: cannot fail
    }
}

/// Seals frames in order (the server's answer side in tests, the phone's WebSocket messages).
public struct KE2EFrameSealer: Sendable {
    let key: SymmetricKey
    let aad: Data
    public private(set) var counter: UInt64 = 0

    public init(key: SymmetricKey, aad: Data) {
        self.key = key
        self.aad = aad
    }

    /// ciphertext ‖ tag of the next frame. `flag` nil = no flag byte in the aad (WebSocket).
    public mutating func seal(_ plaintext: Data, flag: KE2EFlag?) throws -> Data {
        guard counter < UInt64.max else { throw KE2EError.counterExhausted }
        var ad = aad
        if let flag { ad.append(flag.rawValue) }
        let box = try ChaChaPoly.seal(plaintext, using: key, nonce: KE2ENonce.make(counter), authenticating: ad)
        counter += 1
        return box.ciphertext + box.tag
    }

    /// An answer frame with its length prefix: u32 BE ‖ ciphertext ‖ tag.
    public mutating func sealPrefixed(_ plaintext: Data, flag: KE2EFlag) throws -> Data {
        let c = try seal(plaintext, flag: flag)
        return KE2EBytes.u32(UInt32(c.count)) + c
    }
}

/// Opens frames in order; the counter moves only when a frame opened, so a missing, repeated or reordered frame fails.
public struct KE2EFrameOpener: Sendable {
    let key: SymmetricKey
    let aad: Data
    public private(set) var counter: UInt64 = 0

    public init(key: SymmetricKey, aad: Data) {
        self.key = key
        self.aad = aad
    }

    /// Opens a frame sealed with `flag` (nil = no flag byte).
    public mutating func open(_ frame: Data, flag: KE2EFlag?) throws -> Data {
        guard counter < UInt64.max else { throw KE2EError.counterExhausted }
        guard let plain = attempt(frame, flag: flag) else { throw KE2EError.badFrame }
        counter += 1
        return plain
    }

    /// Opens an answer frame whose flag is unknown: (plaintext, isLast).
    public mutating func openEither(_ frame: Data) throws -> (Data, Bool) {
        guard counter < UInt64.max else { throw KE2EError.counterExhausted }
        if let p = attempt(frame, flag: .more) { counter += 1; return (p, false) }
        if let p = attempt(frame, flag: .last) { counter += 1; return (p, true) }
        throw KE2EError.badFrame
    }

    private func attempt(_ frame: Data, flag: KE2EFlag?) -> Data? {
        guard frame.count >= KE2E.tagBytes else { return nil }
        var ad = aad
        if let flag { ad.append(flag.rawValue) }
        let f = Data(frame)   // own indices from 0
        guard let box = try? ChaChaPoly.SealedBox(nonce: KE2ENonce.make(counter), ciphertext: f.prefix(f.count - KE2E.tagBytes),
                                                  tag: f.suffix(KE2E.tagBytes)) else { return nil }
        return try? ChaChaPoly.open(box, using: key, authenticating: ad)
    }
}

/// The answer head in frame 0: `{"s": 200, "h": {"content-type": …, "etag": …, "retry-after": …}}`.
public struct KE2EResponseHead: Equatable, Sendable {
    public var status: Int
    /// Header names in lower case.
    public var headers: [String: String]

    public init(status: Int, headers: [String: String] = [:]) {
        self.status = status
        self.headers = Dictionary(headers.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { _, b in b })
    }

    public static func parse(_ data: Data) throws -> KE2EResponseHead {
        guard let json = try? JSON.parse(data), let s = json["s"]?.intValue, (100...599).contains(s) else { throw KE2EError.badHead }
        var h: [String: String] = [:]
        for (k, v) in json["h"]?.objectValue ?? [:] { if let t = v.text { h[k.lowercased()] = t } }
        return KE2EResponseHead(status: s, headers: h)
    }

    public func json() -> Data {
        let obj: [String: Any] = ["s": status, "h": headers]
        return (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data()
    }
}

/// One piece of an answer as it comes in.
public enum KE2EResponsePart: Equatable, Sendable {
    case head(KE2EResponseHead)
    /// Body bytes (for an event stream: one frame per event).
    case body(Data)
}

/// Reads length-prefixed frames (u32 BE ‖ ciphertext ‖ tag, flag in the aad) as bytes arrive: the common part of answers and of a
/// streamed (0x02) request body. `finish()` throws `.truncated` when the last frame never came or half a frame is left.
public struct KE2EFrameStreamReader: Sendable {
    private var opener: KE2EFrameOpener
    private var buffer = Data()
    private let maxFrame: Int
    /// The last frame opened: anything more is an error.
    public private(set) var complete = false

    public init(key: SymmetricKey, aad: Data, maxFrame: Int = KE2E.maxFrame) {
        opener = KE2EFrameOpener(key: key, aad: aad)
        self.maxFrame = maxFrame
    }

    /// Adds received bytes; returns the plaintexts of the frames they completed, in order.
    public mutating func feed<S: Sequence>(_ bytes: S) throws -> [Data] where S.Element == UInt8 {
        buffer.append(contentsOf: bytes)
        var out: [Data] = []
        while true {
            if complete {
                if !buffer.isEmpty { throw KE2EError.trailingData }
                return out
            }
            guard buffer.count >= 4 else { return out }
            let n = Int(KE2EBytes.readU32(buffer, at: buffer.startIndex))
            guard n <= maxFrame else { throw KE2EError.frameTooLarge }
            guard buffer.count >= 4 + n else { return out }
            let start = buffer.startIndex + 4
            let frame = buffer.subdata(in: start..<(start + n))
            buffer.removeSubrange(buffer.startIndex..<(start + n))
            let (plain, last) = try opener.openEither(frame)
            out.append(plain)
            if last { complete = true }
        }
    }

    public func finish() throws {
        guard complete, buffer.isEmpty else { throw KE2EError.truncated }
    }
}

/// Reads a KE2E answer body as bytes arrive (whole answers, server-sent events, downloads): length-prefixed frames, the head first,
/// then the body; `finish()` at the end of the HTTP body throws `.truncated` when the last frame never came.
public struct KE2EResponseReader: Sendable {
    private var frames: KE2EFrameStreamReader
    public private(set) var head: KE2EResponseHead?
    public var complete: Bool { frames.complete }

    public init(key: SymmetricKey, maxFrame: Int = KE2E.maxFrame) {
        frames = KE2EFrameStreamReader(key: key, aad: KE2E.responseLabel, maxFrame: maxFrame)
    }

    /// Adds received bytes; returns the parts they completed, in order.
    public mutating func feed<S: Sequence>(_ bytes: S) throws -> [KE2EResponsePart] where S.Element == UInt8 {
        var out: [KE2EResponsePart] = []
        for plain in try frames.feed(bytes) {
            if head == nil {
                let h = try KE2EResponseHead.parse(plain)
                head = h
                out.append(.head(h))
            } else {
                out.append(.body(plain))
            }
        }
        return out
    }

    /// The HTTP body ended: fine only after the last frame with nothing left over.
    public func finish() throws { try frames.finish() }

    /// A whole answer at once (ordinary JSON calls): its head and the body pieces joined.
    public static func readWhole(_ data: Data, key: SymmetricKey) throws -> (KE2EResponseHead, Data) {
        var r = KE2EResponseReader(key: key)
        var body = Data()
        for part in try r.feed(data) { if case .body(let d) = part { body.append(d) } }
        try r.finish()
        guard let h = r.head else { throw KE2EError.truncated }
        return (h, body)
    }
}

/// Writes a KE2E answer (the server's side; here for tests and a local reference server): the head, the body in pieces of at most
/// `KE2E.maxBodyFrame`, the last frame flagged.
public struct KE2EResponseWriter: Sendable {
    private var sealer: KE2EFrameSealer

    public init(key: SymmetricKey) { sealer = KE2EFrameSealer(key: key, aad: KE2E.responseLabel) }

    /// The whole answer: head (last when the body is empty), then body frames.
    public mutating func whole(head: KE2EResponseHead, body: Data) throws -> Data {
        var out = Data()
        let pieces = KE2EBytes.chunks(body, size: KE2E.maxBodyFrame)
        out += try sealer.sealPrefixed(head.json(), flag: pieces.isEmpty ? .last : .more)
        for (i, p) in pieces.enumerated() { out += try sealer.sealPrefixed(p, flag: i == pieces.count - 1 ? .last : .more) }
        return out
    }

    public mutating func frame(_ plaintext: Data, last: Bool) throws -> Data {
        try sealer.sealPrefixed(plaintext, flag: last ? .last : .more)
    }
}

enum KE2EBytes {
    static func u32(_ v: UInt32) -> Data {
        Data([UInt8(truncatingIfNeeded: v >> 24), UInt8(truncatingIfNeeded: v >> 16), UInt8(truncatingIfNeeded: v >> 8), UInt8(truncatingIfNeeded: v)])
    }

    static func readU32(_ d: Data, at i: Data.Index) -> UInt32 {
        UInt32(d[i]) << 24 | UInt32(d[i + 1]) << 16 | UInt32(d[i + 2]) << 8 | UInt32(d[i + 3])
    }

    static func chunks(_ d: Data, size: Int) -> [Data] {
        guard !d.isEmpty else { return [] }
        return stride(from: 0, to: d.count, by: size).map { d.subdata(in: (d.startIndex + $0)..<(d.startIndex + min($0 + size, d.count))) }
    }
}
