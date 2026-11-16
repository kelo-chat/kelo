import Testing
import Foundation
import KeloCore
import KeloTestSupport
import KeloShared
@testable import KeloMy

/// The card row's line and the card page's reason (Android WalletScreens autoLine / reasonText), and the grants' amounts.
@MainActor @Suite(.serialized) struct WalletAutoLineTests {
    private func card(autoPay: Bool, pays: Bool?, reason: String = "", usable: Bool = true, expired: Bool = false) -> WalletCard {
        WalletCard(id: "c", autoPay: autoPay, usable: usable, expired: expired, paysAutomatically: pays, askReason: reason)
    }

    @Test func eachReasonHasItsOwnWords() {
        withLanguage("en") {
        #expect(walletReasonText(WalletCard.reasonNotAuto) == L(R.string.wallet_reason_not_auto))
        #expect(walletReasonText(WalletCard.reasonNoServerCopy) == L(R.string.wallet_reason_no_server_copy))
        #expect(walletReasonText(WalletCard.reasonPaused) == L(R.string.wallet_reason_paused))
        #expect(walletReasonText(WalletCard.reasonVaultLocked) == L(R.string.wallet_reason_vault_locked))
        #expect(walletReasonText("something_new") == L(R.string.wallet_reason_not_auto))   // a reason this app does not know yet
        let words = Set([WalletCard.reasonNotAuto, WalletCard.reasonNoServerCopy, WalletCard.reasonPaused, WalletCard.reasonVaultLocked].map(walletReasonText))
        #expect(words.count == 4 && !words.contains(""))
        }
    }

    @Test func theRowSaysWhetherItPaysOnItsOwnOrWhyItAsks() {
        withLanguage("en") {
        #expect(walletAutoLine(card(autoPay: true, pays: true)) == L(R.string.wallet_row_pays_auto))
        #expect(walletAutoLine(card(autoPay: false, pays: false, reason: "not_auto")) == L(R.string.wallet_reason_not_auto))
        #expect(walletAutoLine(card(autoPay: true, pays: false, reason: "paused")) == L(R.string.wallet_reason_paused))
        // an older server: the switch decides
        #expect(walletAutoLine(card(autoPay: false, pays: nil)) == L(R.string.wallet_reason_not_auto))
        // a card that cannot pay at all gets no line (its tags say why)
        #expect(walletAutoLine(card(autoPay: true, pays: true, expired: true)) == nil)
        #expect(walletAutoLine(card(autoPay: true, pays: true, usable: false)) == nil)
        }
    }

    @Test func onlyACardSavedToAskEachTimeOffersTurningAutoPayOn() {
        #expect(card(autoPay: false, pays: false, reason: "not_auto").offersTurnOn)
        #expect(!card(autoPay: true, pays: true).offersTurnOn)
        #expect(!card(autoPay: true, pays: false, reason: "paused").offersTurnOn)
        #expect(!card(autoPay: false, pays: false, expired: true).offersTurnOn)
        #expect(!card(autoPay: false, pays: false, usable: false).offersTurnOn)
    }

    @Test func grantAmountsUseTheWalletsWording() {
        withLanguage("en") {
            #expect(keloMoney(12900, "KRW") == L(R.string.wallet_money_krw, "12,900"))
            #expect(keloMoney(25, "usd") == L(R.string.wallet_money, "25.00", "USD"))
            #expect(walletMoney(25, "USD") == keloMoney(25, "USD"))
        }
    }
}
