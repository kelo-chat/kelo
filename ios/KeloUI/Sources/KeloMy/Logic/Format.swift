import Foundation
import KeloShared

/// Numbers, sizes and times in the app's language (Android my/Format.kt). The pure helpers take the locale; the @MainActor ones
/// use the app's language and its strings.
public enum MyFormat {
    /// 1234567 -> "1,234,567"
    public static func number(_ n: Int64, locale: Locale) -> String {
        let f = NumberFormatter()
        f.locale = locale
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? String(n)
    }

    /// 1.25 -> "1.3", as Android's String.format(locale, "%.1f") (android my/NumberText.kt): half up on the number's shortest
    /// decimal form (0.15 -> "0.2", 2.25 -> "2.3"), the language's decimal sign, no grouping. A NumberFormatter alone rounds half
    /// even on the binary value, so the same size read "2.2 GB" here and "2.3 GB" on the phone.
    public static func oneDecimal(_ v: Double, locale: Locale) -> String { fixed(v, digits: 1, locale: locale) }

    /// `v` with exactly `digits` decimals, rounded the way java.util.Formatter does ("%.Nf").
    public static func fixed(_ v: Double, digits: Int, locale: Locale) -> String {
        guard v.isFinite else { return String(v) }
        var exact = Decimal(string: "\(v)", locale: Locale(identifier: "en_US_POSIX")) ?? Decimal(v)   // the shortest form, as Java reads it
        var rounded = Decimal()
        NSDecimalRound(&rounded, &exact, digits, .plain)   // half up (away from zero)
        let f = NumberFormatter()
        f.locale = locale
        f.numberStyle = .decimal
        f.usesGroupingSeparator = false
        f.minimumFractionDigits = digits
        f.maximumFractionDigits = digits
        f.roundingMode = .halfUp
        return f.string(from: rounded as NSDecimalNumber) ?? String(format: "%.\(digits)f", v)
    }

    /// The size number and its unit: (value, "gb" | "mb" | "kb"). GB with one decimal, MB with one decimal under 10 MB, else KB.
    public static func storageParts(_ n: Int64, locale: Locale) -> (String, String) {
        let gb = 1024.0 * 1024 * 1024, mb = 1024.0 * 1024, d = Double(n)
        if d >= gb { return (oneDecimal(d / gb, locale: locale), "gb") }
        if d >= mb { return (d >= 10 * mb ? String(Int64((d / mb).rounded())) : oneDecimal(d / mb, locale: locale), "mb") }
        return (String(max(0, Int64((d / 1024).rounded()))), "kb")
    }

    /// A file's size: "1.2MB" from 1 MB, else at least "1KB".
    public static func fileParts(_ n: Int64, locale: Locale) -> (String, String) {
        let mb = 1024.0 * 1024, d = Double(n)
        if d >= mb { return (oneDecimal(d / mb, locale: locale), "mb") }
        return (String(max(1, Int64((d / 1024).rounded()))), "kb")
    }

    /// A CPU count: "2" for 2.0, "1.5" otherwise.
    public static func cpuCount(_ n: Double) -> String { n == n.rounded(.down) ? String(Int64(n)) : String(n) }

    /// 65.4 -> "1:05", 3723 -> "1:02:03".
    public static func clipLength(_ seconds: Double) -> String {
        let s = max(0, Int64(seconds))
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, sec) : String(format: "%d:%02d", m, sec)
    }

    /// How long ago, as a unit and amount: nil = never, ("now", 0), ("m", n), ("h", n), ("d", n).
    public static func ago(_ seconds: Double?, now: Double) -> (String, Int64)? {
        guard let seconds, seconds != 0 else { return nil }
        let s = max(0, now - seconds)
        if s < 120 { return ("now", 0) }
        if s < 7200 { return ("m", Int64(s / 60)) }
        if s < 172_800 { return ("h", Int64(s / 3600)) }
        return ("d", Int64(s / 86400))
    }

    /// "$0.19", "¥12.30": two decimals, the currency's own symbol when the system knows it.
    public static func money(_ v: Double, currency: String, locale: Locale) -> String {
        let f = NumberFormatter()
        f.locale = locale
        f.numberStyle = .currency
        f.currencyCode = currency.isEmpty ? "USD" : currency
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f.string(from: NSNumber(value: v)) ?? String(format: "%.2f %@", v, currency)
    }

    // ---- in the app's language ----------------------------------------------------------------------------------------------------

    @MainActor static var locale: Locale { Strings.shared.locale }

    @MainActor public static func number(_ n: Int64) -> String { number(n, locale: locale) }

    @MainActor public static func storageSize(_ n: Int64) -> String {
        let (v, u) = storageParts(n, locale: locale)
        switch u {
        case "gb": return L(R.string.size_gb, v)
        case "mb": return L(R.string.size_mb, v)
        default: return L(R.string.size_kb, v)
        }
    }

    @MainActor public static func fileSize(_ n: Int64) -> String {
        let (v, u) = fileParts(n, locale: locale)
        return u == "mb" ? L(R.string.size_mb, v) : L(R.string.size_kb, v)
    }

    @MainActor public static func agoText(_ seconds: Double?) -> String {
        guard let (u, n) = ago(seconds, now: Date().timeIntervalSince1970) else { return L(R.string.devices_never_used) }
        switch u {
        case "now": return L(R.string.ago_now)
        case "m": return L(R.string.ago_minutes, n)
        case "h": return L(R.string.ago_hours, n)
        default: return L(R.string.ago_days, n)
        }
    }

    @MainActor public static func date(_ seconds: Double, style: Date.FormatStyle.DateStyle = .abbreviated) -> String {
        Date(timeIntervalSince1970: seconds).formatted(Date.FormatStyle(date: style, time: .omitted).locale(locale))
    }

    @MainActor public static func dayTime(_ seconds: Double) -> String {
        Date(timeIntervalSince1970: seconds).formatted(Date.FormatStyle(date: .numeric, time: .shortened).locale(locale))
    }

    /// "11/6 06:00", or "아직 없어요".
    @MainActor public static func whenText(_ seconds: Double?) -> String {
        guard let seconds, seconds != 0 else { return L(R.string.vault_never) }
        return dayTime(seconds)
    }

    /// "11월 7일 오후 8:40": month, day and time.
    @MainActor public static func monthDayTime(_ seconds: Double) -> String {
        let f = DateFormatter()
        f.locale = locale
        f.setLocalizedDateFormatFromTemplate("MMMdjmm")
        return f.string(from: Date(timeIntervalSince1970: seconds))
    }

    /// "11월 2일" for a local day.
    @MainActor public static func monthDay(year: Int, month: Int, day: Int) -> String {
        var c = DateComponents(); c.year = year; c.month = month; c.day = day; c.hour = 12
        let d = Calendar(identifier: .gregorian).date(from: c) ?? Date()
        let f = DateFormatter()
        f.locale = locale
        f.setLocalizedDateFormatFromTemplate("MMMdEEE")
        return f.string(from: d)
    }

    /// "오후 8:40" for an hour and minute.
    @MainActor public static func clock(_ hour: Int, _ minute: Int) -> String {
        var c = DateComponents(); c.hour = hour; c.minute = minute; c.year = 2000; c.month = 1; c.day = 1
        let d = Calendar(identifier: .gregorian).date(from: c) ?? Date()
        return d.formatted(Date.FormatStyle(date: .omitted, time: .shortened).locale(locale))
    }

    @MainActor public static func money(_ v: Double, currency: String) -> String { money(v, currency: currency, locale: locale) }

    /// "5분 후" / "2시간 후" / "1일 후" for a delay in minutes (the follow-up pickers).
    @MainActor public static func later(_ minutes: Int) -> String {
        if minutes >= 1440 && minutes % 1440 == 0 { return P(R.plurals.agent_rel_days_future, minutes / 1440, minutes / 1440) }
        if minutes >= 60 && minutes % 60 == 0 { return P(R.plurals.agent_rel_hours_future, minutes / 60, minutes / 60) }
        return P(R.plurals.agent_rel_minutes_future, minutes, minutes)
    }
}
