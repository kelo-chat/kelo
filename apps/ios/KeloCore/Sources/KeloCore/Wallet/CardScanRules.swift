import Foundation

// Card scanning, the parts that are plain rules (Android wallet/CardScanRules.kt): what a frame of recognised text says, when several
// frames agree, and what a scan puts into the form. The CVC is never read: it is printed on the back and the owner types it. Nothing
// here keeps, logs or sends a number; a reading lives only until the form takes it.

/// What a scan fills in: the number (digits only) and, when it was read, the expiry as the form's "MMYY" digits.
public struct CardScanResult: Sendable, CustomStringConvertible {
    public let number: String
    public let expiry: String?
    public init(number: String, expiry: String?) { self.number = number; self.expiry = expiry }
    /// Never the number, not even in a debugger's or a crash's text.
    public var description: String { "CardScanResult(…)" }
}

/// What one camera frame said: a card number and the expiry dates printed on it (both may be missing).
public struct CardScanRead: Sendable {
    public let number: String?
    public let expiries: [String]
    public init(number: String?, expiries: [String]) { self.number = number; self.expiries = expiries }
}

public enum CardScanText {
    /// How OCR often misreads a digit inside a run of digits ("4O12" -> "4012").
    static let lookalike: [Character: Character] = ["O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "S": "5",
                                                    "B": "8", "Z": "2"]

    /// Reads one frame's text lines.
    public static func read(_ lines: [String]) -> CardScanRead {
        var number: String?
        var dates: [String] = []
        for line in lines {
            if number == nil { number = self.number(line) }
            dates += expiries(line)
        }
        // the recognizer sometimes splits the number's groups over lines: try neighbouring lines made only of digits together
        if number == nil {
            let groups = lines.map { $0.trimmingCharacters(in: .whitespaces) }
            outer: for i in groups.indices where isDigitLine(groups[i]) {
                var joined = groups[i]
                for j in (i + 1)..<min(groups.count, i + 4) {
                    if !isDigitLine(groups[j]) { break }
                    joined += " " + groups[j]
                    if let n = self.number(joined) { number = n; break outer }
                }
            }
        }
        return CardScanRead(number: number, expiries: dates)
    }

    /// `\d{3,6}( \d{3,7})*`
    static func isDigitLine(_ s: String) -> Bool {
        let parts = s.split(separator: " ", omittingEmptySubsequences: false)
        guard let first = parts.first, (3...6).contains(first.count), first.allSatisfy(\.isASCIIDigit) else { return false }
        return parts.dropFirst().allSatisfy { (3...7).contains($0.count) && $0.allSatisfy(\.isASCIIDigit) }
    }

    /// A card number in `line`: 13 to 19 digits, together or in groups split by spaces or dashes, passing the check digit, with a
    /// length its brand uses. A longer run of digits (an account number, a barcode) is not cut down to fit.
    public static func number(_ line: String) -> String? {
        for run in runs(lookalikes(line)) {
            let digits = run.filter(\.isASCIIDigit)
            guard (13...19).contains(digits.count), groupsLookLikeACard(run), plausible(String(digits)) else { continue }
            return String(digits)
        }
        return nil
    }

    /// Runs of digits with single spaces or dashes between them (`\d(?:[ -]?\d)*`).
    static func runs(_ s: String) -> [String] {
        var out: [String] = []
        let chars = Array(s)
        var i = 0
        while i < chars.count {
            guard chars[i].isASCIIDigit else { i += 1; continue }
            var j = i + 1
            while j < chars.count {
                if chars[j].isASCIIDigit { j += 1; continue }
                if (chars[j] == " " || chars[j] == "-"), j + 1 < chars.count, chars[j + 1].isASCIIDigit { j += 2; continue }
                break
            }
            out.append(String(chars[i..<j]))
            i = j
        }
        return out
    }

    /// The checks a candidate number passes: check digit, no leading 0, not one digit repeated, a length its brand has.
    public static func plausible(_ digits: String) -> Bool {
        guard (13...19).contains(digits.count), digits.allSatisfy(\.isASCIIDigit), let first = digits.first else { return false }
        if first == "0" || digits.allSatisfy({ $0 == first }) { return false }
        if !CardNumber.luhn(digits) { return false }
        switch CardNumber.brand(digits) {
        case .amex: return digits.count == 15
        case .visa: return [13, 16, 19].contains(digits.count)
        case .mastercard: return digits.count == 16
        default: return (14...19).contains(digits.count)
        }
    }

    /// Groups of 3 to 6 digits (4-4-4-4, 4-6-5, 4-6-4, …) or one block; a run like "12 345 6 78 …" is not a card number.
    static func groupsLookLikeACard(_ run: String) -> Bool {
        let parts = run.split(whereSeparator: { $0 == " " || $0 == "-" })
        if parts.count == 1 { return true }
        if parts.count > 5 { return false }
        return parts.dropLast().allSatisfy { (3...6).contains($0.count) } && (1...7).contains(parts.last!.count)
    }

    /// Expiry dates in `line` as "MMYY" ("04/28", "04-28", "04 / 2028", "04.28").
    public static func expiries(_ line: String) -> [String] {
        guard let re = try? NSRegularExpression(pattern: #"(?<![\d/.\-])(0[1-9]|1[0-2])\s?[/\-.]\s?(20\d{2}|\d{2})(?![\d/.\-])"#) else { return [] }
        let ns = line as NSString
        return re.matches(in: line, range: NSRange(location: 0, length: ns.length)).map { m in
            let month = ns.substring(with: m.range(at: 1))
            let year = ns.substring(with: m.range(at: 2))
            return month + String(year.suffix(2))
        }
    }

    static func lookalikes(_ line: String) -> String {
        guard line.contains(where: { lookalike[$0] != nil }) else { return line }
        var chars = Array(line)
        let original = chars
        for i in chars.indices {
            guard let to = lookalike[original[i]] else { continue }
            // only between digits (or a digit and a separator), so words like "VISA" or "BANK" stay as they are
            let before: Character? = i > 0 ? original[i - 1] : nil
            let after: Character? = i + 1 < original.count ? original[i + 1] : nil
            let nearDigit = (before?.isASCIIDigit ?? false) || (after?.isASCIIDigit ?? false)
            let nearLetter = (before.map { $0.isLetter && lookalike[$0] == nil } ?? false) || (after.map { $0.isLetter && lookalike[$0] == nil } ?? false)
            if nearDigit && !nearLetter { chars[i] = to }
        }
        return String(chars)
    }

    /// The later of the dates (a card may show both "valid from" and "valid thru"), or nil.
    public static func latest(_ expiries: some Collection<String>) -> String? {
        expiries.max { ($0.suffix(2) + $0.prefix(2)) < ($1.suffix(2) + $1.prefix(2)) }
    }
}

/// Several frames must agree before a scan counts: the same number in `numberFrames` frames. The expiry is taken when the same date
/// was seen in `expiryFrames` frames; after the number is settled, up to `expiryGrace` more frames are given to the date, and then the
/// scan ends without it (the owner types it). `clear` forgets everything read so far.
public final class CardScanAgreement: @unchecked Sendable {
    public static let numberFrames = 3, expiryFrames = 2, expiryGrace = 10
    static let maxKept = 12
    private let now: () -> YearMonth
    private var numbers: [String: Int] = [:]
    private var dates: [String: Int] = [:]
    private var settled: String?
    private var since = 0

    public init(now: @escaping () -> YearMonth = { YearMonth.now() }) { self.now = now }

    /// One frame's reading; the result once the frames agree, else nil.
    public func offer(_ read: CardScanRead) -> CardScanResult? {
        for d in Set(read.expiries) {
            guard case .ok = Expiry.check(d, now: now()) else { continue }   // a "valid from" date in the past, or no date at all
            dates[d, default: 0] += 1
        }
        if settled == nil, let n = read.number {
            numbers[n, default: 0] += 1
            if numbers[n]! >= Self.numberFrames { settled = n }
        }
        if numbers.count > Self.maxKept { numbers = [:] }   // a moving camera reading noise: start over rather than grow
        if dates.count > Self.maxKept { dates = [:] }
        guard let number = settled else { return nil }
        let date = CardScanText.latest(dates.filter { $0.value >= Self.expiryFrames }.keys)
        if date != nil || since >= Self.expiryGrace {
            let result = CardScanResult(number: number, expiry: date)
            clear()
            return result
        }
        since += 1
        return nil
    }

    public func clear() { numbers = [:]; dates = [:]; settled = nil; since = 0 }
}

private extension Character {
    var isASCIIDigit: Bool { ("0"..."9").contains(self) }
}
