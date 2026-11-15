import Foundation
import Security
import CryptoKit
#if canImport(LocalAuthentication)
import LocalAuthentication
#endif

// Keyguard, the phone side (docs/security/e2e-and-phone-key.md §2.4 and §5.9, §5.14): the server's master key, wrapped on this
// phone with a P-256 key that lives in the Secure Enclave and is usable only after Face ID / Touch ID **or** the passcode
// (`[.privateKeyUsage, .userPresence]`: the key survives re-enrolling a face or finger). The server keeps the blob and never opens it.
// Wrapping needs only the public key (no prompt); unwrapping uses the private key (one prompt).
//
// The wrap must bind the AAD "kelo-keyguard v1" (§5.14). `SecKeyCreateEncryptedData(.ecies…AESGCM)` has no public AAD parameter,
// so the wrap is ECIES in its standard form, RFC 9180 HPKE base mode through CryptoKit: DHKEM(P-256, HKDF-SHA256) / HKDF-SHA256 /
// AES-256-GCM, empty info, aad = "kelo-keyguard v1". The Secure Enclave key is a CryptoKit `SecureEnclave.P256.KeyAgreement`
// key (HPKE accepts it directly).
//
// Blob = 0x02 ‖ enc (65, X9.63 uncompressed ephemeral public key) ‖ AES-GCM ciphertext ‖ tag (16).
//
// The simulator and the macOS test run have no Secure Enclave: `SoftwareKeyguardKey` runs the very same HPKE code with an ordinary
// P-256 key, so everything above the key can be tested.

public enum KeyguardBlob {
    /// Android: AES-256-GCM in AndroidKeyStore.
    public static let androidFormat: UInt8 = 0x01
    /// iPhone: Secure Enclave P-256 + HPKE (ECIES).
    public static let iPhoneFormat: UInt8 = 0x02
    /// Format byte (1) + ephemeral public key (65) + tag (16) on top of the secret.
    public static let overhead = 1 + 65 + 16
    /// The AAD both phones bind (spec §5.14).
    public static let aad = Data("kelo-keyguard v1".utf8)
}

public enum KeyguardError: Error, Equatable, Sendable {
    /// No key on this phone (never enrolled, or deleted).
    case noKey
    /// The owner cancelled the Face ID / passcode prompt (or it failed).
    case cancelled
    /// The phone has no passcode, so a protected key cannot exist.
    case noPasscode
    /// Not a blob this phone made (wrong format byte, changed bytes, another phone's key), or the key can no longer open it.
    case badBlob
    /// Anything else (an OSStatus or LAError code, never key material).
    case failed(Int)
}

/// The phone's key for Keyguard. One per app install; screens never see the private key.
public protocol KeyguardKey: Sendable {
    /// Whether this phone already holds the key (no prompt).
    func exists() -> Bool
    /// Makes the key if there is none (no prompt).
    func create() throws
    /// Wraps `secret` (the server's master key from the KE2E answer) into a blob for the server. No prompt.
    func wrap(_ secret: Data) throws -> Data
    /// Opens a blob this phone made; shows the system prompt with `reason`. The caller wipes the result after use.
    func unwrap(_ blob: Data, reason: String) async throws -> Data
    /// Forgets the key (turning Keyguard off on this phone, or removing the phone).
    func delete() throws
}

extension KeyguardKey {
    /// A NEW key (enrolling again replaces this phone's copy on the server too, as Android's `wrapCipher`).
    public func renew() throws {
        try delete()
        try create()
    }
}

/// The wrap both implementations share.
public enum KeyguardSeal {
    public static let ciphersuite = HPKE.Ciphersuite.P256_SHA256_AES_GCM_256

    public static func wrap(_ secret: Data, to publicKey: P256.KeyAgreement.PublicKey) throws -> Data {
        var sender = try HPKE.Sender(recipientKey: publicKey, ciphersuite: ciphersuite, info: Data())
        let ct = try sender.seal(secret, authenticating: KeyguardBlob.aad)
        return Data([KeyguardBlob.iPhoneFormat]) + sender.encapsulatedKey + ct
    }

    /// Blocking: with a Secure Enclave key the ECDH runs in the Enclave. Call off the main thread.
    public static func unwrap<K: HPKEDiffieHellmanPrivateKey>(_ blob: Data, with key: K) throws -> Data {
        let b = Data(blob)
        guard b.count > KeyguardBlob.overhead, b[0] == KeyguardBlob.iPhoneFormat else { throw KeyguardError.badBlob }
        do {
            var r = try HPKE.Recipient(privateKey: key, ciphersuite: ciphersuite, info: Data(), encapsulatedKey: b.subdata(in: 1..<66))
            return try r.open(b.subdata(in: 66..<b.count), authenticating: KeyguardBlob.aad)
        } catch {
            if let e = error as? KeyguardError { throw e }
            #if canImport(LocalAuthentication)
            if let la = error as? LAError { throw KeyguardKeys.map(la) }
            #endif
            throw KeyguardError.badBlob
        }
    }
}

/// A small generic-password Keychain slot (this device only) for a key's bytes: the software key's raw private key on the
/// simulator, the Secure Enclave key's `dataRepresentation` (useless off this phone's Enclave) on a real phone.
struct KeyguardSlot: Sendable {
    let service: String
    let account: String

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    func load() -> Data? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess else { return nil }
        return out as? Data
    }

    func save(_ data: Data) throws {
        SecItemDelete(query as CFDictionary)
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeyguardError.failed(Int(status)) }
    }

    func delete() { SecItemDelete(query as CFDictionary) }
}

/// A P-256 key without the Secure Enclave and without a prompt (the simulator, tests, previews). In memory by default; with
/// `keychainAccount` it is kept in the simulator's Keychain so it survives a restart. `allow` decides each unwrap, so a test can
/// play "the owner cancelled". Never used on a phone that has a Secure Enclave.
public final class SoftwareKeyguardKey: KeyguardKey, @unchecked Sendable {
    private let lock = NSLock()
    private var key: P256.KeyAgreement.PrivateKey?
    private let slot: KeyguardSlot?
    private let allow: @Sendable (String) async -> Bool

    public init(keychainAccount: String? = nil, allow: @escaping @Sendable (String) async -> Bool = { _ in true }) {
        slot = keychainAccount.map { KeyguardSlot(service: "chat.kelo.app.keyguard.sim", account: $0) }
        self.allow = allow
    }

    private func current() -> P256.KeyAgreement.PrivateKey? {
        lock.withLock {
            if key == nil, let raw = slot?.load() { key = try? P256.KeyAgreement.PrivateKey(rawRepresentation: raw) }
            return key
        }
    }

    public func exists() -> Bool { current() != nil }

    public func create() throws {
        if exists() { return }
        let k = P256.KeyAgreement.PrivateKey()
        // kept in the Keychain when it can be (an unsigned simulator build has no Keychain entitlement: then in memory only)
        try? slot?.save(k.rawRepresentation)
        lock.withLock { if key == nil { key = k } }
    }

    public func wrap(_ secret: Data) throws -> Data {
        guard let k = current() else { throw KeyguardError.noKey }
        return try KeyguardSeal.wrap(secret, to: k.publicKey)
    }

    public func unwrap(_ blob: Data, reason: String) async throws -> Data {
        guard let k = current() else { throw KeyguardError.noKey }
        guard await allow(reason) else { throw KeyguardError.cancelled }
        return try KeyguardSeal.unwrap(blob, with: k)
    }

    public func delete() throws {
        lock.withLock { key = nil }
        slot?.delete()
    }
}

/// The real one: a Secure Enclave P-256 key-agreement key made with `access` (this device only, needs a passcode); its
/// `dataRepresentation` sits in the Keychain. Spec §5.9: `[.privateKeyUsage, .userPresence]`.
public final class SecureEnclaveKeyguardKey: KeyguardKey, @unchecked Sendable {
    public static let defaultAccess: SecAccessControlCreateFlags = [.privateKeyUsage, .userPresence]

    /// False on the simulator and on Macs without one.
    public static var isAvailable: Bool { SecureEnclave.isAvailable }

    private let slot: KeyguardSlot
    private let access: SecAccessControlCreateFlags

    /// One key per server address, so pairing another server never replaces this one (Android KeyguardKey alias).
    public static func account(serverUrl: String) -> String {
        var u = serverUrl.lowercased()
        while u.hasSuffix("/") { u.removeLast() }
        return "keyguard-v1." + SHA256.hash(data: Data(u.utf8)).prefix(12).map { String(format: "%02x", $0) }.joined()
    }

    public init(account: String = "keyguard-v1", access: SecAccessControlCreateFlags = SecureEnclaveKeyguardKey.defaultAccess) {
        slot = KeyguardSlot(service: "chat.kelo.app.keyguard", account: account)
        self.access = access
    }

    public func exists() -> Bool { slot.load() != nil }

    public func create() throws {
        guard Self.isAvailable else { throw KeyguardError.failed(Int(errSecUnimplemented)) }
        if exists() { return }
        var error: Unmanaged<CFError>?
        guard let acl = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, access, &error) else {
            throw KeyguardError.failed(error.map { CFErrorGetCode($0.takeRetainedValue()) } ?? Int(errSecParam))
        }
        let key: SecureEnclave.P256.KeyAgreement.PrivateKey
        do { key = try SecureEnclave.P256.KeyAgreement.PrivateKey(accessControl: acl) }
        catch { throw KeyguardError.noPasscode }   // WhenPasscodeSet without a passcode is the one way this fails on a phone
        try slot.save(key.dataRepresentation)
    }

    public func wrap(_ secret: Data) throws -> Data {
        guard let d = slot.load() else { throw KeyguardError.noKey }
        // the public key needs no authentication
        let key: SecureEnclave.P256.KeyAgreement.PrivateKey
        do { key = try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: d) } catch { throw KeyguardError.badBlob }
        return try KeyguardSeal.wrap(secret, to: key.publicKey)
    }

    public func unwrap(_ blob: Data, reason: String) async throws -> Data {
        guard let d = slot.load() else { throw KeyguardError.noKey }
        #if canImport(LocalAuthentication)
        // the owner's check first (one prompt), then the Enclave uses the checked context: the wallet's pattern
        let ctx = LAContext()
        do {
            guard try await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) else { throw KeyguardError.cancelled }
        } catch let e as LAError {
            throw KeyguardKeys.map(e)
        }
        let key: SecureEnclave.P256.KeyAgreement.PrivateKey
        do { key = try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: d, authenticationContext: ctx) }
        catch { throw KeyguardError.badBlob }
        #else
        let key: SecureEnclave.P256.KeyAgreement.PrivateKey
        do { key = try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: d) } catch { throw KeyguardError.badBlob }
        #endif
        let box = KeyBox(key)
        return try await withCheckedThrowingContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                do { cont.resume(returning: try KeyguardSeal.unwrap(blob, with: box.key)) } catch { cont.resume(throwing: error) }
            }
        }
    }

    public func delete() throws { slot.delete() }

    private final class KeyBox: @unchecked Sendable {
        let key: SecureEnclave.P256.KeyAgreement.PrivateKey
        init(_ k: SecureEnclave.P256.KeyAgreement.PrivateKey) { key = k }
    }
}

public enum KeyguardKeys {
    /// The key this phone uses: the Secure Enclave one, or the software one on the simulator (debug builds only). nil = this phone
    /// cannot hold the key (the app then shows the locked state and asks for another owner phone, as the desktop does).
    public static func platform(serverUrl: String = "", debug: Bool = ServerAddress.isDebugBuild) -> KeyguardKey? {
        let account = SecureEnclaveKeyguardKey.account(serverUrl: serverUrl)
        #if targetEnvironment(simulator)
        // the simulator has no passcode to bind a Secure Enclave key to: the software key, as the tests use
        if debug { return SoftwareKeyguardKey(keychainAccount: account) }
        #endif
        if SecureEnclaveKeyguardKey.isAvailable { return SecureEnclaveKeyguardKey(account: account) }
        return debug ? SoftwareKeyguardKey(keychainAccount: account) : nil
    }

    #if canImport(LocalAuthentication)
    static func map(_ e: LAError) -> KeyguardError {
        switch e.code {
        case .passcodeNotSet: return .noPasscode
        case .userCancel, .appCancel, .systemCancel, .userFallback, .authenticationFailed, .biometryLockout, .notInteractive: return .cancelled
        default: return .failed(e.code.rawValue)
        }
    }
    #endif
}
