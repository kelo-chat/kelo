import CryptoKit
import Foundation

/// What the server sends for the lock screen, sealed with the activity's own key (live_activity.py on the server).
public struct LivePlain: Codable, Equatable, Sendable {
    public var step: String
    public var site: String
    public var state: String          // working | waiting | done | failed | stopped
    public init(step: String, site: String, state: String) { self.step = step; self.site = site; self.state = state }
}

public enum LiveSeal {
    static let aad = Data("kelo-live v1".utf8)

    public static func open(_ sealed: String, key: Data) -> LivePlain? {
        guard key.count == 32, let raw = Data(base64Encoded: sealed),
              let box = try? ChaChaPoly.SealedBox(combined: raw),
              let plain = try? ChaChaPoly.open(box, using: SymmetricKey(data: key), authenticating: aad) else { return nil }
        return try? JSONDecoder().decode(LivePlain.self, from: plain)
    }

    /// The app seals the first state itself (before the server's first push arrives).
    public static func seal(_ p: LivePlain, key: Data) -> String? {
        guard key.count == 32, let json = try? JSONEncoder().encode(p),
              let box = try? ChaChaPoly.seal(json, using: SymmetricKey(data: key), authenticating: aad) else { return nil }
        return box.combined.base64EncodedString()
    }
}

/// The push token as lowercase hex (what the server's APNs call wants).
public enum LiveToken {
    public static func hex(_ token: Data) -> String { token.map { String(format: "%02x", $0) }.joined() }
}
