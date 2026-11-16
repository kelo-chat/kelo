import Foundation
import Security
import KeloCore

/// Where this phone keeps its sign-in (server address + device token). The real one is the Keychain; tests use `MemorySessionStore`.
public protocol SessionStore: AnyObject, Sendable {
    func load() -> Session?
    func save(_ session: Session?)
}

/// The session in the Keychain (generic password, this device only, readable after the first unlock so a background refresh can
/// use it). Never in UserDefaults, never logged.
public final class KeychainSessionStore: SessionStore, @unchecked Sendable {
    private let service: String
    private let account = "session"

    public init(service: String = "chat.kelo.app.session") { self.service = service }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    public func load() -> Session? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return try? JSONDecoder().decode(Session.self, from: data)
    }

    public func save(_ session: Session?) {
        SecItemDelete(query as CFDictionary)
        guard let session, let data = try? JSONEncoder().encode(session) else { return }
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }
}

/// Small settings that are not secret (UserDefaults): the language pick, the last server address, notification choices.
public final class Prefs: @unchecked Sendable {
    public static let shared = Prefs()
    private let d: UserDefaults

    public init(defaults: UserDefaults = .standard) { d = defaults }

    /// nil until a pick in 마이 → 언어 (the phone's language is followed); then a code of `Languages.all` or `Languages.system`.
    public var language: String? {
        get { d.string(forKey: "language") }
        set { d.set(newValue, forKey: "language") }
    }

    public var lastServerUrl: String? {
        get { d.string(forKey: "last_server_url") }
        set { d.set(newValue, forKey: "last_server_url") }
    }

    public func bool(_ key: String, default def: Bool) -> Bool { d.object(forKey: key) == nil ? def : d.bool(forKey: key) }
    public func set(_ key: String, _ value: Bool) { d.set(value, forKey: key) }
    public func string(_ key: String) -> String? { d.string(forKey: key) }
    public func set(_ key: String, _ value: String?) { d.set(value, forKey: key) }
    public func int(_ key: String, default def: Int = 0) -> Int { d.object(forKey: key) == nil ? def : d.integer(forKey: key) }
    public func set(_ key: String, _ value: Int) { d.set(value, forKey: key) }
}
