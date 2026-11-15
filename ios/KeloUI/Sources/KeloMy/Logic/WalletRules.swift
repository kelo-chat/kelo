import Foundation
import KeloCore

// Money (amounts in a currency's minor units) lives in KeloCore (Wallet/Money.swift): schedule grants show amounts outside the wallet too.

/// Whether the two limit numbers can be saved.
public enum LimitCheck: Equatable, Sendable { case ok, missing, perAboveMonthly }

/// The limits page's rules (Android LimitRules).
public enum LimitRules {
    /// Which currencies the page lists: those the user changed or spent in this month, the phone's own currencies, and any added
    /// on this visit. The server's order is kept.
    public static func visible(_ rows: [WalletLimit], homes: Set<String>, added: Set<String> = []) -> [WalletLimit] {
        rows.filter { $0.custom || $0.spentThisMonth > 0 || homes.contains($0.currency) || added.contains($0.currency) }
    }

    /// Currencies that could be added: known defaults not listed yet.
    public static func addable(_ rows: [WalletLimit], shown: [WalletLimit]) -> [String] {
        let listed = Set(shown.map(\.currency))
        return rows.map(\.currency).filter { !listed.contains($0) }
    }

    /// Both 0 or more, and the per-payment one not above the monthly one (server rule).
    public static func check(_ perPayment: Double?, _ monthly: Double?) -> LimitCheck {
        guard let p = perPayment, let m = monthly, p >= 0, m >= 0 else { return .missing }
        return p > m ? .perAboveMonthly : .ok
    }

    /// How much of the monthly limit is used (0...1; a 0 limit counts as full).
    public static func used(_ spent: Double, _ monthly: Double) -> Double { monthly <= 0 ? 1 : min(1, max(0, spent / monthly)) }

    static let likelyRegion: [String: String] = [
        "ko": "KR", "ja": "JP", "zh": "CN", "en": "US", "de": "DE", "fr": "FR", "es": "ES", "it": "IT", "pt": "BR",
        "ru": "RU", "vi": "VN", "th": "TH", "id": "ID", "tr": "TR", "pl": "PL", "nl": "NL", "sv": "SE",
        "hi": "IN", "ar": "SA", "ms": "MY", "uk": "UA", "cs": "CZ", "da": "DK", "nb": "NO", "fi": "FI", "he": "IL",
    ]

    /// The phone's own currency (KRW in Korea), or nil. A language without a region takes the region its speakers mostly live in.
    public static func homeCurrency(_ locale: Locale) -> String? {
        let lang = locale.language.languageCode?.identifier ?? ""
        let region = locale.region?.identifier ?? likelyRegion[lang] ?? ""
        if region.isEmpty { return nil }
        return Locale(identifier: "\(lang)_\(region)").currency?.identifier
    }

    /// The app language's currency and the phone's region's currency.
    public static func homeCurrencies(app: Locale, system: Locale) -> Set<String> {
        Set([homeCurrency(app), homeCurrency(system)].compactMap { $0 })
    }
}

/// The add form's steps (Android CardFace.kt AddStep / AddCardSteps).
public enum AddStep: Int, CaseIterable, Sendable { case number, dates, owner, confirm }

public enum AddCardSteps {
    public static func next(_ s: AddStep) -> AddStep { AddStep(rawValue: s.rawValue + 1) ?? s }
    public static func prev(_ s: AddStep) -> AddStep? { AddStep(rawValue: s.rawValue - 1) }

    /// 1-based number of the step and how many there are, for "2 / 4".
    public static func position(_ s: AddStep) -> (Int, Int) { (s.rawValue + 1, AddStep.allCases.count) }

    /// NUMBER: a number that passes the check; DATES: an expiry not past nor too far and a CVC of the brand's length; OWNER:
    /// nothing required; CONFIRM: the notice ticked and everything above.
    public static func canLeave(_ s: AddStep, number: String, expiry: String, cvc: String, agreed: Bool, now: YearMonth) -> Bool {
        let numberOk = CardNumber.valid(number)
        var datesOk = false
        if case .ok = Expiry.check(expiry, now: now) { datesOk = Cvc.valid(cvc, brand: CardNumber.brand(CardNumber.digits(number))) }
        switch s {
        case .number: return numberOk
        case .dates: return datesOk
        case .owner: return true
        case .confirm: return numberOk && datesOk && agreed
        }
    }

    /// The first step that still blocks saving (to go back to), or nil.
    public static func firstBlocking(number: String, expiry: String, cvc: String, now: YearMonth) -> AddStep? {
        if !canLeave(.number, number: number, expiry: expiry, cvc: cvc, agreed: false, now: now) { return .number }
        if !canLeave(.dates, number: number, expiry: expiry, cvc: cvc, agreed: false, now: now) { return .dates }
        return nil
    }
}

/// The number field's check, once a full number is typed (Android CardForms numberError).
public enum NumberProblem: Equatable, Sendable { case invalid, otherCard(String) }

public enum WalletFormRules {
    public static func numberProblem(_ number: String, mustEnd: String?) -> NumberProblem? {
        let n = CardNumber.digits(number)
        let brand = CardNumber.brand(n)
        let complete = n.count >= (brand == .amex ? 15 : 16) || n.count == CardNumber.maxDigits
        if !complete { return nil }
        if !CardNumber.valid(n) { return .invalid }
        if let mustEnd, CardNumber.last4(n) != mustEnd { return .otherCard(mustEnd) }
        return nil
    }

    /// A card's name never holds card digits (the server refuses that too).
    public static func nameHasDigits(_ name: String) -> Bool {
        name.range(of: "(?:[0-9][ -]?){12,}", options: .regularExpression) != nil
    }

    /// A re-entered card can be saved: a valid number of the same card (same last 4) and a CVC of the brand's length.
    public static func reenterReady(number: String, cvc: String, card: WalletCard) -> Bool {
        let n = CardNumber.digits(number)
        let brand = n.isEmpty ? CardBrand.of(card.brand) : CardNumber.brand(n)
        return CardNumber.valid(n) && CardNumber.last4(n) == card.last4 && Cvc.valid(cvc, brand: brand)
    }
}

/// The card pages' own sub pages (Android WalletView).
public enum WalletView: Equatable, Sendable {
    case list, add, limits, payments
    case card(String)
    /// Re-enter the number and CVC: to repair the phone copy, or (`autoPay`) to turn auto-pay on.
    case reenter(String, autoPay: Bool)
    /// A shipping address: a saved one by its id, or a new one (nil).
    case address(String?)

    /// Where back goes from here (nil: leave the card pages).
    public var back: WalletView? {
        switch self {
        case .list: nil
        case .reenter(let id, _): .card(id)
        default: .list
        }
    }
}

/// What the add form holds while it is filled in (memory only).
public struct AddCardDraft: Equatable, Sendable {
    public var step: AddStep = .number
    public var number = "", expiry = "", cvc = "", holder = "", postal = ""
    /// The owner's name for the card ("생활비 카드"): its title everywhere; empty = brand and last 4
    public var nickname = ""
    public var agreed = false
    public init() {}
    public var typed: Bool { !number.isEmpty || !expiry.isEmpty || !cvc.isEmpty || !holder.isEmpty || !postal.isEmpty || !nickname.isEmpty }
}

public struct ReenterDraft: Equatable, Sendable {
    public var cardId: String
    public var autoPay: Bool
    public var number = "", cvc = ""
    public init(cardId: String, autoPay: Bool) { self.cardId = cardId; self.autoPay = autoPay }
    public var typed: Bool { !number.isEmpty || !cvc.isEmpty }
}

/// The page that was open and what was typed on it, kept through the lock (Android WalletPlaces): memory only, never on disk,
/// forgotten ten minutes after the last touch or when the card pages are left.
@MainActor public enum WalletPlaces {
    public static let keepSeconds: Double = 10 * 60
    private static var view: WalletView = .list
    private static var add: AddCardDraft?
    private static var reenter: ReenterDraft?
    private static var at: Double = 0

    static func now() -> Double { ProcessInfo.processInfo.systemUptime }

    private static func fresh() -> Bool {
        let t = now()
        if at > 0 && t - at < keepSeconds && t >= at { return true }
        clear()
        return false
    }

    public static func currentView() -> WalletView { fresh() ? view : .list }
    public static func go(_ v: WalletView) { view = v; at = now() }
    public static func touch() { if fresh() { at = now() } }

    public static func addDraft() -> AddCardDraft? { fresh() ? add : nil }
    public static func saveAdd(_ d: AddCardDraft) { add = d; at = now() }

    public static func reenterDraft(_ cardId: String, autoPay: Bool) -> ReenterDraft? {
        guard fresh(), let r = reenter, r.cardId == cardId, r.autoPay == autoPay else { return nil }
        return r
    }
    public static func saveReenter(_ d: ReenterDraft) { reenter = d; at = now() }

    /// Something typed is waiting behind the lock.
    public static func hasTyping() -> Bool { fresh() && ((add?.typed ?? false) || (reenter?.typed ?? false)) }

    public static func clear() { view = .list; add = nil; reenter = nil; at = 0 }
}
