import Testing
import Foundation
import KeloCore
@testable import KeloMy

// Android my/ProactiveDeliveriesTest.kt: the delivery count and the held items.
@Suite struct ProactiveDeliveriesTests {
    func settings(_ json: String) throws -> ProactiveSettings { try JSON.decode(ProactiveSettings.self, from: try JSON.parse(json)) }
    func held(_ json: String) throws -> HeldList { try JSON.decode(HeldList.self, from: try JSON.parse(json)) }

    @Test func readsTheDeliveryFieldsWhenTheServerSendsThem() throws {
        let p = try settings(#"{"enabled": true, "daily_deliveries": 3, "today_deliveries": 1, "held": 4}"#)
        #expect(p.dailyDeliveries == 3 && p.todayDeliveries == 1 && p.held == 4)
        #expect(DeliveryRules.hasCount(p) && DeliveryRules.hasHeld(p))
        // 0 is a real choice (only what cannot wait), not "missing"
        let zero = try settings(#"{"daily_deliveries": 0, "today_deliveries": 0, "held": 0}"#)
        #expect(DeliveryRules.hasCount(zero) && DeliveryRules.hasHeld(zero))
        #expect(zero.dailyDeliveries == 0)
    }

    @Test func anOlderServerWithoutTheFieldsHidesTheRows() throws {
        let p = try settings(#"{"enabled": true, "daily_total": 3}"#)
        #expect(p.dailyDeliveries == nil && p.todayDeliveries == nil && p.held == nil)
        #expect(!DeliveryRules.hasCount(p) && !DeliveryRules.hasHeld(p))
        // a null sent explicitly is the same as absent
        #expect(!DeliveryRules.hasCount(try settings(#"{"daily_deliveries": null}"#)))
        // the other settings are read as before
        #expect(p.enabled && p.dailyTotal == 3)
    }

    @Test func theChoiceStaysInsideZeroToTen() {
        #expect(DeliveryRules.clamp(-1) == 0 && DeliveryRules.clamp(0) == 0)
        #expect(DeliveryRules.clamp(10) == 10 && DeliveryRules.clamp(11) == 10)
        #expect(DeliveryRules.clamp(7) == 7)
        #expect(DeliveryRules.min == 0 && DeliveryRules.max == 10)
    }

    @Test func whyCodesMapToTheirReasonAndAnUnknownOneToTheGeneralText() {
        #expect(DeliveryRules.why("slots") == .slots)
        #expect(DeliveryRules.why("over_cap") == .overCap)
        #expect(DeliveryRules.why("waiting_for_reply") == .waitingForReply)
        #expect(DeliveryRules.why("") == .other)
        #expect(DeliveryRules.why("quiet_hours_or_something_new") == .other)
    }

    @Test func readsHeldItemsAndSurvivesMissingAndExtraFields() throws {
        let list = try held("""
        {"items": [{"key": "mail:1", "source": "mail", "title": "Invoice due", "urgency": "오늘", "message": "The invoice is due today.",
                    "why": "slots", "held_at": 1762650000.5, "expires_at": 1762680000.0, "future_field": 1}, {"key": "x"}]}
        """)
        #expect(list.items.count == 2)
        #expect(list.items[0].title == "Invoice due" && list.items[0].expiresAt == 1762680000.0 && list.items[0].why == "slots")
        #expect(list.items[1].title == "" && list.items[1].urgency == nil && list.items[1].expiresAt == 0)
        #expect(try held("{}").items.isEmpty)
    }

    @Test func theHeadingIsTheTitleElseTheMessagesFirstLineAndThePreviewNeverRepeatsIt() {
        let item = HeldItem(title: "Invoice due", message: "The invoice is due today.\n\n\nPay it before 5.")
        #expect(DeliveryRules.heading(item) == "Invoice due")
        #expect(DeliveryRules.preview(item) == "The invoice is due today.\nPay it before 5.")
        let untitled = HeldItem(message: "  Only a line  ")
        #expect(DeliveryRules.heading(untitled) == "Only a line")
        #expect(DeliveryRules.preview(untitled) == "")
        #expect(DeliveryRules.heading(HeldItem()) == "")
    }

    @Test func aDeadlineOnTheSameLocalDayIsShownAsATimeOnly() throws {
        let zone = try #require(TimeZone(identifier: "Asia/Seoul"))
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = zone
        let noon = try #require(cal.date(from: DateComponents(year: 2026, month: 11, day: 16, hour: 12))).timeIntervalSince1970
        #expect(DeliveryRules.sameDay(noon + 6 * 3600, now: noon, zone: zone))
        #expect(!DeliveryRules.sameDay(noon + 24 * 3600, now: noon, zone: zone))
    }

    @Test func theHeldPageHasItsOwnRoute() {
        #expect(MyRoute.proactiveHeld == .proactiveHeld)
        #expect(MyRoute.proactiveHeld != .notify)
    }
}
