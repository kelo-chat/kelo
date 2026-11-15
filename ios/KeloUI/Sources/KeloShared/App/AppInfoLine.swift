import Foundation
import KeloCore

/// The app's version as people see it: Kelo's release from version.json (`KeloVersion.version`, the same on every app and in
/// `X-Kelo-App`) and the App Store build number (CFBundleVersion), "0.9.0 (26111011)". CFBundleShortVersionString is the App Store's
/// own marketing number and is not shown (docs/versioning.md).
public enum AppInfoLine {
    public static var current: String {
        line(version: KeloVersion.version, build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String)
    }

    public static func line(version: String, build: String?) -> String {
        let v = version.trimmingCharacters(in: .whitespaces), b = (build ?? "").trimmingCharacters(in: .whitespaces)
        if v.isEmpty { return b }
        return b.isEmpty || b == v ? v : "\(v) (\(b))"
    }
}
