import Testing
import Foundation
import KeloCore
@testable import KeloMy

// Android my/ProactiveDeliveriesTest.kt: the held items.
@Suite struct ProactiveDeliveriesTests {
    func settings(_ json: String) throws -> ProactiveSettings { try JSON.decode(ProactiveSettings.self, from: try JSON.parse(json)) }
    func held(_ json: String) throws -> HeldList { try JSON.decode(HeldList.self, from: try JSON.parse(json)) }

    @Test func readsTheHeldCountAndIgnoresTheRetiredFields() throws {
        let p = try settings(#"{"enabled": true, "daily_deliveries": 3, "today_deliveries": 1, "held": 4}"#)
        #expect(p.held == 4 && DeliveryRules.hasHeld(p) && DeliveryRules.heldCount(p.held) == 4)
        let zero = try settings(#"{"held": 0}"#)
        #expect(DeliveryRules.hasHeld(zero) && DeliveryRules.heldCount(zero.held) == nil)   // nothing held: the link is hidden
        #expect(DeliveryRules.heldCount(nil) == nil)
    }

    @Test func anOlderServerWithoutTheFieldsHidesTheRows() throws {
        let p = try settings(#"{"enabled": true, "daily_total": 3}"#)
        #expect(p.held == nil)
        #expect(!DeliveryRules.hasHeld(p))
        #expect(!DeliveryRules.hasHeld(try settings(#"{"held": null}"#)))
        // the other settings are read as before
        #expect(p.enabled && p.dailyTotal == 3)
    }

    @Test func whyCodesMapToTheirReasonAndAnUnknownOneToTheGeneralText() {
        #expect(DeliveryRules.why("slots") == .other)   // retired 2026-11-19: the general text
        #expect(DeliveryRules.why("over_cap") == .overCap)
        #expect(DeliveryRules.why("waiting_for_reply") == .waitingForReply)
        #expect(DeliveryRules.why("") == .other)
        #expect(DeliveryRules.why("quiet_hours_or_something_new") == .other)
    }

    @Test func readsHeldItemsAndSurvivesMissingAndExtraFields() throws {
        let list = try held("""
        {"items": [{"key": "mail:1", "source": "mail", "title": "Invoice due", "urgency": "오늘", "message": "The invoice is due today.",
                    "why": "over_cap", "held_at": 1762650000.5, "expires_at": 1762680000.0, "future_field": 1}, {"key": "x"}]}
        """)
        #expect(list.items.count == 2)
        #expect(list.items[0].title == "Invoice due" && list.items[0].expiresAt == 1762680000.0 && list.items[0].why == "over_cap")
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
