import Foundation
import Security

/// One 32-byte key per Live Activity, readable by the app and its widget extension (keychain access group
/// "<team>.chat.kelo.app.live"; both targets' entitlements list it), and readable while the phone is locked after its first unlock
/// (the lock screen shows the activity).
public enum LiveKeys {
    public nonisolated(unsafe) static var accessGroup: String? = nil       // set by the app and the extension from their Info.plist key "KeloLiveKeychainGroup"
    static let service = "chat.kelo.live"

    public static func make() -> (id: String, key: Data)? {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, 32, &bytes) == errSecSuccess else { return nil }
        let id = UUID().uuidString, key = Data(bytes)
        var q = base(id)
        q[kSecValueData as String] = key
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemDelete(base(id) as CFDictionary)
        return SecItemAdd(q as CFDictionary, nil) == errSecSuccess ? (id, key) : nil
    }

    public static func key(_ id: String) -> Data? {
        var q = base(id)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        return SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess ? out as? Data : nil
    }

    public static func remove(_ id: String) { SecItemDelete(base(id) as CFDictionary) }

    /// Every key whose activity is gone (the app calls it at start with the ids of the activities still there).
    public static func removeAll(except keep: Set<String>) {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecReturnAttributes as String: true, kSecMatchLimit as String: kSecMatchLimitAll]
        if let g = accessGroup { q[kSecAttrAccessGroup as String] = g }
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let rows = out as? [[String: Any]] else { return }
        for r in rows { if let id = r[kSecAttrAccount as String] as? String, !keep.contains(id) { remove(id) } }
    }

    static func base(_ id: String) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: id]
        if let g = accessGroup { q[kSecAttrAccessGroup as String] = g }
        return q
    }
}
