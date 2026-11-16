import Testing
@testable import KeloCore

/// Port of android wallet/CardRulesTest.kt.
@Suite struct CardRulesTests {
    @Test func digitsDropSpacesAndDashesAndStopAt19() {
        #expect(CardNumber.digits("4242 4242-4242 4242") == "4242424242424242")
        #expect(CardNumber.digits(String(repeating: "1", count: 30)).count == 19)
        #expect(CardNumber.digits("abc") == "")
    }

    @Test func luhnMatchesTheServer() {
        #expect(CardNumber.luhn("4242424242424242"))
        #expect(CardNumber.luhn("5555555555554444"))
        #expect(CardNumber.luhn("378282246310005"))
        #expect(!CardNumber.luhn("4242424242424241"))
        #expect(!CardNumber.luhn(""))
        #expect(!CardNumber.luhn("4242a"))
        #expect(CardNumber.valid("4242 4242 4242 4242"))
        #expect(!CardNumber.valid("42424242424"))   // 11 digits
    }

    @Test func brandDetectionFollowsWalletPy() {
        #expect(CardNumber.brand("4242424242424242") == .visa)
        #expect(CardNumber.brand("4") == .visa)
        #expect(CardNumber.brand("5555555555554444") == .mastercard)
        #expect(CardNumber.brand("2221000000000009") == .mastercard)
        #expect(CardNumber.brand("378282246310005") == .amex)
        #expect(CardNumber.brand("34") == .amex)
        #expect(CardNumber.brand("3530111333300000") == .jcb)
        #expect(CardNumber.brand("6011111111111117") == .discover)
        #expect(CardNumber.brand("6445000000000000") == .discover)
        #expect(CardNumber.brand("6200000000000005") == .unionpay)
        #expect(CardNumber.brand("30569309025904") == .diners)
        #expect(CardNumber.brand("36") == .diners)
        #expect(CardNumber.brand("9999") == .other)
        #expect(CardNumber.brand("") == .other)
        #expect(CardBrand.of("MasterCard") == .mastercard)
        #expect(CardBrand.of(nil) == .other)
    }

    @Test func groupingIsFourFourFourFourAndAmexFourSixFive() {
        #expect(CardNumber.group("4242424242424242") == "4242 4242 4242 4242")
        #expect(CardNumber.group("5555555555554444") == "5555 5555 5555 4444")
        #expect(CardNumber.group("424242") == "4242 42")
        #expect(CardNumber.group("4242") == "4242")
        #expect(CardNumber.group("378282246310005") == "3782 822463 10005")
        #expect(CardNumber.group("30569309025904") == "3056 930902 5904")
        #expect(CardNumber.group("4242424242424242424") == "4242 4242 4242 4242 424")
        #expect(CardNumber.group("") == "")
    }

    @Test func lengthsAndCvc() {
        #expect(CardNumber.maxLength(.amex) == 15)
        #expect(CardNumber.maxLength(.visa) == 19)
        #expect(CardNumber.last4("4242 4242 4242 4242") == "4242")
        #expect(Cvc.clean("12a34", brand: .visa) == "123")
        #expect(Cvc.clean("12345", brand: .amex) == "1234")
        #expect(Cvc.valid("123", brand: .mastercard))
        #expect(!Cvc.valid("1234", brand: .visa))
        #expect(Cvc.valid("1234", brand: .amex))
        #expect(!Cvc.valid("12", brand: .visa))
    }

    private let now = YearMonth(year: 2026, month: 11)

    @Test func expiryInputIsCleanedAndFormatted() {
        #expect(Expiry.clean("4") == "04")
        #expect(Expiry.clean("1") == "1")
        #expect(Expiry.clean("04/28") == "0428")
        #expect(Expiry.clean("12/299") == "1229")
        #expect(Expiry.format("0428") == "04/28")
        #expect(Expiry.format("042") == "04/2")
        #expect(Expiry.format("04") == "04")
        #expect(Expiry.display(month: 4, year: 2028) == "04/28")
    }

    @Test func expiryIsGoodThroughItsMonth() {
        #expect(Expiry.check("1126", now: now) == .ok(month: 11, year: 2026))
        #expect(Expiry.check("0428", now: now) == .ok(month: 4, year: 2028))
        #expect(Expiry.check("1026", now: now) == .expired)
        #expect(Expiry.check("1225", now: now) == .expired)
        #expect(Expiry.check("1328", now: now) == .badMonth)
        #expect(Expiry.check("0028", now: now) == .badMonth)
        #expect(Expiry.check("042", now: now) == .incomplete)
        #expect(Expiry.check("0151", now: now) == .ok(month: 1, year: 2051))
        #expect(Expiry.check("0152", now: now) == .tooFar)
    }

    @Test func maskKeepsOnlyTheLastFour() {
        #expect(CardMask.last4("••4242") == "4242")
        #expect(CardMask.last4(nil) == "")
    }
}

/// Port of android wallet/CardFaceTest.kt. The palette is ARGB numbers here instead of Compose colours. The two AddCardSteps tests
/// (the stepped add form) have no Swift counterpart yet and are left out.
@Suite struct CardFaceTests {
    @Test func everyBrandHasItsOwnFace() {
        let faces = CardBrand.allCases.map { CardFaceRules.palette($0) }
        #expect(Set(faces.map { "\($0.0)-\($0.1)" }).count == faces.count)
        for (a, b) in faces { #expect(a != b) }   // a gradient, not a flat colour
    }

    @Test func lastFourOnTheFace() {
        #expect(CardFaceRules.lastFour("4242") == "•• 4242")
        #expect(CardFaceRules.lastFour("x1234") == "•• 1234")
        #expect(CardFaceRules.lastFour("") == "•• ••••")
    }

    @Test func thePreviewShowsWhatIsTypedAndHidesTheMiddle() {
        #expect(CardFaceRules.preview("") == "•••• •••• •••• ••••")
        #expect(CardFaceRules.preview("42") == "42•• •••• •••• ••••")
        #expect(CardFaceRules.preview("4242424242") == "4242 4242 42•• ••••")
        // long enough: only the first six and the last four in full
        #expect(CardFaceRules.preview("4242424242424242") == "4242 42•• •••• 4242")
        // American Express groups 4-6-5 over 15 digits
        #expect(CardFaceRules.preview("378282246310005") == "3782 82•••• •0005")
    }
}
