import Foundation

/// The `Accept-Language` header every request to the Kelo server carries, so the server writes its own words (connector names,
/// schedule lines, notifications) in the app's language and features the services of the phone's region (Android
/// core/AcceptLanguage.kt, same rules).
///
/// The language picked in the app comes first (with the phone's region when the phone has that language, e.g. "ko-KR"), then the
/// phone's own languages in order (`Locale.preferredLanguages` on iOS); each tagged region is followed by its bare language:
/// "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7".
public enum AcceptLanguage {
    public static let header = "Accept-Language"
    static let maxEntries = 6

    public static func build(appLanguage: String?, deviceTags: [String]) -> String {
        let device = deviceTags.compactMap(clean)
        let app = appLanguage.flatMap(clean)
        var ordered: [String] = []
        if let app {
            // the app's language with the phone's region for it, when the phone has one ("ko" + "ko-KR" -> "ko-KR")
            let regional = app.contains("-") ? app : device.first { base($0) == app && $0.contains("-") }
            ordered.append(regional ?? app)
        }
        ordered += device
        var tags: [String] = []
        func add(_ t: String) { if !tags.contains(where: { $0.caseInsensitiveCompare(t) == .orderedSame }) { tags.append(t) } }
        for t in ordered {
            add(t)
            if t.contains("-") { add(base(t)) }
        }
        return tags.prefix(maxEntries).enumerated().map { i, t in i == 0 ? t : "\(t);q=\(q(i))" }.joined(separator: ",")
    }

    /// 1.0, 0.9, 0.8 … never below 0.1.
    static func q(_ i: Int) -> String { String(format: "%.1f", locale: Locale(identifier: "en_US_POSIX"), Double(max(10 - i, 1)) / 10.0) }

    static func base(_ tag: String) -> String { String(tag.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false).first ?? "") }

    /// A language tag as language[-REGION] in ASCII ("ko_kr" -> "ko-KR"); nil for nothing usable ("und", "", "*").
    static func clean(_ tag: String) -> String? {
        let bits = tag.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: "_", with: "-")
            .split(separator: "-").map(String.init).filter { !$0.isEmpty }
        guard let first = bits.first else { return nil }
        let lang = first.lowercased()
        guard lang != "und", (2...3).contains(lang.count), lang.allSatisfy({ ("a"..."z").contains($0) }) else { return nil }
        let region = bits.dropFirst().first { b in
            (b.count == 2 && b.allSatisfy { $0.isASCII && $0.isLetter }) || (b.count == 3 && b.allSatisfy { $0.isASCII && $0.isNumber })
        }
        return region.map { "\(lang)-\($0.uppercased())" } ?? lang
    }
}
