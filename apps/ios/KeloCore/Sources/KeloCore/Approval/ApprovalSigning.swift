import Foundation
import Security
import CryptoKit
#if canImport(LocalAuthentication)
import LocalAuthentication
#endif

// Signed payment approvals, the phone side (docs/approval-signing.md, docs/security/e2e-and-phone-key.md part 6; Android
// approval/ApprovalSigning.kt). The phone signs the Allow of a payment card with its own approval key: one P-256 key in the Secure
// Enclave, used for nothing else, behind Face ID / Touch ID (or the passcode). ES256: ECDSA over SHA-256, the signature DER, base64.

public let approvalAlg = "ES256"

/// What the phone's approval key gave back for one signature.
public enum ApprovalSigned: Equatable, Sendable {
    /// base64 DER ECDSA P-256 over SHA-256 of the message
    case ok(String)
    /// The owner backed out of the check
    case cancelled
    /// The phone has no passcode, so no key can ask for the check
    case noLock
    /// This phone has no usable approval key
    case keyLost
    case failed
}

/// This phone's approval key and the owner's check that comes with each signature.
public protocol ApprovalSigner: Sendable {
    /// This device can sign at all.
    var available: Bool { get }
    /// SPKI DER, base64, of this phone's approval key (made on first use), or nil when there is none.
    func publicKey() -> String?
    /// Whether `publicKey` was already registered with the server at `serverUrl` from this phone.
    func registered(serverUrl: String, publicKey: String) -> Bool
    func markRegistered(serverUrl: String, publicKey: String)
    /// The owner's check, then one signature over `message` with the approval key.
    func sign(_ message: Data, title: String, subtitle: String) async -> ApprovalSigned
}

/// A device without a key: the approval is sent without a signature.
public struct NoApprovalSigner: ApprovalSigner {
    public init() {}
    public var available: Bool { false }
    public func publicKey() -> String? { nil }
    public func registered(serverUrl: String, publicKey: String) -> Bool { false }
    public func markRegistered(serverUrl: String, publicKey: String) {}
    public func sign(_ message: Data, title: String, subtitle: String) async -> ApprovalSigned { .keyLost }
}

/// The bytes the phone signs, and whether the server checks a signature for an approval.
public enum ApprovalSignRules {
    public static let messagePrefix = "kelo-approval v1\n"
    public static let signatureRequired = "signature_required"
    public static let badSignature = "bad_signature"

    /// UTF-8 of "kelo-approval v1\n" + approval_id + "\n" + ("approve" | "deny") + "\n" + issued_at (server approval_sign.approval_message).
    public static func message(approvalId: String, approve: Bool, issuedAt: Int64) -> Data {
        Data("\(messagePrefix)\(approvalId)\n\(approve ? "approve" : "deny")\n\(issuedAt)".utf8)
    }

    /// The server's approval_sign.is_payment on the approval_request event: `bool(event.get("payment"))`, or the purchase card (name
    /// confirm_purchase), or a browser click judged to be the paying one (commit "pay"). Python truthiness: an empty object, list or
    /// string, zero, false and null are not.
    public static func isPayment(_ event: JSON) -> Bool {
        truthy(event["payment"]) || event["name"]?.stringValue == "confirm_purchase" || event["commit"]?.stringValue == "pay"
    }

    static func truthy(_ e: JSON?) -> Bool {
        switch e {
        case .none, .some(.null): return false
        case .some(.bool(let b)): return b
        case .some(.number(let n)): return n != 0
        case .some(.string(let s)): return !s.isEmpty
        case .some(.array(let a)): return !a.isEmpty
        case .some(.object(let o)): return !o.isEmpty
        }
    }

    /// Whether `sig` (base64 DER) is a valid signature of `message` by `publicKey` (base64 SPKI DER). The vectors test uses it.
    public static func verify(publicKey: String, message: Data, sig: String) -> Bool {
        guard let pk = Data(base64Encoded: publicKey), let s = Data(base64Encoded: sig),
              let key = try? P256.Signing.PublicKey(derRepresentation: pk),
              let signature = try? P256.Signing.ECDSASignature(derRepresentation: s) else { return false }
        return key.isValidSignature(signature, for: message)
    }
}

/// What the Allow of a payment came to on this phone.
public enum ApprovalSignOutcome: Equatable, Sendable {
    /// `sig` is nil when this phone cannot sign (the server then decides, as before)
    case ready(String?)
    /// The owner backed out: the card stays open, nothing is sent
    case cancelled
    /// The card stays open and says why
    case failed(Reason)

    public enum Reason: Equatable, Sendable { case noLock, keyLost, failed }
}

public enum ApprovalSigning {
    /// The owner's check and the signature of an Allow of a payment. `title` / `subtitle` are the system prompt's words.
    public static func signPaymentAllow(_ signer: ApprovalSigner, approvalId: String, issuedAt: Int64,
                                        title: String, subtitle: String) async -> ApprovalSignOutcome {
        guard signer.available else { return .ready(nil) }
        let done = await signer.sign(ApprovalSignRules.message(approvalId: approvalId, approve: true, issuedAt: issuedAt), title: title, subtitle: subtitle)
        switch done {
        case .ok(let sig): return .ready(sig)
        case .cancelled: return .cancelled
        case .noLock: return .failed(.noLock)
        case .keyLost: return .failed(.keyLost)
        case .failed: return .failed(.failed)
        }
    }

    /// 403 signature_required / bad_signature on an Allow.
    public static func isSignature(_ e: ApiError) -> Bool { e.code == ApprovalSignRules.signatureRequired || e.code == ApprovalSignRules.badSignature }

    /// The server's own message of a refused signature (nil: none, the caller says it in its own words).
    public static func serverMessage(_ e: ApiError) -> String? {
        let m = e.detailJson?["message"]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (m?.isEmpty == false) ? m : nil
    }
}

/// Registers this phone's approval key with the server once per key (POST /api/devices/approval-key).
public enum ApprovalKeyRegistrar {
    public static func refresh(_ signer: ApprovalSigner, api: KeloApi) async {
        guard signer.available, let pub = signer.publicKey(), !signer.registered(serverUrl: api.baseUrl, publicKey: pub) else { return }
        do {
            let body: JSON = .object(["alg": .string(approvalAlg), "public_key": .string(pub)])
            _ = try await api.request("POST", "/api/devices/approval-key", body: body)
            signer.markRegistered(serverUrl: api.baseUrl, publicKey: pub)
        } catch {
            // 404: an older server, asked again on the next start. 403 signature_required: the server keeps another key for this
            // phone (its keys were lost); the phone must be removed and paired again. Nothing else to do here.
        }
    }
}

/// Which server this phone's approval key was registered with: "approval.reg.<first 12 hex of sha256(url)>" -> the public key.
struct ApprovalRegistration: Sendable {
    let defaults: UserDefaultsBox

    static func key(_ serverUrl: String) -> String {
        var u = serverUrl.lowercased()
        while u.hasSuffix("/") { u.removeLast() }
        return "approval.reg." + SHA256.hash(data: Data(u.utf8)).prefix(6).map { String(format: "%02x", $0) }.joined()
    }

    func registered(_ serverUrl: String, _ publicKey: String) -> Bool { defaults.value.string(forKey: Self.key(serverUrl)) == publicKey }
    func mark(_ serverUrl: String, _ publicKey: String) { defaults.value.set(publicKey, forKey: Self.key(serverUrl)) }
}

/// UserDefaults is thread-safe but not Sendable.
struct UserDefaultsBox: @unchecked Sendable { let value: UserDefaults }

/// A P-256 key without the Secure Enclave and without a prompt (the simulator, tests). `allow` decides each signature, so a test can
/// play "the owner cancelled".
public final class SoftwareApprovalSigner: ApprovalSigner, @unchecked Sendable {
    private let lock = NSLock()
    private var key: P256.Signing.PrivateKey
    private let allow: @Sendable (String) async -> Bool
    private let reg: ApprovalRegistration

    public init(key: P256.Signing.PrivateKey = P256.Signing.PrivateKey(), defaults: UserDefaults = .standard,
                allow: @escaping @Sendable (String) async -> Bool = { _ in true }) {
        self.key = key
        self.allow = allow
        self.reg = ApprovalRegistration(defaults: UserDefaultsBox(value: defaults))
    }

    public var available: Bool { true }
    public func publicKey() -> String? { lock.withLock { key.publicKey.derRepresentation.base64EncodedString() } }
    public func registered(serverUrl: String, publicKey: String) -> Bool { reg.registered(serverUrl, publicKey) }
    public func markRegistered(serverUrl: String, publicKey: String) { reg.mark(serverUrl, publicKey) }

    public func sign(_ message: Data, title: String, subtitle: String) async -> ApprovalSigned {
        guard await allow(title) else { return .cancelled }
        let k = lock.withLock { key }
        guard let sig = try? k.signature(for: message) else { return .failed }
        return .ok(sig.derRepresentation.base64EncodedString())
    }
}

/// The real one: a Secure Enclave P-256 signing key (this device only, needs a passcode, Face ID / Touch ID or the passcode for each
/// signature). Used only for approvals, never for Keyguard or the card keys.
public final class SecureEnclaveApprovalSigner: ApprovalSigner, @unchecked Sendable {
    public static let defaultAccess: SecAccessControlCreateFlags = [.privateKeyUsage, .biometryCurrentSet, .or, .devicePasscode]
    public static var isAvailable: Bool { SecureEnclave.isAvailable }

    private let slot: KeyguardSlot
    private let access: SecAccessControlCreateFlags
    private let reg: ApprovalRegistration

    public init(account: String = "approval-v1", access: SecAccessControlCreateFlags = SecureEnclaveApprovalSigner.defaultAccess,
                defaults: UserDefaults = .standard) {
        slot = KeyguardSlot(service: "chat.kelo.app.approval", account: account)
        self.access = access
        self.reg = ApprovalRegistration(defaults: UserDefaultsBox(value: defaults))
    }

    public var available: Bool { Self.isAvailable }
    public func registered(serverUrl: String, publicKey: String) -> Bool { reg.registered(serverUrl, publicKey) }
    public func markRegistered(serverUrl: String, publicKey: String) { reg.mark(serverUrl, publicKey) }

    /// Makes the key on first use (no prompt: the public key needs none).
    private func loadOrCreate() throws -> SecureEnclave.P256.Signing.PrivateKey {
        if let d = slot.load() { return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: d) }
        var error: Unmanaged<CFError>?
        guard let acl = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, access, &error) else {
            throw KeyguardError.failed(error.map { CFErrorGetCode($0.takeRetainedValue()) } ?? Int(errSecParam))
        }
        let key: SecureEnclave.P256.Signing.PrivateKey
        do { key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: acl) } catch { throw KeyguardError.noPasscode }
        try slot.save(key.dataRepresentation)
        return key
    }

    public func publicKey() -> String? {
        guard Self.isAvailable, let k = try? loadOrCreate() else { return nil }
        return k.publicKey.derRepresentation.base64EncodedString()
    }

    public func sign(_ message: Data, title: String, subtitle: String) async -> ApprovalSigned {
        guard Self.isAvailable else { return .keyLost }
        let d: Data
        do {
            _ = try loadOrCreate()
            guard let loaded = slot.load() else { return .keyLost }
            d = loaded
        } catch KeyguardError.noPasscode { return .noLock } catch { return .failed }
        #if canImport(LocalAuthentication)
        let ctx = LAContext()
        do {
            guard try await ctx.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: subtitle.isEmpty ? title : "\(title)\n\(subtitle)") else { return .cancelled }
        } catch let e as LAError {
            switch KeyguardKeys.map(e) {
            case .noPasscode: return .noLock
            case .cancelled: return .cancelled
            default: return .failed
            }
        } catch { return .failed }
        guard let key = try? SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: d, authenticationContext: ctx) else { return .keyLost }
        guard let sig = try? key.signature(for: message) else { return .keyLost }
        return .ok(sig.derRepresentation.base64EncodedString())
        #else
        return .keyLost
        #endif
    }
}

public enum ApprovalSigners {
    /// The signer this phone uses: the Secure Enclave one, or the software one on the simulator (debug builds only). A phone without
    /// either sends the approval unsigned (the server decides).
    public static func platform(debug: Bool = ServerAddress.isDebugBuild) -> ApprovalSigner {
        #if targetEnvironment(simulator)
        if debug { return SoftwareApprovalSigner() }
        #endif
        if SecureEnclaveApprovalSigner.isAvailable { return SecureEnclaveApprovalSigner() }
        return debug ? SoftwareApprovalSigner() : NoApprovalSigner()
    }
}
