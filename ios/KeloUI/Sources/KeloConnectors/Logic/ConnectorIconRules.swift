import Foundation

/// Which of the server's service icons a tile shows, and how it is kept (Android connectors/ConnectorModels.kt ConnectorIconRules).
/// The server sends "icon" (256 px) and "icon_small" (128 px), each a URL with ?v=<content hash>: a new picture is a new URL, so a
/// file kept under its URL's name never goes stale.
public enum ConnectorIconRules {
    /// Tiles up to this size (pt) use the 128 px icon: enough for 40 pt at 3x.
    public static let smallMax: Double = 48

    /// The URL for a tile of `size`; empty when the service has no icon (the initials tile shows).
    public static func pick(icon: String, small: String, size: Double) -> String {
        size <= smallMax ? (small.isEmpty ? icon : small) : (icon.isEmpty ? small : icon)
    }

    /// Only the server's own icon paths are fetched (never another host, never anything else on the server).
    public static func valid(_ url: String) -> Bool {
        url.range(of: #"^/api/connectors/icons/[a-z0-9_]+(-128)?\.png(\?v=[0-9a-f]{1,64})?$"#, options: .regularExpression) != nil
    }

    private static func name(_ url: String) -> String {
        let last = url.split(separator: "/").last.map(String.init) ?? ""
        let base = last.split(separator: "?", maxSplits: 1).first.map(String.init) ?? ""
        return base.hasSuffix(".png") ? String(base.dropLast(4)) : base
    }

    private static func version(_ url: String) -> String {
        guard let r = url.range(of: "?v=") else { return "" }
        return String(url[r.upperBound...])
    }

    /// The kept file's name for `url`: its path and version, safe as a file name.
    public static func fileName(_ url: String) -> String {
        let v = version(url)
        return v.isEmpty ? "\(name(url)).png" : "\(name(url))-\(v).png"
    }

    /// A kept file that is an older version of `url`'s icon, to delete when the new one is kept.
    public static func stale(_ url: String, kept: String) -> Bool {
        let n = name(url)
        guard kept != fileName(url) else { return false }
        if kept == "\(n).png" { return true }
        let escaped = NSRegularExpression.escapedPattern(for: n)
        return kept.range(of: "^\(escaped)-[0-9a-f]{8,}\\.png$", options: .regularExpression) != nil
    }

    /// "#RRGGBB" as (r, g, b) in 0...1; nil when it is not that shape.
    public static func rgb(_ hex: String) -> (Double, Double, Double)? {
        let h = hex.hasPrefix("#") ? String(hex.dropFirst()) : hex
        guard h.count == 6, let v = UInt32(h, radix: 16) else { return nil }
        return (Double((v >> 16) & 0xFF) / 255, Double((v >> 8) & 0xFF) / 255, Double(v & 0xFF) / 255)
    }
}
