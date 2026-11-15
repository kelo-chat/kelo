import Foundation

/// The card brands the server knows (wallet.py brand_of), in the same order of checks.
public enum CardBrand: String, CaseIterable, Sendable {
    case visa, amex, mastercard, jcb, discover, unionpay, diners, other

    public static func of(_ wire: String?) -> CardBrand { CardBrand(rawValue: (wire ?? "").lowercased()) ?? .other }
}

/// Card number rules on the phone: the same Luhn check and brand detection as the server (wallet.py), plus the grouping the add form
/// shows while typing (Android wallet/CardRules.kt). Nothing here keeps or logs a number.
public enum CardNumber {
    public static let minDigits = 12
    public static let maxDigits = 19

    /// Only the digits of what was typed or pasted (spaces and dashes dropped), at most `maxDigits`.
    public static func digits(_ text: String) -> String {
        var out = ""
        for ch in text where ch >= "0" && ch <= "9" && ch.isASCII {
            out.append(ch)
            if out.count == maxDigits { break }
        }
        return out
    }

    public static func luhn(_ number: String) -> Bool {
        guard !number.isEmpty, number.allSatisfy({ $0.isASCII && $0.isNumber }) else { return false }
        var total = 0
        for (i, ch) in number.reversed().enumerated() {
            var d = Int(String(ch))!
            if i % 2 == 1 { d = d > 4 ? d * 2 - 9 : d * 2 }
            total += d
        }
        return total % 10 == 0
    }

    static func prefix(_ n: String, _ len: Int) -> Int { n.count >= len ? Int(n.prefix(len)) ?? 0 : 0 }

    /// wallet.py brand_of: works on a partial number too.
    public static func brand(_ number: String) -> CardBrand {
        let n = digits(number)
        if n.isEmpty { return .other }
        let p2 = prefix(n, 2), p3 = prefix(n, 3), p4 = prefix(n, 4)
        if n.hasPrefix("4") { return .visa }
        if n.hasPrefix("34") || n.hasPrefix("37") { return .amex }
        if (51...55).contains(p2) || (2221...2720).contains(p4) { return .mastercard }
        if (3528...3589).contains(p4) { return .jcb }
        if n.hasPrefix("6011") || n.hasPrefix("65") || (644...649).contains(p3) { return .discover }
        if n.hasPrefix("62") { return .unionpay }
        if n.hasPrefix("36") || n.hasPrefix("38") || n.hasPrefix("39") || (300...305).contains(p3) { return .diners }
        return .other
    }

    /// Where the spaces go: 4-6-5 for American Express, 4-6-4 for Diners (14), otherwise groups of 4.
    public static func groups(_ brand: CardBrand, length: Int) -> [Int] {
        if brand == .amex { return [4, 6, 5] }
        if brand == .diners && length <= 14 { return [4, 6, 4] }
        return Array(repeating: 4, count: (max(length, 16) + 3) / 4)
    }

    static func chunk(_ chars: [Character], sizes: [Int], keepRest: Bool) -> String {
        var parts: [String] = []
        var at = 0
        for size in sizes {
            if at >= chars.count { break }
            parts.append(String(chars[at..<min(at + size, chars.count)]))
            at += size
        }
        if keepRest && at < chars.count { parts.append(String(chars[at...])) }
        return parts.joined(separator: " ")
    }

    /// "4242424242424242" -> "4242 4242 4242 4242" (a partial number is grouped as far as it goes).
    public static func group(_ number: String) -> String {
        let n = digits(number)
        return chunk(Array(n), sizes: groups(brand(n), length: n.count), keepRest: true)
    }

    /// How many digits a complete number of `brand` has (the form stops there).
    public static func maxLength(_ brand: CardBrand) -> Int { brand == .amex ? 15 : maxDigits }

    /// Whether the typed number can be saved: 12 to 19 digits that pass the check digit.
    public static func valid(_ number: String) -> Bool {
        let n = digits(number)
        return (minDigits...maxDigits).contains(n.count) && luhn(n)
    }

    public static func last4(_ number: String) -> String { String(digits(number).suffix(4)) }
}

/// The CVC: 4 digits on American Express, 3 on the others.
public enum Cvc {
    public static func length(_ brand: CardBrand) -> Int { brand == .amex ? 4 : 3 }
    public static func clean(_ text: String, brand: CardBrand) -> String { String(text.filter { $0.isASCII && $0.isNumber }.prefix(length(brand))) }
    public static func valid(_ cvc: String, brand: CardBrand) -> Bool { cvc.count == length(brand) && cvc.allSatisfy { $0.isASCII && $0.isNumber } }
}

/// A year and month (card expiry).
public struct YearMonth: Comparable, Hashable, Sendable {
    public let year: Int, month: Int
    public init(year: Int, month: Int) { self.year = year; self.month = month }
    public static func < (a: YearMonth, b: YearMonth) -> Bool { (a.year, a.month) < (b.year, b.month) }
    public static func now(calendar: Calendar = .current) -> YearMonth {
        let c = calendar.dateComponents([.year, .month], from: Date())
        return YearMonth(year: c.year ?? 2000, month: c.month ?? 1)
    }
}

/// What the expiry field holds after a check.
public enum ExpiryCheck: Equatable, Sendable {
    case ok(month: Int, year: Int), incomplete, badMonth, expired, tooFar
}

/// The "MM/YY" field: typed as up to 4 digits, shown with the slash.
public enum Expiry {
    /// Up to 4 digits; a first digit 2-9 means a single-digit month, so a 0 goes in front ("4" -> "04").
    public static func clean(_ text: String) -> String {
        let d = text.filter { $0.isASCII && $0.isNumber }
        let fixed = d.count == 1 && ("2"..."9").contains(d) ? "0" + d : d
        return String(fixed.prefix(4))
    }

    /// "0428" -> "04/28" (the slash shows once the month is complete).
    public static func format(_ digits: String) -> String { digits.count <= 2 ? digits : digits.prefix(2) + "/" + digits.dropFirst(2) }

    /// A card is good through the last day of its month (wallet.py check_expiry).
    public static func check(_ digits: String, now: YearMonth) -> ExpiryCheck {
        guard digits.count == 4, digits.allSatisfy({ $0.isASCII && $0.isNumber }) else { return .incomplete }
        let m = Int(digits.prefix(2))!, y = 2000 + Int(digits.suffix(2))!
        guard (1...12).contains(m) else { return .badMonth }
        if YearMonth(year: y, month: m) < now { return .expired }
        if y > now.year + 25 { return .tooFar }
        return .ok(month: m, year: y)
    }

    /// "04/28" for display on a saved card.
    public static func display(month: Int, year: Int) -> String { String(format: "%02d/%02d", month, year % 100) }
}

/// How a saved card is named when only the last digits may show.
public enum CardMask {
    public static let dots = "••"
    public static func last4(_ text: String?) -> String { String((text ?? "").filter { $0.isASCII && $0.isNumber }.suffix(4)) }
}

/// A card drawn as a small face: a colour pair per brand (Kelo's own, no brand logos), the brand's short name and the last four.
public enum CardFaceRules {
    /// The two colours (ARGB) of a brand's face; both stops keep white text at 4.5:1 or more.
    public static func palette(_ brand: CardBrand) -> (UInt32, UInt32) {
        switch brand {
        case .visa: return (0xFF1C2E8C, 0xFF3D5AD8)
        case .mastercard: return (0xFF3A1F1A, 0xFFB34A16)
        case .amex: return (0xFF0E5E7A, 0xFF1C7A8C)
        case .jcb: return (0xFF0F5A3A, 0xFF237A4C)
        case .discover: return (0xFF6B3A0A, 0xFFA85C0C)
        case .unionpay: return (0xFF6E0F1E, 0xFFC62F3F)
        case .diners: return (0xFF2B3440, 0xFF5E6B7C)
        case .other: return (0xFF3B3B46, 0xFF6A6A7A)
        }
    }

    /// "•• 4242", or just the dots when the last four are not known.
    public static func lastFour(_ last4: String) -> String {
        let d = String(last4.filter { $0.isNumber }.suffix(4))
        return "•• " + (d.isEmpty ? "••••" : d)
    }

    /// The number as the add form's preview shows it while typing: the typed digits grouped like the field, the rest of the expected
    /// length as dots, and only the first six and the last four ever shown once the number is long.
    public static func preview(_ digits: String) -> String {
        let d = Array(CardNumber.digits(digits))
        let brand = CardNumber.brand(String(d))
        let length = max(d.count, brand == .amex ? 15 : 16)
        var masked: [Character] = []
        for i in 0..<length {
            if i >= d.count { masked.append("•") }
            else if d.count > 10 && i >= 6 && i < d.count - 4 { masked.append("•") }
            else { masked.append(d[i]) }
        }
        return CardNumber.chunk(masked, sizes: CardNumber.groups(brand, length: length), keepRest: false)
    }
}
