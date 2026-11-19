import Foundation
import StoreKit

/// What the App Store says is the newest version of Kelo (iTunes lookup), so an App Store build hears of a newer app from the store
/// itself (docs/version-notices.md, store builds). The server still decides compatibility (its `X-Kelo-App-Update` drops a version
/// whose signed store row needs a newer server). The answer is cached: the store is asked at most every 6 hours.
public enum AppStoreVersionRules {
    public static let bundleId = "chat.kelo.app"
    public static let checkEveryHours: TimeInterval = 6

    /// The lookup for one storefront country (ISO 3166 alpha-2), as iTunes Search answers it.
    public static func lookupURL(bundleId: String, country: String) -> URL {
        var c = URLComponents(string: "https://itunes.apple.com/lookup")!
        c.queryItems = [URLQueryItem(name: "bundleId", value: bundleId), URLQueryItem(name: "country", value: country.uppercased())]
        return c.url!
    }

    /// The first result's version, or nil (not sold in this storefront, a broken answer, a blank version).
    public static func parse(_ data: Data) -> String? {
        struct Lookup: Decodable { struct Item: Decodable { let version: String? }; let results: [Item]? }
        guard let v = (try? JSONDecoder().decode(Lookup.self, from: data))?.results?.first?.version else { return nil }
        return AppNoticeRules.clean(v)
    }

    /// Strictly newer, part by part as numbers ("1.0.10" is newer than "1.0.9"). An unknown running version is never judged.
    public static func newer(_ store: String, than running: String) -> Bool {
        let a = parts(store), b = parts(running)
        guard !a.isEmpty, !b.isEmpty else { return false }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
    }

    private static func parts(_ s: String) -> [Int] {
        let t = s.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return [] }
        return t.split(separator: ".").map { Int($0.prefix(while: \.isNumber)) ?? 0 }
    }

    /// Due for a new ask: never asked, six hours have passed, or the clock went back.
    public static func due(lastCheck: Date?, now: Date) -> Bool {
        guard let at = lastCheck else { return true }
        return now.timeIntervalSince(at) >= checkEveryHours * 3600 || at > now
    }

    /// The cache text: "<seconds of the check>|<version or empty>".
    public static func encode(checked: Date, version: String?) -> String {
        "\(Int(checked.timeIntervalSince1970))|\(version ?? "")"
    }

    public static func decode(_ text: String?) -> (checked: Date, version: String?)? {
        guard let text, let bar = text.firstIndex(of: "|"), let secs = TimeInterval(text[..<bar]) else { return nil }
        return (Date(timeIntervalSince1970: secs), AppNoticeRules.clean(String(text[text.index(after: bar)...])))
    }
}

/// Asks the App Store for the newest version: a plain GET to the public lookup (no account, no token, nothing about the user), with the
/// device's storefront country. Nil when the answer does not come.
public struct AppStoreLookup: Sendable {
    public let version: @Sendable () async -> String?

    public init(version: @escaping @Sendable () async -> String?) { self.version = version }

    /// The live lookup of this App Store build (the storefront of the device; the locale's region when there is none).
    public static let live = AppStoreLookup {
        let country = (await Storefront.current)?.countryCode ?? Locale.current.region?.identifier ?? "US"
        var request = URLRequest(url: AppStoreVersionRules.lookupURL(bundleId: AppStoreVersionRules.bundleId, country: country))
        request.timeoutInterval = 15
        request.setValue("Kelo-iOS", forHTTPHeaderField: "User-Agent")
        guard let (data, reply) = try? await URLSession.shared.data(for: request), (reply as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return AppStoreVersionRules.parse(data)
    }
}
