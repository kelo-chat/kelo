import Foundation
import Testing
@testable import KeloCore

/// The Keyguard wrap with the software key: the same ECIES (SecKeyCreateEncryptedData, X9.63-SHA256 AES-GCM, cofactor, variable IV)
/// the Secure Enclave key runs, without the Enclave and without a prompt.
@Suite struct KeyguardTests {
    let master = Data((0..<44).map { UInt8($0) })   // a Fernet key is 44 characters of base64

    @Test func wrapAndUnwrap() async throws {
        let k = SoftwareKeyguardKey()
        #expect(!k.exists())
        try k.create()
        #expect(k.exists())
        let blob = try k.wrap(master)
        #expect(blob.first == KeyguardBlob.iPhoneFormat)
        #expect(blob.count == master.count + KeyguardBlob.overhead)
        #expect(blob.range(of: master) == nil)
        #expect(try await k.unwrap(blob, reason: "unlock") == master)
    }

    @Test func eachWrapIsFresh() throws {
        let k = SoftwareKeyguardKey()
        try k.create()
        #expect(try k.wrap(master) != (try k.wrap(master)))
    }

    @Test func createKeepsTheKey() async throws {
        let k = SoftwareKeyguardKey()
        try k.create()
        let blob = try k.wrap(master)
        try k.create()
        #expect(try await k.unwrap(blob, reason: "") == master)
    }

    @Test func noKey() async throws {
        let k = SoftwareKeyguardKey()
        #expect(throws: KeyguardError.noKey) { try k.wrap(master) }
        await #expect(throws: KeyguardError.noKey) { try await k.unwrap(Data(repeating: 2, count: 200), reason: "") }
        try k.create()
        try k.delete()
        #expect(!k.exists())
    }

    @Test func anotherPhonesOrAChangedBlobDoesNotOpen() async throws {
        let a = SoftwareKeyguardKey(), b = SoftwareKeyguardKey()
        try a.create(); try b.create()
        let blob = try a.wrap(master)
        await #expect(throws: KeyguardError.badBlob) { try await b.unwrap(blob, reason: "") }
        var changed = blob
        changed[changed.count - 1] ^= 1
        await #expect(throws: KeyguardError.badBlob) { try await a.unwrap(changed, reason: "") }
        var android = blob
        android[0] = 0x01   // the Android phone's blob format byte
        await #expect(throws: KeyguardError.badBlob) { try await a.unwrap(android, reason: "") }
        await #expect(throws: KeyguardError.badBlob) { try await a.unwrap(Data([0x02, 1, 2, 3]), reason: "") }
    }

    @Test func theOwnerCancels() async throws {
        let reasons = Box<[String]>([])
        let k = SoftwareKeyguardKey(allow: { r in reasons.update { $0.append(r) }; return false })
        try k.create()
        let blob = try k.wrap(master)
        await #expect(throws: KeyguardError.cancelled) { try await k.unwrap(blob, reason: "Kelo is locked") }
        #expect(reasons.value == ["Kelo is locked"])
    }

    @Test func platformKeyIsNeverMissingInDebug() {
        // macOS test run: no Secure Enclave key for a command-line test, so the software one
        #expect(KeyguardKeys.platform(debug: true) != nil)
        if !SecureEnclaveKeyguardKey.isAvailable { #expect(KeyguardKeys.platform(debug: false) == nil) }
    }
}
