import Foundation
import Observation

/// A string's key (Android's R.string name).
public struct StringKey: Hashable, Sendable, ExpressibleByStringLiteral {
    public let name: String
    public init(_ name: String) { self.name = name }
    public init(stringLiteral value: String) { name = value }
}

/// A plural's key (Android's R.plurals name).
public struct PluralKey: Hashable, Sendable {
    public let name: String
    public init(_ name: String) { self.name = name }
}

/// A string array's key (Android's R.array name).
public struct ArrayKey: Hashable, Sendable {
    public let name: String
    public init(_ name: String) { self.name = name }
}

/// A language the app can be shown in, under its own name (Android core/Languages.kt).
public struct AppLanguage: Hashable, Sendable, Identifiable {
    public let code: String
    public let name: String
    public var id: String { code }
}

/// Android core/Languages.kt, same list, order and matching rules.
public enum Languages {
    /// The choice that follows the phone's own language (English when the app lacks it).
    public static let system = "system"

    public static let all: [AppLanguage] = [
        .init(code: "en", name: "English"), .init(code: "ko", name: "한국어"), .init(code: "ja", name: "日本語"),
        .init(code: "zh-Hans", name: "简体中文"), .init(code: "zh-Hant", name: "繁體中文"), .init(code: "es", name: "Español"),
        .init(code: "fr", name: "Français"), .init(code: "de", name: "Deutsch"), .init(code: "pt-BR", name: "Português (Brasil)"),
        .init(code: "it", name: "Italiano"), .init(code: "ru", name: "Русский"), .init(code: "id", name: "Bahasa Indonesia"),
        .init(code: "vi", name: "Tiếng Việt"), .init(code: "th", name: "ไทย"), .init(code: "tr", name: "Türkçe"),
        .init(code: "ar", name: "العربية"), .init(code: "hi", name: "हिन्दी"), .init(code: "pl", name: "Polski"),
        .init(code: "nl", name: "Nederlands"), .init(code: "uk", name: "Українська"),
    ]

    public static func isKnown(_ code: String?) -> Bool { code == system || (code != nil && all.contains { $0.code == code }) }

    /// A picked language (not `system`) or nil.
    public static func override(_ code: String?) -> String? {
        guard let code, code != system, all.contains(where: { $0.code == code }) else { return nil }
        return code
    }

    /// Which of `all` a device language tag ("en-US", "zh-Hant-TW", "zh-HK", "pt-PT", "in-ID") is shown in; nil when the app lacks it.
    public static func match(_ tag: String) -> String? {
        let parts = tag.replacingOccurrences(of: "_", with: "-").split(separator: "-").map(String.init)
        guard let first = parts.first?.lowercased(), !first.isEmpty else { return nil }
        let lang: String = switch first { case "in": "id"; case "iw": "he"; default: first }
        if lang == "zh" {
            let rest = parts.dropFirst()
            let script = rest.first { $0.count == 4 }?.lowercased()
            let region = rest.first { $0.count == 2 }?.uppercased()
            let traditional = script == "hant" || (script == nil && ["TW", "HK", "MO"].contains(region ?? ""))
            return traditional ? "zh-Hant" : "zh-Hans"
        }
        if lang == "pt" { return "pt-BR" }
        return all.first { $0.code == lang }?.code
    }

    /// The first of the device's languages the app has; otherwise English.
    public static func suggested(_ deviceTags: [String]) -> String { deviceTags.lazy.compactMap(match).first ?? "en" }

    public static func name(_ code: String?) -> String { all.first { $0.code == code }?.name ?? "" }

    /// What a pick in 마이 → 언어 tells the server (PUT /api/language, the language of notifications, the briefing and scheduled
    /// runs): the picked code, or for `system` the one the app is shown in for the phone's languages.
    public static func forServer(_ code: String?, deviceTags: [String]) -> String { override(code) ?? suggested(deviceTags) }

    /// Right-to-left languages among `all`.
    public static func isRightToLeft(_ code: String) -> Bool { code == "ar" }
}

/// CLDR cardinal plural categories for whole numbers, for the app's 20 languages (the same categories the Android strings use).
public enum PluralRules {
    public static func category(_ n: Int, language: String) -> String {
        let lang = language.split(separator: "-").first.map { String($0).lowercased() } ?? "en"
        let i = abs(n)
        switch lang {
        case "ko", "ja", "zh", "id", "vi", "th":
            return "other"
        case "en", "de", "nl", "it", "tr":
            if i == 1 { return "one" }
            if lang == "it" && i != 0 && i % 1_000_000 == 0 { return "many" }
            return "other"
        case "hi":
            return i == 0 || i == 1 ? "one" : "other"
        case "es":
            if i == 1 { return "one" }
            return i != 0 && i % 1_000_000 == 0 ? "many" : "other"
        case "fr", "pt":
            if i == 0 || i == 1 { return "one" }
            return i % 1_000_000 == 0 ? "many" : "other"
        case "ru", "uk":
            let m10 = i % 10, m100 = i % 100
            if m10 == 1 && m100 != 11 { return "one" }
            if (2...4).contains(m10) && !(12...14).contains(m100) { return "few" }
            return "many"
        case "pl":
            let m10 = i % 10, m100 = i % 100
            if i == 1 { return "one" }
            if (2...4).contains(m10) && !(12...14).contains(m100) { return "few" }
            return "many"
        case "ar":
            let m100 = i % 100
            if i == 0 { return "zero" }
            if i == 1 { return "one" }
            if i == 2 { return "two" }
            if (3...10).contains(m100) { return "few" }
            if (11...99).contains(m100) { return "many" }
            return "other"
        default:
            return i == 1 ? "one" : "other"
        }
    }
}

/// Fills Android-style placeholders: %1$s, %2$d, %s, %d (in order), %% -> %. A missing argument leaves the placeholder as it is.
public enum StringFormat {
    public static func format(_ template: String, _ args: [Any]) -> String {
        if args.isEmpty && !template.contains("%%") { return template }
        var out = ""
        var next = 0
        let chars = Array(template)
        var i = 0
        while i < chars.count {
            let c = chars[i]
            guard c == "%", i + 1 < chars.count else { out.append(c); i += 1; continue }
            if chars[i + 1] == "%" { out.append("%"); i += 2; continue }
            // %[n$][flags/width/.precision]conv
            var j = i + 1
            var index: Int?
            var digits = ""
            while j < chars.count, chars[j].isNumber { digits.append(chars[j]); j += 1 }
            if j < chars.count, chars[j] == "$", let n = Int(digits) { index = n - 1; j += 1; digits = "" }
            var spec = digits
            while j < chars.count, "-+ 0#.,".contains(chars[j]) || chars[j].isNumber { spec.append(chars[j]); j += 1 }
            guard j < chars.count, "sdfx@".contains(chars[j]) else { out.append(c); i += 1; continue }
            let conv = chars[j]
            let argIndex = index ?? next
            if index == nil { next += 1 }
            guard argIndex >= 0, argIndex < args.count else { out.append(contentsOf: chars[i...j]); i = j + 1; continue }
            out += render(args[argIndex], conv: conv, spec: spec)
            i = j + 1
        }
        return out
    }

    private static func render(_ arg: Any, conv: Character, spec: String) -> String {
        switch conv {
        case "d":
            if let v = arg as? Int { return String(v) }
            if let v = arg as? Int64 { return String(v) }
            if let v = arg as? Double { return String(Int(v)) }
            return "\(arg)"
        case "f":
            let v = (arg as? Double) ?? (arg as? Int).map(Double.init) ?? 0
            return String(format: "%" + spec + "f", v)
        default:
            return "\(arg)"
        }
    }
}

/// The app's words in the language it is shown in. The picked language can differ from the phone's (picked in 마이 →
/// 언어), so lookups go to that language's .lproj directly, falling back to English and then to the key (as Android falls back to
/// values/).
@MainActor @Observable
public final class Strings {
    public static let shared = Strings()

    /// The language shown (one of `Languages.all`).
    public private(set) var code: String = "en"
    @ObservationIgnored private var bundle: Bundle?
    @ObservationIgnored private var english: Bundle?
    @ObservationIgnored private var table: [String: String] = [:]
    @ObservationIgnored private var englishTable: [String: String] = [:]

    public init(code: String? = nil) {
        english = Self.lproj("en")
        englishTable = Self.load(english)
        use(code ?? Languages.suggested(Locale.preferredLanguages))
    }

    /// Show the app in `choice` (a code or `Languages.system`).
    public func apply(choice: String?) {
        use(Languages.override(choice) ?? Languages.suggested(Locale.preferredLanguages))
    }

    public func use(_ code: String) {
        let c = Languages.all.contains { $0.code == code } ? code : "en"
        self.code = c
        bundle = Self.lproj(c)
        table = c == "en" ? englishTable : Self.load(bundle)
    }

    public var locale: Locale { Locale(identifier: code) }
    public var isRightToLeft: Bool { Languages.isRightToLeft(code) }

    public func raw(_ name: String) -> String? { table[name] ?? englishTable[name] }

    public func string(_ key: StringKey, _ args: [Any] = []) -> String {
        _ = code   // observed: views re-render on a language change
        return StringFormat.format(raw(key.name) ?? key.name, args)
    }

    public func plural(_ key: PluralKey, _ count: Int, _ args: [Any]) -> String {
        _ = code
        let cat = PluralRules.category(count, language: code)
        let text = table["\(key.name)##\(cat)"] ?? table["\(key.name)##other"]
            ?? englishTable["\(key.name)##\(PluralRules.category(count, language: "en"))"] ?? englishTable["\(key.name)##other"] ?? key.name
        return StringFormat.format(text, args)
    }

    public func array(_ key: ArrayKey) -> [String] {
        _ = code
        let n = Int(raw("\(key.name)##count") ?? "") ?? 0
        return (0..<n).map { raw("\(key.name)##\($0)") ?? "" }
    }

    private static func lproj(_ code: String) -> Bundle? {
        if let p = Bundle.module.path(forResource: code, ofType: "lproj"), let b = Bundle(path: p) { return b }
        if let p = Bundle.module.path(forResource: code.lowercased(), ofType: "lproj"), let b = Bundle(path: p) { return b }
        return nil
    }

    /// The whole Localizable.strings of a language (read once; ~1500 entries).
    private static func load(_ bundle: Bundle?) -> [String: String] {
        guard let url = bundle?.url(forResource: "Localizable", withExtension: "strings"),
              let dict = NSDictionary(contentsOf: url) as? [String: String] else { return [:] }
        return dict
    }
}

/// `L(R.string.x)` / `L(R.string.x, arg)`: Android's stringResource.
@MainActor public func L(_ key: StringKey, _ args: Any...) -> String { Strings.shared.string(key, args) }
/// `P(R.plurals.x, count, args...)`: Android's pluralStringResource.
@MainActor public func P(_ key: PluralKey, _ count: Int, _ args: Any...) -> String { Strings.shared.plural(key, count, args) }
/// `LA(R.array.x)`: Android's stringArrayResource.
@MainActor public func LA(_ key: ArrayKey) -> [String] { Strings.shared.array(key) }
/// The language the app speaks now (its locale and its code): the text API's other two answers, so no view reaches `Strings.shared`.
@MainActor public func appLocale() -> Locale { Strings.shared.locale }
@MainActor public func appLanguageCode() -> String { Strings.shared.code }
/// A string by its name (the connector and vault rules return names; R.string holds the same ones).
@MainActor public func LS(_ name: String, _ args: Any...) -> String { Strings.shared.string(StringKey(name), args) }
