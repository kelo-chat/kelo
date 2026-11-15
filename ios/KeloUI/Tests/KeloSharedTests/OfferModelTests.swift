import Foundation
import Testing
import KeloCore
import KeloTestSupport
@testable import KeloShared

@MainActor @Suite struct OfferModelTests {
    @Test func decodeOffersJson() throws {
        let json = """
        {
            "items": [
                {
                    "id": "off1",
                    "kind": "routine",
                    "sentence": "Check monthly subscription",
                    "needs": ["card", "login"],
                    "created_at": 1700000000.0,
                    "has_numbers": true,
                    "checked_at": 1699990000.0
                }
            ]
        }
        """
        let list = try JSONDecoder().decode(OfferList.self, from: Data(json.utf8))
        #expect(list.items.count == 1)
        let item = list.items[0]
        #expect(item.id == "off1")
        #expect(item.kind == "routine")
        #expect(item.sentence == "Check monthly subscription")
        #expect(item.needs == ["card", "login"])
        #expect(item.createdAt == 1700000000.0)
        #expect(item.hasNumbers == true)
        #expect(item.checkedAt == 1699990000.0)
    }

    @Test func offerStartFor409BusyAndFailed() async {
        let t = RoutedTransport()
        // 409 without code -> busy
        t.on("POST /api/offers/o1/start", 409, #"{"detail":"a reply is already running"}"#)
        let r1 = await OfferCalls.start(api: t.api(), id: "o1")
        #expect(r1 == .busy)

        // 409 with code -> failed
        t.on("POST /api/offers/o2/start", 409, #"{"code":"secret_in_message","detail":"secret"}"#)
        let r2 = await OfferCalls.start(api: t.api(), id: "o2")
        #expect(r2 == .failed)

        // 409 with code in detail object -> failed
        t.on("POST /api/offers/o3/start", 409, #"{"detail":{"code":"secret_in_message","message":"secret"}}"#)
        let r3 = await OfferCalls.start(api: t.api(), id: "o3")
        #expect(r3 == .failed)

        // 200 with turn_id -> started
        t.on("POST /api/offers/o4/start", #"{"turn_id":"t4"}"#)
        let r4 = await OfferCalls.start(api: t.api(), id: "o4")
        #expect(r4 == .started("t4"))
    }

    @Test func dismissRemovesOfferAtOnce() async {
        let t = RoutedTransport()
        t.on("POST /api/offers/o1/dismiss", #"{"ok":true}"#)
        let m = OffersModel(api: { t.api() })
        let o1 = Offer(id: "o1", sentence: "Task 1")
        let o2 = Offer(id: "o2", sentence: "Task 2")
        m.setItems([o1, o2])
        #expect(m.items.count == 2)

        m.dismiss(o1, reason: .doneAlready)
        // removed at once (optimistic removal)
        #expect(m.items.count == 1)
        #expect(m.items.first?.id == "o2")

        await m.jobs.settled()
        #expect(m.items.count == 1)
    }
}
