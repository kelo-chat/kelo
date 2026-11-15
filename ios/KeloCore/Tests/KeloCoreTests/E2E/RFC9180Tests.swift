import Foundation
import CryptoKit
import Testing
@testable import KeloCore

func hex(_ s: String) -> Data {
    let chars = Array(s.filter { !$0.isWhitespace }.utf8)
    var out = Data(capacity: chars.count / 2)
    var i = 0
    while i + 1 < chars.count {
        out.append(UInt8(String(decoding: chars[i...i + 1], as: UTF8.self), radix: 16)!)
        i += 2
    }
    return out
}

func hexString(_ d: Data) -> String { d.map { String(format: "%02x", $0) }.joined() }

func keyBytes(_ k: SymmetricKey) -> Data { k.withUnsafeBytes { Data($0) } }

/// RFC 9180 Appendix A.2.1: DHKEM(X25519, HKDF-SHA256), HKDF-SHA256, ChaCha20Poly1305, base mode, exactly KE2E's suite. CryptoKit's
/// sender always picks a random ephemeral key, so the deterministic checks run on the recipient side (skRm + enc); the sender is
/// checked by a round trip.
@Suite struct RFC9180Tests {
    let info = hex("4f6465206f6e2061204772656369616e2055726e")
    let skRm = hex("8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb")
    let pkRm = hex("4310ee97d88cc1f088a5576c77ab0cf5c3ac797f3d95139c6c84b5429c59662a")
    let enc = hex("1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a")
    let pt = hex("4265617574792069732074727574682c20747275746820626561757479")
    let key = hex("ad2744de8e17f4ebba575b3f5f5a8fa1f69c2a07f6e7500bc60ca6e3e3ec1c91")
    let baseNonce = hex("5c4d98150661b848853b547f")
    /// (sequence, aad, ct)
    let encryptions: [(UInt64, Data, Data)] = [
        (0, hex("436f756e742d30"), hex("1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db21993c62ce81883d2dd1b51a28")),
        (1, hex("436f756e742d31"), hex("6b53c051e4199c518de79594e1c4ab18b96f081549d45ce015be002090bb119e85285337cc95ba5f59992dc98c")),
        (2, hex("436f756e742d32"), hex("71146bd6795ccc9c49ce25dda112a48f202ad220559502cef1f34271e0cb4b02b4f10ecac6f48c32f878fae86b")),
        (4, hex("436f756e742d34"), hex("63357a2aa291f5a4e5f27db6baa2af8cf77427c7c1a909e0b37214dd47db122bb153495ff0b02e9e54a50dbe16")),
        (255, hex("436f756e742d323535"), hex("18ab939d63ddec9f6ac2b60d61d36a7375d2070c9b683861110757062c52b8880a5f6b3936da9cd6c23ef2a95c")),
        (256, hex("436f756e742d323536"), hex("7a4a13e9ef23978e2c520fd4d2e757514ae160cd0cd05e556ef692370ca53076214c0c40d4c728d6ed9e727a5b")),
    ]
    let exports: [(Data, Data)] = [
        (Data(), hex("4bbd6243b8bb54cec311fac9df81841b6fd61f56538a775e7c80a9f40160606e")),
        (hex("00"), hex("8c1df14732580e5501b00f82b10a1647b40713191b7c1240ac80e2b68808ba69")),
        (hex("54657374436f6e74657874"), hex("5acb09211139c43b3090489a9da433e8a30ee7188ba8b0a9a1ccf0c229283e53")),
    ]

    func recipient() throws -> HPKE.Recipient {
        try HPKE.Recipient(privateKey: Curve25519.KeyAgreement.PrivateKey(rawRepresentation: skRm), ciphersuite: KE2E.ciphersuite,
                           info: info, encapsulatedKey: enc)
    }

    @Test func theSuiteIsKE2Es() {
        #expect(KE2E.ciphersuite.kem == .Curve25519_HKDF_SHA256)
        #expect(KE2E.ciphersuite.kdf == .HKDF_SHA256)
        #expect(KE2E.ciphersuite.aead == .chaChaPoly)
    }

    @Test func recipientKeyMatches() throws {
        #expect(try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: skRm).publicKey.rawRepresentation == pkRm)
    }

    @Test func recipientOpensTheFirstThreeInOrder() throws {
        var r = try recipient()
        for (_, aad, ct) in encryptions.prefix(3) {
            #expect(try r.open(ct, authenticating: aad) == pt)
        }
    }

    @Test func recipientRefusesOutOfOrder() throws {
        var r = try recipient()
        #expect(throws: (any Error).self) { try r.open(encryptions[1].2, authenticating: encryptions[1].1) }
    }

    /// The later sequence numbers cannot be reached through CryptoKit's context (it opens strictly in order), so they check the frame
    /// layer's own ChaCha20-Poly1305 use with the RFC's key and nonces: the same primitive the KE2E frames run on.
    @Test func everySequenceWithThePrimitive() throws {
        for (seq, aad, ct) in encryptions {
            var nonce = [UInt8](baseNonce)
            for i in 0..<8 { nonce[11 - i] ^= UInt8(truncatingIfNeeded: seq >> (8 * UInt64(i))) }
            let box = try ChaChaPoly.seal(pt, using: SymmetricKey(data: key), nonce: ChaChaPoly.Nonce(data: nonce), authenticating: aad)
            #expect(box.ciphertext + box.tag == ct, "seq \(seq)")
        }
    }

    @Test func exportedValues() throws {
        let r = try recipient()
        for (ctx, value) in exports {
            #expect(keyBytes(try r.exportSecret(context: ctx, outputByteCount: 32)) == value)
        }
    }

    @Test func senderRoundTrip() throws {
        let pub = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: pkRm)
        var s = try HPKE.Sender(recipientKey: pub, ciphersuite: KE2E.ciphersuite, info: info)
        let ct = try s.seal(pt, authenticating: encryptions[0].1)
        var r = try HPKE.Recipient(privateKey: Curve25519.KeyAgreement.PrivateKey(rawRepresentation: skRm), ciphersuite: KE2E.ciphersuite,
                                   info: info, encapsulatedKey: s.encapsulatedKey)
        #expect(try r.open(ct, authenticating: encryptions[0].1) == pt)
        for (ctx, _) in exports {
            #expect(keyBytes(try s.exportSecret(context: ctx, outputByteCount: 32)) == keyBytes(try r.exportSecret(context: ctx, outputByteCount: 32)))
        }
    }
}
