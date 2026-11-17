import Foundation

/// The min-version contract in both directions (docs/versioning.md, MIN VERSION CONTRACT).
public enum Versioning {
    /// "0.9.0" -> (0, 9, 0); "0.9.0+12" and "0.9.0-beta.1" read their release part. nil when it is not MAJOR.MINOR.PATCH.
    public static func parse(_ text: String?) -> (Int, Int, Int)? {
        guard var t = text?.trimmingCharacters(in: .whitespaces), !t.isEmpty else { return nil }
        if let plus = t.firstIndex(of: "+") { t = String(t[..<plus]) }
        if let dash = t.firstIndex(of: "-") { t = String(t[..<dash]) }
        let parts = t.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3, let a = Int(parts[0]), let b = Int(parts[1]), let c = Int(parts[2]), a >= 0, b >= 0, c >= 0 else { return nil }
        return (a, b, c)
    }

    /// `a` is older than `b` (both MAJOR.MINOR.PATCH); false when either does not parse.
    public static func older(_ a: String?, than b: String?) -> Bool {
        guard let x = parse(a), let y = parse(b) else { return false }
        return x < y
    }

    /// The `X-Kelo-Server` header of an answer ("0.9.0+1") says the server is older than this app's compiled-in `min_server`.
    /// A missing header (a server older than 0.9.0) or one that does not parse is not judged here: such a server never offered
    /// KE2E, so it is used in plaintext and the app shows that state (spec 5.5).
    public static func serverTooOld(header: String?, minServer: String = KeloVersion.minServer) -> Bool {
        older(header, than: minServer)
    }

    public static let serverHeader = "X-Kelo-Server"
    public static let appHeader = "X-Kelo-App"
    /// Where this build is updated from, sent with every call (docs/version-notices.md): "appstore", "play", "download" or "msstore".
    public static let channelHeader = "X-Kelo-App-Channel"
    /// On /api/about and /api/profile: the newest released app that works with this server (when newer than this one).
    public static let updateHeader = "X-Kelo-App-Update"
    /// 426 `{"code": "app_too_old"}`: the server needs a newer app.
    public static let appTooOldCode = "app_too_old"

    /// `ios/<version>+<CFBundleVersion>` ("ios/0.9.0+26111011"); without a build number (tests, previews) just `ios/<version>`.
    public static func appHeaderValue(version: String = KeloVersion.version, build: String?) -> String {
        let b = (build ?? "").trimmingCharacters(in: .whitespaces)
        return b.isEmpty || !b.allSatisfy(\.isNumber) ? "ios/\(version)" : "ios/\(version)+\(b)"
    }

    /// This running app's header value (CFBundleVersion of the main bundle).
    public static let currentAppHeader: String =
        appHeaderValue(build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String)

    /// The channel of this running app: "appstore" for an App Store install (its receipt is the production one); "" for TestFlight,
    /// a simulator or a debug build, which the App Store version list says nothing about (no banner there).
    public static let currentChannel: String = channel(receiptName: Bundle.main.appStoreReceiptURL?.lastPathComponent)

    public static func channel(receiptName: String?) -> String { receiptName == "receipt" ? "appstore" : "" }
}
