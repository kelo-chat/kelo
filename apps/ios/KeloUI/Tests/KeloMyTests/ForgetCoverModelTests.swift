import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private let topics = #"{"items":[{"id":"a","label":"Old job","created_at":100,"removed":{"memory":3,"notes":0}},{"id":"b","label":"Trip","created_at":200,"removed":{}}]}"#

@MainActor @Suite struct ForgetModelTests {
    @Test func loadsNewestFirst() async {
        let t = RoutedTransport()
        t.on("GET /api/forget", topics)
        let m = ForgetModel(api: t.api())
        await m.load()
        #expect(m.items?.map(\.id) == ["b", "a"])
        #expect(!m.failed)
    }

    @Test func aFailureBeforeTheFirstLoadIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/forget", 500, "{}")
        let m = ForgetModel(api: t.api())
        await m.load()
        #expect(m.items == nil && m.failed)
    }

    @Test func allowAgainRemovesAtOnceAndSaysSo() async {
        let t = RoutedTransport()
        t.on("GET /api/forget", topics)
        t.on("DELETE /api/forget/a", "{}")
        let m = ForgetModel(api: t.api())
        await m.load()
        m.allowAgain(m.items!.first { $0.id == "a" }!)
        #expect(m.items?.map(\.id) == ["b"])
        await m.jobs.settled()
        #expect(m.notice != nil && m.busy.isEmpty)
        #expect(t.count("DELETE /api/forget/a") == 1)
    }

    @Test func aGoneTopicCountsAsDone() async {
        let t = RoutedTransport()
        t.on("GET /api/forget", topics)
        t.on("DELETE /api/forget/a", 404, #"{"detail":"gone"}"#)
        let m = ForgetModel(api: t.api())
        await m.load()
        m.allowAgain(m.items!.first { $0.id == "a" }!)
        await m.jobs.settled()
        #expect(m.items?.map(\.id) == ["b"])
    }

    @Test func anotherFailurePutsTheListBack() async {
        let t = RoutedTransport()
        t.on("GET /api/forget", topics)
        t.on("DELETE /api/forget/a", 500, #"{"detail":"x"}"#)
        let m = ForgetModel(api: t.api())
        await m.load()
        m.allowAgain(m.items!.first { $0.id == "a" }!)
        await m.jobs.settled()
        #expect(m.items?.count == 2)
    }
}

@MainActor @Suite struct PaymentCoverModelTests {
    private func routes(_ t: RoutedTransport, enabled: Bool = true) {
        t.on("GET /api/approvals/pending", "[]")
        t.on("GET /api/approvals/rules", #"{"items":[]}"#)
        t.on("GET /api/permissions/recipients", #"{"items":[]}"#)
        t.on("GET /api/approvals/history", #"{"items":[]}"#)
        t.on("GET /api/permissions/preapprovals", #"{"items":[{"id":"late","intent":"B","max_total":5,"currency":"usd","expires_at":900},{"id":"soon","intent":"A","max_total":9,"expires_at":100}]}"#)
        t.on("GET /api/permissions/pay-allows", #"{"items":[{"id":"w","schedule_name":"Weekly","merchant_origin":"https://shop.example/","payment_label":"Visa 4242"}]}"#)
        t.on("GET /api/permissions/payment-cover", enabled ? #"{"enabled":true}"# : #"{"enabled":false}"#)
    }

    @Test func loadsBothListsAndTheSwitch() async {
        let t = RoutedTransport()
        routes(t)
        let m = PermissionsModel(api: t.api())
        await m.load()
        #expect(m.cover?.orders.map(\.id) == ["soon", "late"])
        #expect(m.cover?.allows.first?.shop == "shop.example")
        #expect(m.cover?.enabled == true)
    }

    @Test func anOlderServerHidesTheSection() async {
        let t = RoutedTransport()
        routes(t)
        t.on("GET /api/permissions/preapprovals", 404, #"{"detail":"nope"}"#)
        let m = PermissionsModel(api: t.api())
        await m.load()
        #expect(m.cover == nil && !m.failed && m.pending != nil)
    }

    @Test func theSwitchIsSentAndTheServersAnswerWins() async {
        let t = RoutedTransport()
        routes(t)
        t.on("PUT /api/permissions/payment-cover", #"{"enabled":false}"#)
        let m = PermissionsModel(api: t.api())
        await m.load()
        m.setCover(false)
        #expect(m.cover?.enabled == false)
        await m.jobs.settled()
        #expect(m.cover?.enabled == false && t.count("PUT /api/permissions/payment-cover") == 1)
    }

    @Test func aFailedSwitchGoesBack() async {
        let t = RoutedTransport()
        routes(t)
        t.on("PUT /api/permissions/payment-cover", 500, #"{"detail":"x"}"#)
        let m = PermissionsModel(api: t.api())
        await m.load()
        m.setCover(false)
        await m.jobs.settled()
        #expect(m.cover?.enabled == true && m.notice != nil)
    }

    @Test func removingAnOrderAndAnAllowTakesThemOffTheList() async {
        let t = RoutedTransport()
        routes(t)
        t.on("DELETE /api/permissions/preapprovals/soon", "{}")
        t.on("DELETE /api/permissions/pay-allows/w", 404, #"{"detail":"gone"}"#)
        let m = PermissionsModel(api: t.api())
        await m.load()
        m.removeOrder(m.cover!.orders[0])
        m.removeAllow(m.cover!.allows[0])
        #expect(m.cover?.orders.map(\.id) == ["late"] && m.cover?.allows.isEmpty == true)
        await m.jobs.settled()
        #expect(t.count("DELETE /api/permissions/preapprovals/soon") == 1)
        #expect(m.cover?.allows.isEmpty == true)
    }

    @Test func aFailedRemoveReloads() async {
        let t = RoutedTransport()
        routes(t)
        t.on("DELETE /api/permissions/preapprovals/soon", 500, #"{"detail":"x"}"#)
        let m = PermissionsModel(api: t.api())
        await m.load()
        m.removeOrder(m.cover!.orders[0])
        await m.jobs.settled()
        #expect(m.cover?.orders.count == 2 && m.notice != nil)
    }
}
