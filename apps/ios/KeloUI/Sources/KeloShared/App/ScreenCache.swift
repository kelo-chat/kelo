import Foundation
import CryptoKit
import Security

/// Each screen's last good data, so the next open shows it at once and an unreachable server still shows something (Android
/// core/ScreenCache.kt). Kept per server, in memory and on disk under Caches/screens, encrypted with AES-GCM under a key that lives
/// only in this device's Keychain. Losing the key or a file only means the screen loads fresh.
public final class ScreenCache: @unchecked Sendable {
    private let dir: URL?
    private let keyProvider: () -> SymmetricKey?
    private var memory: [String: Data] = [:]
    private var server = ""
    private let lock = NSLock()

    /// `directory` nil keeps everything in memory (tests, previews).
    public init(directory: URL? = ScreenCache.defaultDirectory, key: @escaping () -> SymmetricKey? = ScreenCache.keychainKey) {
        dir = directory
        keyProvider = key
    }

    public static var defaultDirectory: URL? {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?.appendingPathComponent("screens", isDirectory: true)
    }

    /// Switch to a server's slots (a different server never sees another's data).
    public func use(server url: String) {
        lock.lock(); defer { lock.unlock() }
        if url != server { memory = [:]; server = url }
    }

    public func get<T: Decodable>(_ slot: String, as type: T.Type = T.self) -> T? {
        guard let data = read(slot) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    public func put<T: Encodable>(_ slot: String, _ value: T) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        write(slot, data)
    }

    /// Drops everything (signing out). A write still on its way afterwards lands under no server, so it never shows again.
    public func clear() {
        lock.lock(); memory = [:]; server = ""; lock.unlock()
        if let dir { try? FileManager.default.removeItem(at: dir) }
    }

    private func fileName(_ slot: String) -> String {
        let digest = SHA256.hash(data: Data((server + "\n" + slot).utf8))
        return digest.prefix(16).map { String(format: "%02x", $0) }.joined()
    }

    private func read(_ slot: String) -> Data? {
        lock.lock()
        if let d = memory[slot] { lock.unlock(); return d }
        let name = fileName(slot)
        lock.unlock()
        guard let dir, let key = keyProvider(),
              let sealed = try? Data(contentsOf: dir.appendingPathComponent(name)),
              let box = try? AES.GCM.SealedBox(combined: sealed),
              let plain = try? AES.GCM.open(box, using: key) else { return nil }
        lock.lock(); memory[slot] = plain; lock.unlock()
        return plain
    }

    private func write(_ slot: String, _ data: Data) {
        lock.lock()
        memory[slot] = data
        let name = fileName(slot)
        lock.unlock()
        guard let dir, let key = keyProvider(), let sealed = try? AES.GCM.seal(data, using: key).combined else { return }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? sealed.write(to: dir.appendingPathComponent(name), options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    /// The cache key in the Keychain (made on first use; this device only).
    public static func keychainKey() -> SymmetricKey? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: "chat.kelo.app.cache",
                                kSecAttrAccount as String: "v1"]
        var read = q
        read[kSecReturnData as String] = true
        var out: CFTypeRef?
        if SecItemCopyMatching(read as CFDictionary, &out) == errSecSuccess, let d = out as? Data, d.count == 32 {
            return SymmetricKey(data: d)
        }
        let key = SymmetricKey(size: .bits256)
        var add = q
        add[kSecValueData as String] = key.withUnsafeBytes { Data($0) }
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemDelete(q as CFDictionary)
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess ? key : nil
    }
}
