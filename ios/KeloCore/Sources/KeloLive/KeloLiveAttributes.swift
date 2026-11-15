#if canImport(ActivityKit) && os(iOS)
import ActivityKit

/// A running reply's Live Activity (backlog 64b). The words come sealed (LiveSeal); `keyId` names the keychain item that opens them.
public struct KeloLiveAttributes: ActivityAttributes {
    public struct ContentState: Codable, Hashable, Sendable {
        public var v: Int
        public var n: Int
        public var sealed: String
        public init(v: Int = 1, n: Int = 0, sealed: String = "") { self.v = v; self.n = n; self.sealed = sealed }
    }
    public var turnId: String
    public var keyId: String
    public init(turnId: String, keyId: String) { self.turnId = turnId; self.keyId = keyId }
}
#endif
