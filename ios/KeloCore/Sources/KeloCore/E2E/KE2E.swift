import Foundation
import CryptoKit

// KE2E v1: the app talks to its own Kelo server end to end (docs/security/e2e-and-phone-key.md §1). Cloudflare, the operator server and
// anything else on the path see only ciphertext. Standard parts only: HPKE (RFC 9180, CryptoKit) for the request, ChaCha20-Poly1305
// frames keyed by HPKE's exporter for the answer and the live-view WebSocket.
//
// This folder is the crypto core: no networking, no storage. The one place it plugs into the app is a transport that wraps
// `HTTPTransport` (see ios agent notes); screens never see it.

/// The constants of KE2E v1. Every label is ASCII and part of the wire format: changing one breaks every server.
public enum KE2E {
    /// DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / ChaCha20-Poly1305, base mode (KEM 0x0020, KDF 0x0001, AEAD 0x0003).
    public static let ciphersuite = HPKE.Ciphersuite.Curve25519_SHA256_ChachaPoly
    /// First byte of the outer request body: 0x01 = the body is inside the HPKE ciphertext (normal calls).
    public static let envelopeVersion: UInt8 = 0x01
    /// 0x02 = streamed body (large uploads, spec 5.2): the HPKE ciphertext seals only the head (empty body), the body follows as
    /// frames keyed by `Export("kelo-e2e v1 req body", 32)`.
    public static let envelopeVersionStream: UInt8 = 0x02
    public static let contentType = "application/x-kelo-e2e"
    /// The outer request goes here (POST); the live view's upgrade goes to `webSocketPath`.
    public static let path = "/e2e"
    public static let webSocketPath = "/e2e/ws"
    /// Plaintext on purpose: the server's public key for trust on first use (`KE2EInfo`).
    public static let infoPath = "/api/e2e/info"
    /// The plaintext 409 answer's `code` when the server does not know the `kid` (its key changed): the app says "pair again".
    public static let keyChangedCode = "e2e_key_changed"

    static let infoPrefix = "kelo-e2e v1 "
    static let requestAAD = Data("kelo-e2e v1 req".utf8)
    static let requestBodyLabel = Data("kelo-e2e v1 req body".utf8)
    static let responseLabel = Data("kelo-e2e v1 res".utf8)
    static let webSocketC2S = Data("kelo-e2e v1 ws c2s".utf8)
    static let webSocketS2C = Data("kelo-e2e v1 ws s2c".utf8)
    static let webSocketAAD = Data("kelo-e2e v1 ws".utf8)

    /// Bytes in the outer request before the HPKE ciphertext: version (1) + kid (8) + enc (32).
    public static let envelopeHeaderBytes = 1 + 8 + 32
    /// The longest body piece a sender puts in one frame (large uploads and downloads).
    public static let maxBodyFrame = 64 * 1024
    /// The longest frame a reader accepts (a guard against a broken or hostile length prefix; one SSE event or one answer head is
    /// far smaller).
    public static let maxFrame = 8 * 1024 * 1024
    /// ChaCha20-Poly1305's tag.
    public static let tagBytes = 16

    /// WebSocket close codes when the first envelope fails (spec 5.8): it does not open / the inner request is not signed in (or the
    /// ticket is refused) / the server's key changed (pair again).
    public static let closeBadEnvelope = 4400
    public static let closeUnauthorized = 4401
    public static let closeKeyChanged = 4409
    /// The plaintext 400 answer's `code` when `ts` is outside ±300 s; its `now` is the server's clock (spec 5.13, `KE2EClock`).
    public static let clockCode = "e2e_clock"

    /// HPKE `info` = "kelo-e2e v1 " + kid (16 lowercase hex characters).
    public static func info(kid: String) -> Data { Data((infoPrefix + kid).utf8) }
}

/// What can go wrong in KE2E. None of the cases carries key material or plaintext.
public enum KE2EError: Error, Equatable, Sendable {
    /// Not a usable server public key (not 32 bytes, not base64url, or `/api/e2e/info` whose kid/fingerprint do not match its key).
    case badKey
    /// A frame did not open: wrong key, a changed byte, a frame missing in between (counter gap) or out of order.
    case badFrame
    /// The stream ended without its last frame (cut off), or with half a frame.
    case truncated
    /// Bytes after the last frame.
    case trailingData
    /// A length prefix above `KE2E.maxFrame`.
    case frameTooLarge
    /// Frame 0 is not a readable answer head, or an inner request head is not readable.
    case badHead
    /// The counter ran out (2^64 frames; never in practice).
    case counterExhausted
    /// The server does not know this phone's pinned key (409 `e2e_key_changed`): pair again.
    case keyChanged
}

/// The server's static X25519 public key as the app pins it (spec §1.2), with the ids derived from it.
public struct KE2EServerKey: Hashable, Sendable, Codable {
    /// 32 bytes (`pkS`).
    public let publicKey: Data

    public init(publicKey: Data) throws {
        guard publicKey.count == 32, (try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: publicKey)) != nil else {
            throw KE2EError.badKey
        }
        self.publicKey = publicKey
    }

    /// From the pairing link's `k` or `/api/e2e/info`'s `pk`: base64url, padding optional.
    public init(base64url text: String) throws {
        guard let raw = Base64URL.decode(text) else { throw KE2EError.badKey }
        try self.init(publicKey: raw)
    }

    public var base64url: String { Base64URL.encode(publicKey) }

    /// First 8 bytes of SHA-256(pkS): the outer envelope carries these bytes.
    public var kidBytes: Data { Data(SHA256.hash(data: publicKey).prefix(8)) }
    /// The same 8 bytes as 16 lowercase hex characters (in HPKE `info` and `/api/e2e/info`).
    public var kid: String { kidBytes.map { String(format: "%02x", $0) }.joined() }

    /// For people (My → Devices, `assistant phone fingerprint`, the server's QR page): SHA-256(pkS), first 15 bytes, base32 without
    /// padding, in groups of four letters ("ABCD-EFGH-IJKL-MNOP-QRST-UVWX").
    public var fingerprint: String {
        let text = Base32.encode(Data(SHA256.hash(data: publicKey).prefix(15)))
        var groups: [String] = []
        var i = text.startIndex
        while i < text.endIndex {
            let j = text.index(i, offsetBy: 4, limitedBy: text.endIndex) ?? text.endIndex
            groups.append(String(text[i..<j]))
            i = j
        }
        return groups.joined(separator: "-")
    }

    var hpkeKey: Curve25519.KeyAgreement.PublicKey {
        // checked in init
        try! Curve25519.KeyAgreement.PublicKey(rawRepresentation: publicKey)
    }

    public init(from decoder: Decoder) throws {
        let text = try decoder.singleValueContainer().decode(String.self)
        try self.init(base64url: text)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(base64url)
    }
}

/// `GET /api/e2e/info` (plaintext): `{"v": 1, "kid", "pk", "fingerprint"}`. Used for trust on first use (typed codes, devices paired
/// before KE2E). The key is taken only when its own kid and fingerprint agree with what the answer says.
public enum KE2EInfo {
    public static func parse(_ json: JSON) throws -> KE2EServerKey {
        guard json["v"]?.intValue == 1, let pk = json["pk"]?.stringValue else { throw KE2EError.badKey }
        let key = try KE2EServerKey(base64url: pk)
        if let kid = json["kid"]?.stringValue, kid.lowercased() != key.kid { throw KE2EError.badKey }
        if let fp = json["fingerprint"]?.stringValue, fp.uppercased() != key.fingerprint { throw KE2EError.badKey }
        return key
    }
}

/// The phone's correction to the server's clock for `ts` (spec 5.13): a plaintext `400 {"code": "e2e_clock", "now": …}` sets the
/// offset once and the call is sent again once; a second clock refusal in a row is an error.
public struct KE2EClock: Sendable, Equatable {
    /// Seconds to add to the phone's clock.
    public private(set) var offset: Int64 = 0

    public init(offset: Int64 = 0) { self.offset = offset }

    public func now(_ phone: Date = Date()) -> Date { phone.addingTimeInterval(TimeInterval(offset)) }

    /// The server's `now` from the refusal; returns false when the answer carries none (then nothing to retry).
    public mutating func correct(serverNow: Int64?, phone: Date = Date()) -> Bool {
        guard let serverNow else { return false }
        offset = serverNow - Int64(phone.timeIntervalSince1970.rounded(.down))
        return true
    }

    /// The server's `now` in a refusal body, when it is the clock refusal.
    public static func serverNow(in body: Data) -> Int64? {
        guard let j = try? JSON.parse(body), j["code"]?.stringValue == KE2E.clockCode else { return nil }
        return j["now"]?.int64Value
    }
}

/// What the app keeps per signed-in server once it knows the key (next to the session in the Keychain): the key and whether the owner
/// verified it (came from a scanned QR / pairing link, or the fingerprint was compared). `verified == false` shows "key not verified".
public struct KE2EPin: Equatable, Sendable, Codable {
    public var key: KE2EServerKey
    public var verified: Bool

    public init(key: KE2EServerKey, verified: Bool) {
        self.key = key
        self.verified = verified
    }

    /// A pairing link's `k` for the same server: verified if it is the pinned key; a different key replaces the pin only through
    /// "pair again" (the caller decides), never silently.
    public func scanned(_ key: KE2EServerKey) -> KE2EPin? { key == self.key ? KE2EPin(key: key, verified: true) : nil }
}

/// base64url (RFC 4648 §5) without padding; decoding takes it with or without padding and refuses anything else.
public enum Base64URL {
    public static func encode(_ data: Data) -> String {
        data.base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func decode(_ text: String) -> Data? {
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        while s.hasSuffix("=") { s.removeLast() }
        guard !s.isEmpty, s.utf8.allSatisfy({ ($0 >= 0x41 && $0 <= 0x5A) || ($0 >= 0x61 && $0 <= 0x7A) || ($0 >= 0x30 && $0 <= 0x39) || $0 == 0x2D || $0 == 0x5F })
        else { return nil }
        if s.count % 4 == 1 { return nil }
        s = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        s += String(repeating: "=", count: (4 - s.count % 4) % 4)
        return Data(base64Encoded: s)
    }
}

/// base32 (RFC 4648 §6), upper case, no padding.
public enum Base32 {
    static let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    public static func encode(_ data: Data) -> String {
        var out = ""
        var buffer: UInt32 = 0
        var bits = 0
        for b in data {
            buffer = (buffer << 8) | UInt32(b)
            bits += 8
            while bits >= 5 {
                out.append(alphabet[Int((buffer >> UInt32(bits - 5)) & 31)])
                bits -= 5
            }
        }
        if bits > 0 { out.append(alphabet[Int((buffer << UInt32(5 - bits)) & 31)]) }
        return out
    }
}
