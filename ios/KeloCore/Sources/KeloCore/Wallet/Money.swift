import Foundation

/// Amounts in a currency's own minor units (Android wallet/Money.kt): KRW, JPY and other zero-decimal currencies without decimals,
/// USD/EUR and the like with two. The currency itself is put around the number by a string (wallet_money_*).
public enum Money {
    /// Currencies paid in whole units even where ISO 4217 lists minor units nobody uses (the server's limits are whole too).
    public static let whole: Set<String> = ["KRW", "JPY", "VND", "IDR", "CLP", "ISK", "HUF", "TWD", "UGX", "PYG", "XAF", "XOF"]

    public static func decimals(_ currency: String) -> Int {
        let code = currency.uppercased()
        if whole.contains(code) { return 0 }
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = code
        let d = f.maximumFractionDigits
        return d >= 0 ? d : 2
    }

    /// 30000 KRW -> "30,000"; 25 USD -> "25.00" (in `locale`'s digits and separators).
    public static func number(_ amount: Double, _ currency: String, locale: Locale) -> String {
        let d = decimals(currency)
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.locale = locale
        f.usesGroupingSeparator = true
        f.minimumFractionDigits = d
        f.maximumFractionDigits = d
        f.roundingMode = .halfUp
        return f.string(from: NSNumber(value: amount)) ?? String(amount)
    }

    /// The number to put back in an edit field: no grouping, "." as the decimal point, no trailing ".00" on whole amounts.
    public static func plain(_ amount: Double, _ currency: String) -> String {
        let d = decimals(currency)
        let scale = pow(10.0, Double(d))
        let v = (amount * scale).rounded() / scale
        if v == v.rounded() { return String(Int64(v)) }
        return String(format: "%.\(d)f", v)
    }

    /// What the user typed in a limit field: digits with grouping commas or spaces, a decimal point only where the currency has
    /// minor units ("1,000" KRW -> 1000; "12.5" USD -> 12.5; "12.5" KRW -> nil). Empty -> nil.
    public static func parse(_ text: String, _ currency: String) -> Double? {
        let t = text.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: "")
            .replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "\u{00A0}", with: "")
        if t.isEmpty { return nil }
        let d = decimals(currency)
        let pattern = d == 0 ? "^[0-9]{1,13}$" : "^[0-9]{1,13}(\\.[0-9]{1,\(d)})?$"
        guard t.range(of: pattern, options: .regularExpression) != nil else { return nil }
        return Double(t)
    }

    /// The currency code is a 3-letter ISO code (the server's rule).
    public static func validCode(_ text: String) -> Bool { text.range(of: "^[A-Z]{3}$", options: .regularExpression) != nil }
}
