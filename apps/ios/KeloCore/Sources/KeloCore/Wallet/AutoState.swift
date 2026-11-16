import Foundation

// Whether a card pays on its own, why it asks each time, how many cards pay on their own, and what a schedule may do without
// asking: the server's fields (WALLET API and SCHEDULES in server.py, schedule_grants.py) read the way Android does
// (wallet/WalletModels.kt, agent/ScheduleGrants.kt), with the same fallbacks for an older server that does not send them.

extension WalletCard {
    public static let reasonNotAuto = "not_auto"
    public static let reasonNoServerCopy = "no_server_copy"
    public static let reasonPaused = "paused"
    public static let reasonVaultLocked = "vault_locked"

    /// Pays on its own now (an older server without `pays_automatically`: its auto-pay switch); never an expired or unfinished card.
    public var autoNow: Bool { (paysAutomatically ?? autoPay) && usable && !expired }

    /// The card-wide reason it asks, also for an older server that does not send one ("" when it pays on its own).
    public var reason: String { askReason.isEmpty ? (autoPay ? "" : WalletCard.reasonNotAuto) : askReason }

    /// The card can pay at all (not expired, finished), so it either pays on its own or asks each time.
    public var canPay: Bool { usable && !expired }

    /// A card that can pay but was saved to ask each time: its row offers turning auto-pay on (Android WalletScreens.offersTurnOn).
    public var offersTurnOn: Bool { canPay && !autoPay }
}

extension WalletInfo {
    /// Cards that pay on their own right now: the server's count, else counted here (none while paused).
    public var autoCount: Int { autoCards ?? (paused ? 0 : cards.filter(\.autoNow).count) }
}

extension ScheduleGrant {
    public static let kindLogin = "login"
    public static let kindPurchase = "purchase"
    public var isLogin: Bool { kind == ScheduleGrant.kindLogin }
    public var isPurchase: Bool { kind == ScheduleGrant.kindPurchase }
    /// The login's name as the keychain shows it (its label, else its key name).
    public var loginName: String { loginLabel.trimmingCharacters(in: .whitespaces).isEmpty ? login : loginLabel }
}

/// What the wallet list shows about auto-pay (Android WalletList): the value of the limits row, and which notes go under it.
public enum WalletAutoSummary: Equatable, Sendable {
    case paused, none, cards(Int)

    public static func of(_ info: WalletInfo) -> WalletAutoSummary {
        if info.paused { return .paused }
        let n = info.autoCount
        return n == 0 ? .none : .cards(n)
    }

    /// "No card pays on its own yet": only when there are cards, none pays on its own, and auto-pay is not paused.
    public static func showsNoneNote(_ info: WalletInfo) -> Bool { !info.cards.isEmpty && info.autoCount == 0 && !info.paused }

    /// The first-site rule goes under the list once at least one card pays on its own.
    public static func showsFirstSiteRule(_ info: WalletInfo) -> Bool { info.autoCount > 0 }
}

// ---- nickname, default card, a card's own history (Android wallet/CardRules.kt CardMask.subtitle, DefaultCardRules, CardHistoryRules)

extension CardMask {
    /// The line under a card's nickname: its brand and last 4 ("Visa ••4242"); nil when the nickname already says exactly that (a card
    /// saved without a nickname is named so by the server). `brandWord` is the brand's display name.
    public static func subtitle(label: String, brandWord: String, last4 digits: String) -> String? {
        let line = "\(brandWord) \(dots)\(last4(digits))".trimmingCharacters(in: .whitespaces)
        return line.lowercased() == label.trimmingCharacters(in: .whitespaces).lowercased() ? nil : line
    }
}

/// The default card: the one the agent pays with unless the owner or the task names another (server wallet.py).
public enum DefaultCardRules {
    /// Deleting `card`: nil when nothing needs choosing (not the default, or at most one card left, which the server then makes the
    /// default); otherwise the cards the owner picks the new default from.
    public static func choicesOnDelete(_ card: WalletCard, _ cards: [WalletCard]) -> [WalletCard]? {
        guard card.isDefault else { return nil }
        let rest = cards.filter { $0.id != card.id }
        return rest.count >= 2 ? rest : nil
    }

    /// The first pick offered when choosing a new default: a card that can pay, else the first one.
    public static func suggested(_ choices: [WalletCard]) -> WalletCard? { choices.first(where: \.canPay) ?? choices.first }
}

/// Where a payment in a card's history leads.
public enum PaymentLink: Equatable, Sendable {
    case chat
    case task(Int64)
    case none
}

/// A card's own payment history, a page at a time (GET /api/wallet/cards/{id}/payments).
public enum CardHistoryRules {
    /// What is shown after one more page: newest first, each payment once.
    public static func merge(_ shown: [WalletPayment], _ page: WalletPayments) -> [WalletPayment] {
        let ids = Set(shown.map(\.id))
        return (shown + page.items.filter { !ids.contains($0.id) }).sorted { $0.id > $1.id }
    }

    /// Its chat ("chat"/"api" turns and the like), its task (a schedule run or a job), or nowhere.
    public static func link(_ p: WalletPayment) -> PaymentLink {
        if p.source.hasPrefix("schedule:") || p.source.hasPrefix("task:") || p.source == "background" {
            return p.taskId.map { .task($0) } ?? .none
        }
        if !p.source.isEmpty { return .chat }
        return p.taskId.map { .task($0) } ?? .none
    }
}

extension CardBrand {
    /// The brand's name as people write it (product names, the same in every language); nil for an unknown brand.
    public var displayName: String? {
        switch self {
        case .visa: "Visa"
        case .mastercard: "Mastercard"
        case .amex: "American Express"
        case .jcb: "JCB"
        case .discover: "Discover"
        case .unionpay: "UnionPay"
        case .diners: "Diners Club"
        case .other: nil
        }
    }

    /// The brand's short mark on a card face ("VISA", "MC", ...).
    public var shortName: String {
        switch self {
        case .visa: "VISA"
        case .mastercard: "MC"
        case .amex: "AMEX"
        case .jcb: "JCB"
        case .discover: "DISC"
        case .unionpay: "UP"
        case .diners: "DC"
        case .other: "•••"
        }
    }
}

extension CardBrief {
}
