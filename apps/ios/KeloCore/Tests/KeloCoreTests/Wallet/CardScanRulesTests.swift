import Testing
@testable import KeloCore

/// Android wallet/CardScanRulesTest.kt (CardScanTextTest, CardScanAgreementTest), case for case.
@Suite struct CardScanTextTests {
    @Test func readsANumberWrittenTogetherSpacedOrDashed() {
        #expect(CardScanText.number("4242424242424242") == "4242424242424242")
        #expect(CardScanText.number("4242 4242 4242 4242") == "4242424242424242")
        #expect(CardScanText.number("4242-4242-4242-4242") == "4242424242424242")
        #expect(CardScanText.number("CARD NO. 5555 5555 5555 4444 ") == "5555555555554444")
    }

    @Test func readsTheGroupingsOfOtherBrands() {
        #expect(CardScanText.number("3782 822463 10005") == "378282246310005")
        #expect(CardScanText.number("3056 930902 5904") == "30569309025904")
        #expect(CardScanText.number("3530 1113 3330 0000") == "3530111333300000")
        #expect(CardScanText.number("6011 1111 1111 1117") == "6011111111111117")
        #expect(CardScanText.number("4222222222222") == "4222222222222")
    }

    @Test func fixesLetterLookalikesOnlyBetweenDigits() {
        #expect(CardScanText.number("4242 4242 4Z42 4242") == "4242424242424242")
        #expect(CardScanText.number("4O12 8888 8888 1881") == "4012888888881881")
        #expect(CardScanText.number("4012 8888 8888 l88l") == "4012888888881881")
        #expect(CardScanText.number("BOSS BANK") == nil)
    }

    @Test func rejectsNumbersThatFailTheCheckDigit() {
        #expect(CardScanText.number("4242 4242 4242 4241") == nil)
        #expect(!CardScanText.plausible("1234567812345678"))
    }

    @Test func rejectsDigitRunsThatAreNotCardNumbers() {
        #expect(CardScanText.number("TEL 1588-1688") == nil)
        #expect(CardScanText.number("424242424242") == nil)
        #expect(CardScanText.number("42424242424242424242") == nil)
        #expect(CardScanText.number("4242 4242 4242 4242 4242") == nil)
        #expect(CardScanText.number("0000 0000 0000 0000") == nil)
        #expect(CardScanText.number("1111111111111111") == nil)
        #expect(CardScanText.number("0424242424242424") == nil)
        #expect(CardScanText.number("1 2 3 4 5 6 7 8 1 2 3 4 5 6 7 8") == nil)
        #expect(CardScanText.number("04/28 12/30") == nil)
        #expect(!CardScanText.plausible("37828224631000"))
        #expect(!CardScanText.plausible("55555555555544449"))
    }

    @Test func readsExpiryFormats() {
        #expect(CardScanText.expiries("VALID THRU 04/28") == ["0428"])
        #expect(CardScanText.expiries("04-28") == ["0428"])
        #expect(CardScanText.expiries("04 / 2028") == ["0428"])
        #expect(CardScanText.expiries("EXP 12.30") == ["1230"])
        #expect(CardScanText.expiries("FROM 01/27 THRU 01/32") == ["0127", "0132"])
    }

    @Test func ignoresWhatIsNotAnExpiry() {
        #expect(CardScanText.expiries("13/28").isEmpty)
        #expect(CardScanText.expiries("00/28").isEmpty)
        #expect(CardScanText.expiries("01/02/2028").isEmpty)
        #expect(CardScanText.expiries("4242-1242-4242").isEmpty)
        #expect(CardScanText.expiries("4242 1242").isEmpty)
    }

    @Test func picksTheLaterDate() {
        #expect(CardScanText.latest(["0127", "0132"]) == "0132")
        #expect(CardScanText.latest(["1229", "0129"]) == "1229")
        #expect(CardScanText.latest([String]()) == nil)
    }

    @Test func joinsGroupsSplitOverLines() {
        let r = CardScanText.read(["KELO BANK", "4242 4242", "4242 4242", "VALID THRU 04/28", "HONG GILDONG"])
        #expect(r.number == "4242424242424242")
        #expect(r.expiries == ["0428"])
    }

    @Test func aReadNeverPrintsTheNumber() {
        #expect(!String(describing: CardScanResult(number: "4242424242424242", expiry: "0428")).contains("4242"))
    }
}

@Suite struct CardScanAgreementTests {
    let now = YearMonth(year: 2026, month: 11)
    func frame(_ number: String?, _ dates: String...) -> CardScanRead { CardScanRead(number: number, expiries: dates) }

    @Test func needsSeveralFramesToAgree() {
        let a = CardScanAgreement { now }
        #expect(a.offer(frame("4242424242424242", "0428")) == nil)
        #expect(a.offer(frame("4242424242424242", "0428")) == nil)
        let r = a.offer(frame("4242424242424242", "0428"))
        #expect(r?.number == "4242424242424242")
        #expect(r?.expiry == "0428")
    }

    @Test func aMisreadFrameDoesNotWin() {
        let a = CardScanAgreement { now }
        _ = a.offer(frame("4242424242424242"))
        _ = a.offer(frame("4012888888881881"))
        _ = a.offer(frame("4242424242424242"))
        #expect(a.offer(frame("4012888888881881", "0428")) == nil)
        #expect(a.offer(frame("4242424242424242", "0428"))?.number == "4242424242424242")
    }

    @Test func waitsAWhileForTheDateThenEndsWithoutIt() {
        let a = CardScanAgreement { now }
        for _ in 0..<(CardScanAgreement.numberFrames - 1) { #expect(a.offer(frame("4242424242424242")) == nil) }
        var result: CardScanResult?
        var frames = 0
        while result == nil && frames < 50 { result = a.offer(frame("4242424242424242")); frames += 1 }
        #expect(result?.number == "4242424242424242")
        #expect(result?.expiry == nil)
        #expect(frames == CardScanAgreement.expiryGrace + 1)
    }

    @Test func skipsAPastValidFromDate() {
        let a = CardScanAgreement { now }
        var r: CardScanResult?
        for _ in 0..<3 { r = a.offer(frame("4242424242424242", "0124", "0129")) }
        #expect(r?.expiry == "0129")
    }

    @Test func clearForgetsEverything() {
        let a = CardScanAgreement { now }
        _ = a.offer(frame("4242424242424242", "0428"))
        _ = a.offer(frame("4242424242424242", "0428"))
        a.clear()
        #expect(a.offer(frame("4242424242424242", "0428")) == nil)
    }
}
