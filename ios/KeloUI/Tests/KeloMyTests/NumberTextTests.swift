import Testing
import Foundation
@testable import KeloMy

// Android my/NumberTextTest.kt, same cases: sizes and money on 마이 → 사용량 / Kelo 서버 자원 round and read as on the phone.
@Suite struct NumberTextTests {
    let en = Locale(identifier: "en_US")
    let de = Locale(identifier: "de_DE")

    @Test func oneDecimalRoundsHalfUpOnTheShortestForm() {
        #expect(MyFormat.oneDecimal(1.25, locale: en) == "1.3")
        #expect(MyFormat.oneDecimal(2.25, locale: en) == "2.3")   // half even would say 2.2
        #expect(MyFormat.oneDecimal(0.15, locale: en) == "0.2")   // 0.1499999… in binary; its shortest form is 0.15
        #expect(MyFormat.oneDecimal(1.0, locale: en) == "1.0")
        #expect(MyFormat.oneDecimal(123_456.75, locale: en) == "123456.8")   // no grouping, as "%.1f"
        #expect(MyFormat.oneDecimal(1.25, locale: de) == "1,3")
    }

    @Test func storageSizes() {
        let mib: Int64 = 1024 * 1024, gib = 1024 * mib
        #expect(MyFormat.storageParts(gib + gib / 4, locale: en) == ("1.3", "gb"))
        #expect(MyFormat.storageParts(2 * gib + gib / 4, locale: en) == ("2.3", "gb"))
        #expect(MyFormat.storageParts(gib + gib / 4, locale: de) == ("1,3", "gb"))
        #expect(MyFormat.storageParts(5 * mib + mib / 4, locale: en) == ("5.3", "mb"))
        #expect(MyFormat.storageParts(10 * mib + mib / 2, locale: en) == ("11", "mb"))
        #expect(MyFormat.storageParts(1536, locale: en) == ("2", "kb"))
        #expect(MyFormat.storageParts(0, locale: en) == ("0", "kb"))
    }

    @Test func fileSizes() {
        let mib: Int64 = 1024 * 1024
        #expect(MyFormat.fileParts(mib + mib / 4, locale: en) == ("1.3", "mb"))
        #expect(MyFormat.fileParts(1536, locale: en) == ("2", "kb"))
        #expect(MyFormat.fileParts(100, locale: en) == ("1", "kb"))
    }

    @Test func cpusAndWholeNumbers() {
        #expect(MyFormat.cpuCount(2) == "2")
        #expect(MyFormat.cpuCount(1.5) == "1.5")
        #expect(MyFormat.cpuCount(0.25) == "0.25")
        #expect(MyFormat.number(1_234_567, locale: en) == "1,234,567")
        #expect(MyFormat.number(1_234_567, locale: de) == "1.234.567")
    }

    @Test func moneyIsTwoDecimalsHalfEven() {
        #expect(MyFormat.money(1234.5, currency: "USD", locale: en) == "$1,234.50")
        #expect(MyFormat.money(0.125, currency: "USD", locale: en) == "$0.12")
        #expect(MyFormat.money(0.375, currency: "USD", locale: en) == "$0.38")
        #expect(MyFormat.money(-0.19, currency: "USD", locale: en) == "-$0.19")
        #expect(MyFormat.money(0, currency: "", locale: en) == "$0.00")
        let euros = MyFormat.money(1234.5, currency: "EUR", locale: de)
        #expect(euros.hasPrefix("1.234,50") && euros.hasSuffix("€"))
    }
}
