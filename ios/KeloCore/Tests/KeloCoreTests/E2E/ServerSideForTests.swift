import Foundation
import CryptoKit
@testable import KeloCore

// The server's side of KE2E, for the tests only (the app never opens an envelope or writes an answer).

/// The outer envelope's parts (for the server side in tests and for checking what goes on the wire). Version 0x01: `ciphertext`
/// holds head and body, `bodyFrames` is empty. Version 0x02: `ciphertext` holds the head only, `bodyFrames` the bytes after it.
struct KE2EEnvelope: Equatable, Sendable {
    let version: UInt8
    let kid: Data
    let enc: Data
    let ciphertext: Data
    let bodyFrames: Data

    static func parse(_ outer: Data) throws -> KE2EEnvelope {
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


/// Writes a KE2E answer (the server's side; here for tests and a local reference server): the head, the body in pieces of at most
/// `KE2E.maxBodyFrame`, the last frame flagged.
struct KE2EResponseWriter: Sendable {
    private var sealer: KE2EFrameSealer

    init(key: SymmetricKey) { sealer = KE2EFrameSealer(key: key, aad: KE2E.responseLabel) }

    /// The whole answer: head (last when the body is empty), then body frames.
    mutating func whole(head: KE2EResponseHead, body: Data) throws -> Data {
        var out = Data()
        let pieces = KE2EBytes.chunks(body, size: KE2E.maxBodyFrame)
        out += try sealer.sealPrefixed(head.json(), flag: pieces.isEmpty ? .last : .more)
        for (i, p) in pieces.enumerated() { out += try sealer.sealPrefixed(p, flag: i == pieces.count - 1 ? .last : .more) }
        return out
    }

    mutating func frame(_ plaintext: Data, last: Bool) throws -> Data {
        try sealer.sealPrefixed(plaintext, flag: last ? .last : .more)
    }
}


extension KE2EResponseHead {
    func json() -> Data {
        let obj: [String: Any] = ["s": status, "h": headers]
        return (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys, .withoutEscapingSlashes])) ?? Data()
    }
}

extension KE2EWebSocketCipher {
    /// The server's view of the same keys (tests, reference server): it sends with s2c and reads with c2s.
    static func serverSide(c2s: SymmetricKey, s2c: SymmetricKey) -> KE2EWebSocketCipher {
        KE2EWebSocketCipher(send: s2c, receive: c2s)
    }
}
