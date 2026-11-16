import Testing
import Foundation
import KeloCore
@testable import KeloMy

/// The wallet rules ported from Android (Money, LimitRules, AddCardSteps, the card forms' checks, WalletView back, WalletPlaces).
@Suite struct WalletParityTests {
    @Test func moneyDecimalsParseAndPlain() {
        #expect(Money.decimals("KRW") == 0)
        #expect(Money.decimals("jpy") == 0)
        #expect(Money.decimals("USD") == 2)
        #expect(Money.parse("1,000", "KRW") == 1000)
        #expect(Money.parse("12.5", "USD") == 12.5)
        #expect(Money.parse("12.5", "KRW") == nil)
        #expect(Money.parse("", "USD") == nil)
        #expect(Money.parse("-3", "USD") == nil)
        #expect(Money.plain(30000, "KRW") == "30000")
        #expect(Money.plain(25, "USD") == "25")
        #expect(Money.plain(12.5, "USD") == "12.50")
        #expect(Money.number(30000, "KRW", locale: Locale(identifier: "en_US")) == "30,000")
        #expect(Money.number(25, "USD", locale: Locale(identifier: "en_US")) == "25.00")
        #expect(Money.validCode("USD"))
        #expect(!Money.validCode("us"))
    }

    @Test func limitRules() {
        let rows = [
            WalletLimit(currency: "KRW", perPayment: 30000, monthly: 100000, defaultPerPayment: 30000, defaultMonthly: 100000),
            WalletLimit(currency: "USD", perPayment: 20, monthly: 100, defaultPerPayment: 20, defaultMonthly: 100, spentThisMonth: 5),
            WalletLimit(currency: "EUR", perPayment: 0, monthly: 0, defaultPerPayment: 20, defaultMonthly: 100, custom: true),
            WalletLimit(currency: "JPY", defaultPerPayment: 3000, defaultMonthly: 10000),
        ]
        let shown = LimitRules.visible(rows, homes: ["KRW"])
        #expect(shown.map(\.currency) == ["KRW", "USD", "EUR"])
        #expect(LimitRules.addable(rows, shown: shown) == ["JPY"])
        #expect(LimitRules.visible(rows, homes: [], added: ["JPY"]).map(\.currency) == ["USD", "EUR", "JPY"])
        #expect(LimitRules.check(10, 100) == .ok)
        #expect(LimitRules.check(200, 100) == .perAboveMonthly)
        #expect(LimitRules.check(nil, 100) == .missing)
        #expect(LimitRules.used(50, 100) == 0.5)
        #expect(LimitRules.used(5, 0) == 1)
        #expect(WalletLimit(currency: "THB").added)
        #expect(!rows[0].added)
        #expect(LimitRules.homeCurrency(Locale(identifier: "ko")) == "KRW")
        #expect(LimitRules.homeCurrency(Locale(identifier: "en_GB")) == "GBP")
    }

    @Test func addCardSteps() {
        let now = YearMonth(year: 2026, month: 11)
        let good = "4242424242424242"
        #expect(AddCardSteps.next(.number) == .dates)
        #expect(AddCardSteps.next(.confirm) == .confirm)
        #expect(AddCardSteps.prev(.number) == nil)
        #expect(AddCardSteps.position(.owner) == (3, 4))
        #expect(!AddCardSteps.canLeave(.number, number: "4242", expiry: "", cvc: "", agreed: false, now: now))
        #expect(AddCardSteps.canLeave(.number, number: good, expiry: "", cvc: "", agreed: false, now: now))
        #expect(!AddCardSteps.canLeave(.dates, number: good, expiry: "1025", cvc: "123", agreed: false, now: now))   // expired
        #expect(AddCardSteps.canLeave(.dates, number: good, expiry: "1229", cvc: "123", agreed: false, now: now))
        #expect(AddCardSteps.canLeave(.owner, number: "", expiry: "", cvc: "", agreed: false, now: now))
        #expect(!AddCardSteps.canLeave(.confirm, number: good, expiry: "1229", cvc: "123", agreed: false, now: now))
        #expect(AddCardSteps.canLeave(.confirm, number: good, expiry: "1229", cvc: "123", agreed: true, now: now))
        #expect(AddCardSteps.firstBlocking(number: good, expiry: "13", cvc: "1", now: now) == .dates)
        #expect(AddCardSteps.firstBlocking(number: good, expiry: "1229", cvc: "123", now: now) == nil)
    }

    @Test func formChecks() {
        #expect(WalletFormRules.numberProblem("4242", mustEnd: nil) == nil)          // not complete yet
        #expect(WalletFormRules.numberProblem("4242424242424241", mustEnd: nil) == .invalid)
        #expect(WalletFormRules.numberProblem("4242424242424242", mustEnd: "1111") == .otherCard("1111"))
        #expect(WalletFormRules.numberProblem("4242424242424242", mustEnd: "4242") == nil)
        #expect(WalletFormRules.nameHasDigits("Card 4242 4242 4242 4242"))
        #expect(!WalletFormRules.nameHasDigits("Everyday card 2026"))
        let card = try! KeloApi.decode(WalletCard.self, ["id": "c1", "brand": "visa", "last4": "4242"])
        #expect(WalletFormRules.reenterReady(number: "4242424242424242", cvc: "123", card: card))
        #expect(!WalletFormRules.reenterReady(number: "4000056655665556", cvc: "123", card: card))
    }

    @Test func viewBack() {
        #expect(WalletView.list.back == nil)
        #expect(WalletView.card("a").back == .list)
        #expect(WalletView.reenter("a", autoPay: true).back == .card("a"))
        #expect(WalletView.limits.back == .list)
    }

    @MainActor @Test func placesKeepTypingUntilCleared() {
        WalletPlaces.clear()
        #expect(!WalletPlaces.hasTyping())
        var d = AddCardDraft()
        d.number = "4242"
        WalletPlaces.saveAdd(d)
        WalletPlaces.go(.add)
        #expect(WalletPlaces.hasTyping())
        #expect(WalletPlaces.currentView() == .add)
        #expect(WalletPlaces.addDraft()?.number == "4242")
        #expect(WalletPlaces.reenterDraft("x", autoPay: false) == nil)
        WalletPlaces.clear()
        #expect(WalletPlaces.addDraft() == nil)
        #expect(WalletPlaces.currentView() == .list)
    }
}
