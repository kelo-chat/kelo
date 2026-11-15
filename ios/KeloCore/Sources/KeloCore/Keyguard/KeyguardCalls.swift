import Foundation

// Keyguard (docs/keyguard.md; spec docs/security/e2e-and-phone-key.md 2, 5.9, 5.14, 5.15; Android keyguard/KeyguardModels.kt): the key
// of the owner's passwords, keys and cards is held by the owner's phones instead of a file on the server. Plain models, rules and
// calls here (tested); the phone's key is `KeyguardKey` (Keyguard.swift), the screens are in KeloMy.

/// GET /api/keyguard (works while locked). `mode` "server" (off) or "phone" (on).
public struct KeyguardView: Equatable, Sendable, Decodable {
    public static let modeServer = "server", modePhone = "phone"
    public var mode: String
    public var locked: Bool
    /// ASSISTANT_SECRET_KEY is still set in the server's .env: turning it on is refused until `assistant keyguard enable` ran
    public var envKey: Bool
    public var recovery: Bool
    public var phones: [KeyguardPhone]

    public init(mode: String = KeyguardView.modeServer, locked: Bool = false, envKey: Bool = false, recovery: Bool = false, phones: [KeyguardPhone] = []) {
        self.mode = mode; self.locked = locked; self.envKey = envKey; self.recovery = recovery; self.phones = phones
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        mode = j.str("mode", KeyguardView.modeServer)
        locked = j.bool("locked")
        envKey = j.bool("env_key")
        recovery = j.bool("recovery")
        phones = j.list("phones", of: KeyguardPhone.self)
    }

    public var on: Bool { mode == KeyguardView.modePhone }

    /// This phone holds a copy (its device id from GET /api/devices "current").
    public func holds(_ deviceId: Int64?) -> Bool { deviceId.map { id in phones.contains { $0.deviceId == id } } ?? false }
}

public struct KeyguardPhone: Equatable, Sendable, Decodable {
    public var deviceId: Int64, name: String, alg: Int, created: Double
    public init(deviceId: Int64, name: String = "", alg: Int = 0, created: Double = 0) {
        self.deviceId = deviceId; self.name = name; self.alg = alg; self.created = created
    }
    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        deviceId = j.int64("device_id"); name = j.str("name"); alg = j.int("alg"); created = j.double("created")
    }
}

/// POST /api/keyguard/enroll: the key to wrap (`key`, base64url text) and, when this turns it on, the recovery code (shown ONCE).
/// Neither is ever printed.
public struct EnrollAnswer: Sendable, Decodable, CustomStringConvertible, CustomDebugStringConvertible {
    public var enrollmentId: String
    let key: String
    public var enabling: Bool
    public var recoveryCode: String?

    public init(enrollmentId: String, key: String, enabling: Bool, recoveryCode: String?) {
        self.enrollmentId = enrollmentId; self.key = key; self.enabling = enabling; self.recoveryCode = recoveryCode
    }

    public init(from decoder: Decoder) throws {
        let j = try Lenient(decoder)
        enrollmentId = j.str("enrollment_id"); key = j.str("key"); enabling = j.bool("enabling"); recoveryCode = j.optStr("recovery_code")
    }

    public var description: String { "EnrollAnswer(\(enrollmentId), enabling: \(enabling))" }   // never the key or the code
    public var debugDescription: String { description }
}

/// The pure rules of the Keyguard screens.
public enum KeyguardRules {
    public static let codeLocked = "locked"
    public static let codeEnvKey = "env_key"
    public static let codeWrongKey = "wrong_key"
    public static let codeNotEnrolled = "not_enrolled"
    /// The server's name for "this is the last phone holding the key" (DELETE /api/devices: last_keyguard_phone)
    public static let lastPhoneCodes: Set<String> = ["last_phone", "last_keyguard_phone"]
    /// The iPhone's blob kind (`KeyguardBlob.iPhoneFormat`), as POST /api/keyguard/enroll/finish "alg".
    public static let alg = Int(KeyguardBlob.iPhoneFormat)

    /// A recovery code as people type it: letters and digits only (ASCII), upper case (the server does the same).
    public static func normalizeCode(_ text: String) -> String {
        String(text.uppercased().unicodeScalars.filter { $0.isASCII && CharacterSet.alphanumerics.contains($0) }.map(Character.init))
    }

    /// The recovery code shown in groups of four ("ABCD-EFGH-…"), easier to write down and read back.
    public static func groupCode(_ code: String) -> String {
        let n = Array(normalizeCode(code))
        return stride(from: 0, to: n.count, by: 4).map { String(n[$0..<min($0 + 4, n.count)]) }.joined(separator: "-")
    }

    /// The recovery code as shown once: three groups of four on each line, two lines ("ABCD-EFGH-JKLM\nNPQR-STUV-WXYZ"), so it never
    /// wraps in the middle of a group at any width (Android KeyguardRules.codeLines).
    public static func codeLines(_ code: String) -> String {
        let groups = groupCode(code).split(separator: "-").map(String.init)
        return stride(from: 0, to: groups.count, by: 3).map { groups[$0..<min($0 + 3, groups.count)].joined(separator: "-") }.joined(separator: "\n")
    }

    /// Typed enough to send (the server's codes have 24 characters).
    public static func codeReady(_ text: String) -> Bool { normalizeCode(text).count >= 20 }

    /// What the locked banner offers on this phone: unlock with its own copy, else only the recovery code (or another phone).
    public enum LockedAction: Equatable, Sendable { case unlock, recoverOnly }
    public static func lockedAction(holdsCopy: Bool) -> LockedAction { holdsCopy ? .unlock : .recoverOnly }

    /// A failed call means "Kelo is locked" (423 locked).
    public static func isLocked(_ e: ApiError) -> Bool { e.status == 423 || e.code == codeLocked }
}

/// The Keyguard API (owner only; key material only inside KE2E from an owner phone).
public struct KeyguardCalls: Sendable {
    let api: KeloApi
    public init(api: KeloApi) { self.api = api }

    public func view() async throws -> KeyguardView { try await api.get("/api/keyguard") }
    public func enroll() async throws -> EnrollAnswer { try await api.post("/api/keyguard/enroll") }

    public func finish(enrollmentId: String, blob: Data) async throws -> KeyguardView {
        try await api.post("/api/keyguard/enroll/finish", body: ["enrollment_id": .string(enrollmentId), "alg": .number(Double(KeyguardRules.alg)),
                                                                  "blob": .string(blob.base64EncodedString())])
    }

    /// This phone's wrapped copy (nil: this phone holds none, 404).
    public func blob() async throws -> Data? {
        do {
            let j = try await api.request("GET", "/api/keyguard/blob")
            return j["blob"]?.stringValue.flatMap { Data(base64Encoded: $0.trimmingCharacters(in: .whitespacesAndNewlines)) }
        } catch let e as ApiError where e.status == 404 { return nil }
    }

    /// The unwrapped key as bytes (never a String the app keeps), sent once; the body is wiped here, the key by the caller.
    public func unlock(key: Data) async throws {
        var body = Data("{\"key\":\"".utf8) + key + Data("\"}".utf8)
        defer { body.resetBytes(in: 0..<body.count) }
        _ = try await api.requestBytes("POST", "/api/keyguard/unlock", body: body)
    }

    public func recover(code: String) async throws {
        _ = try await api.request("POST", "/api/keyguard/recover", body: ["code": .string(KeyguardRules.normalizeCode(code))])
    }

    public func removePhone(deviceId: Int64) async throws -> KeyguardView { try await api.delete("/api/keyguard/phones/\(deviceId)") }
    public func disable() async throws -> KeyguardView { try await api.post("/api/keyguard/disable") }
}

/// The two flows that use this phone's key (pure over `KeyguardKey` and the calls, so they run in tests with the software key).
public enum KeyguardFlow {
    public enum Problem: Error, Equatable, Sendable {
        /// The owner backed out of the check: nothing to say
        case cancelled
        /// The phone has no passcode
        case noLock
        /// This phone has no usable key, or its blob does not open
        case keyLost
        /// This phone holds no copy: unlock on a phone that does, or the recovery code
        case notHere
        case server(ApiError)
    }

    static func problem(_ e: Error) -> Problem {
        switch e {
        case KeyguardError.cancelled: return .cancelled
        case KeyguardError.noPasscode: return .noLock
        case KeyguardError.noKey, KeyguardError.badBlob: return .keyLost
        case let a as ApiError: return a.code == KeyguardRules.codeWrongKey ? .keyLost : .server(a)
        case KeyguardError.failed(let code): return .server(ApiError(status: -1, detail: "This phone's key could not be used (\(code)).", code: "keyguard_key"))
        default: return .server(ApiError(status: 0, detail: String(describing: type(of: e))))
        }
    }

    /// Enrols this phone: a key from the server, wrapped here with a NEW phone key (no prompt: wrapping needs only the public key),
    /// the blob back. Returns the view and the recovery code (enabling only, shown once).
    public static func enroll(calls: KeyguardCalls, key: KeyguardKey) async throws -> (KeyguardView, String?) {
        do {
            let answer = try await calls.enroll()
            try key.renew()
            var plain = Data(answer.key.utf8)
            defer { plain.resetBytes(in: 0..<plain.count) }
            let blob = try key.wrap(plain)
            let view = try await calls.finish(enrollmentId: answer.enrollmentId, blob: blob)
            return (view, answer.recoveryCode)
        } catch { throw problem(error) }
    }

    /// Unlocks Kelo with this phone's copy: its blob (KE2E), the owner's check releases the key, the blob opens here and the key goes
    /// to POST /api/keyguard/unlock once; its bytes are wiped.
    public static func unlock(calls: KeyguardCalls, key: KeyguardKey, reason: String) async throws {
        do {
            guard let blob = try await calls.blob() else { throw Problem.notHere }
            guard key.exists() else { throw Problem.keyLost }
            var plain = try await key.unwrap(blob, reason: reason)
            defer { plain.resetBytes(in: 0..<plain.count) }
            try await calls.unlock(key: plain)
        } catch let p as Problem { throw p } catch { throw problem(error) }
    }
}
