import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

@MainActor @Suite struct WalletModelTests {
    @Test func loadAllShowsCardsAndMarksPaymentsSeen() async {
        let t = RoutedTransport()
        t.on("GET /api/wallet", #"{"cards":[{"id":"c1"}]}"#)
        t.on("GET /api/wallet/shipping", #"{"profiles":[]}"#)
        t.on("GET /api/wallet/notifications", #"{"items":[{"id":4,"seen":false}],"unseen":1}"#)
        t.on("POST /api/wallet/notifications/seen", "{}")
        let m = WalletModel(api: t.api())
        await m.loadAll()
        #expect(m.info?.cards.count == 1)
        #expect(m.notices.count == 1)
        #expect(t.count("POST /api/wallet/notifications/seen") == 1)
    }

    @Test func aFailureIsShownAndACardChangeFailureToo() async {
        let t = RoutedTransport()
        t.on("GET /api/wallet", 500, #"{"detail":"down"}"#)
        let m = WalletModel(api: t.api())
        await m.load()
        #expect(!m.failed.isEmpty)
        t.on("PATCH /api/wallet/cards/c1", 400, #"{"detail":"no"}"#)
        var then = 0
        m.patchCard("c1", ["label": .string("x")], then: { then += 1 })
        await m.jobs.settled()
        #expect(!m.message.isEmpty)
        #expect(then == 0)
    }

    @Test func anOlderLoadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/wallet") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"cards":[]}"#) }
            return (200, #"{"cards":[{"id":"c1"}]}"#)
        }
        t.on("GET /api/wallet/shipping", #"{"profiles":[]}"#)
        let m = WalletModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/wallet") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.info?.cards.count == 1)
    }

    @Test func keyguardIsAskedThroughTheInjectedClosure() async {
        let t = RoutedTransport()
        let m = WalletModel(api: t.api(), keyguard: { true })
        var got: Bool??
        m.checkKeyguard(then: { got = .some($0) })
        await m.jobs.settled()
        #expect(got == .some(true))
    }
}

@MainActor @Suite struct WalletPartsModelTests {
    @Test func historyPages() async {
        let t = RoutedTransport()
        t.on("GET /api/wallet/cards/c1/payments", #"{"items":[{"id":1}],"next_before_id":1}"#)
        let m = WalletCardHistoryModel(api: t.api(), cardId: "c1")
        await m.load()
        #expect(m.items?.count == 1)
        #expect(m.next == 1)
    }

    @Test func historyFailureShowsAMessageAndAnEmptyList() async {
        let t = RoutedTransport()
        t.on("GET /api/wallet/cards/c1/payments", 500, #"{"detail":"x"}"#)
        let m = WalletCardHistoryModel(api: t.api(), cardId: "c1")
        await m.load()
        #expect(!m.message.isEmpty)
        #expect(m.items?.isEmpty == true)
    }

    @Test func historyOlderAnswerNeverWins() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/wallet/cards/c1/payments") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"items":[{"id":1}]}"#) }
            return (200, #"{"items":[{"id":2},{"id":1}]}"#)
        }
        let m = WalletCardHistoryModel(api: t.api(), cardId: "c1")
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/wallet/cards/c1/payments") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.items?.count == 2)
    }

    @Test func paymentsLoadAndFail() async {
        let t = RoutedTransport()
        t.on("GET /api/wallet/payments", #"{"items":[{"id":1},{"id":2}]}"#)
        let m = WalletPaymentsModel(api: t.api())
        await m.load()
        #expect(m.items?.count == 2)
        t.on("GET /api/wallet/payments", 500, #"{"detail":"x"}"#)
        await m.load()
        #expect(!m.message.isEmpty)
    }

    @Test func limitsLoadSaveAndFailure() async {
        let t = RoutedTransport()
        t.on("GET /api/wallet/limits", #"{"limits":[]}"#)
        t.on("PUT /api/wallet/limits/USD", #"{"currency":"USD"}"#)
        let m = WalletLimitsModel(api: t.api(), wallet: WalletModel(api: t.api()))
        await m.load()
        #expect(m.data != nil)
        var done = 0
        m.save("USD", 10, 100, done: { done += 1 })
        await m.jobs.settled()
        #expect(done == 1)
        t.on("PUT /api/wallet/limits/USD", 400, #"{"detail":"bad"}"#)
        m.save("USD", 10, 100, done: { done += 1 })
        await m.jobs.settled()
        #expect(done == 1)
        #expect(!m.message.isEmpty)
    }

    @Test func limitsOlderLoadNeverWins() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/wallet/limits") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"month":"old"}"#) }
            return (200, #"{"month":"new"}"#)
        }
        let m = WalletLimitsModel(api: t.api(), wallet: WalletModel(api: t.api()))
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/wallet/limits") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.data?.month == "new")
    }

    @Test func pausingTheWalletAndItsFailure() async {
        let t = RoutedTransport()
        t.on("PUT /api/wallet/pause", #"{"paused":true}"#)
        t.on("GET /api/wallet", "{}")
        let m = WalletLimitsModel(api: t.api(), wallet: WalletModel(api: t.api()))
        m.setPaused(true)
        await m.jobs.settled()
        #expect(m.isPaused == true)
        t.on("PUT /api/wallet/pause", 500, #"{"detail":"x"}"#)
        m.setPaused(false)
        await m.jobs.settled()
        #expect(!m.message.isEmpty)
    }

    @Test func shippingSaveAndFailure() async {
        let t = RoutedTransport()
        t.on("POST /api/wallet/shipping", #"{"id":"s1"}"#)
        let m = ShippingEditModel(api: t.api())
        var saved = 0
        m.save(["name": .string("a")], profile: nil, saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        t.on("POST /api/wallet/shipping", 422, #"{"detail":"bad"}"#)
        m.save(["name": .string("a")], profile: nil, saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        #expect(!m.message.isEmpty)
        #expect(!m.saving)
    }
}

@MainActor @Suite struct NotifyModelTests {
    @Test func loadReadsThreeSettingsAndAMissingOneIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/proactive", #"{"enabled":true}"#)
        t.on("GET /api/checklist", 404, #"{"detail":"none"}"#)
        t.on("GET /api/followups/settings", "{}")
        let m = NotifySettingsModel(api: t.api())
        await m.load()
        #expect(m.proactive?.enabled == true)
        #expect(m.checklistMissing)
        #expect(!m.followupsMissing)
    }

    @Test func aSaveFailureShowsANoticeAndKeepsTheOldValue() async {
        let t = RoutedTransport()
        t.on("GET /api/proactive", #"{"enabled":false}"#)
        t.on("PUT /api/proactive", 500, #"{"detail":"x"}"#)
        let m = NotifySettingsModel(api: t.api())
        await m.load()
        m.putProactive(["enabled": .bool(true)])
        await m.jobs.settled()
        #expect(m.notice != nil)
        #expect(m.proactive?.enabled == false)
    }

    @Test func theNewestSaveWinsWhateverOrderTheAnswersComeIn() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("PUT /api/proactive") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"enabled":false}"#) }
            return (200, #"{"enabled":true}"#)
        }
        let m = NotifySettingsModel(api: t.api())
        m.putProactive(["enabled": .bool(false)])
        _ = await eventually { t.count("PUT /api/proactive") == 1 }
        m.putProactive(["enabled": .bool(true)])
        _ = await eventually { m.proactive?.enabled == true }
        await gate.release()
        await m.jobs.settled()
        #expect(m.proactive?.enabled == true)
    }
}

@MainActor @Suite struct BriefingModelTests {
    @Test func loadAndMissing() async {
        let t = RoutedTransport()
        t.on("GET /api/briefing", #"{"enabled":true}"#)
        let m = BriefingSettingsModel(api: t.api())
        await m.load()
        #expect(m.settings?.enabled == true)
        let t2 = RoutedTransport()
        t2.on("GET /api/briefing", 404, "{}")
        let m2 = BriefingSettingsModel(api: t2.api())
        await m2.load()
        #expect(m2.missing)
    }

    @Test func sendNowSuccessAndFailure() async {
        let t = RoutedTransport()
        t.on("POST /api/briefing/run", "{}")
        let m = BriefingSettingsModel(api: t.api())
        m.sendNow()
        await m.jobs.settled()
        #expect(m.sent == true)
        t.on("POST /api/briefing/run", 500, #"{"detail":"x"}"#)
        m.sendNow()
        await m.jobs.settled()
        #expect(m.sent == nil)
        #expect(m.notice != nil)
    }

    @Test func aKeySaveClosesTheSheetAndAFailureShowsItsReason() async {
        let t = RoutedTransport()
        t.on("POST /api/briefing/keys/w", #"{"enabled":true}"#)
        let m = BriefingSettingsModel(api: t.api())
        m.saveKey("w", key: " k ")
        await m.jobs.settled()
        #expect(m.keySaved == 1)
        t.on("POST /api/briefing/keys/w", 400, #"{"detail":"bad"}"#)
        m.saveKey("w", key: "k")
        await m.jobs.settled()
        #expect(m.keySaved == 1)
        #expect(!m.keyError.isEmpty)
    }

    @Test func aPutAnswerOlderThanANewerOneIsDropped() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("PUT /api/briefing") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"enabled":false}"#) }
            return (200, #"{"enabled":true}"#)
        }
        let m = BriefingSettingsModel(api: t.api())
        m.put(["enabled": .bool(false)])
        _ = await eventually { t.count("PUT /api/briefing") == 1 }
        m.put(["enabled": .bool(true)])
        _ = await eventually { m.settings?.enabled == true }
        await gate.release()
        await m.jobs.settled()
        #expect(m.settings?.enabled == true)
    }

    @Test func sourcesLoadCheckAndFailure() async {
        let t = RoutedTransport()
        t.on("GET /api/briefing/sources", #"{"calendar":{},"mail":{}}"#)
        t.on("POST /api/briefing/sources/check", 500, #"{"detail":"x"}"#)
        let m = BriefingSourcesModel(api: t.api())
        await m.load()
        #expect(m.sources != nil)
        m.check()
        await m.jobs.settled()
        #expect(m.notice != nil)
        #expect(!m.checking)
    }

    @Test func sourcesLoadOlderAnswerNeverWins() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/briefing/sources") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"calendar":{"connected":false}}"#) }
            return (200, #"{"calendar":{"connected":true}}"#)
        }
        let m = BriefingSourcesModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/briefing/sources") == 1 }
        await m.load()
        let newer = m.sources
        await gate.release()
        await first.value
        #expect(m.sources == newer)
    }
}

@MainActor @Suite struct RecordsModelTests {
    @Test func lookbackLoadsAndFails() async {
        let t = RoutedTransport()
        t.on("GET /api/reflection", "{}")
        let m = RecordsModel(api: t.api())
        await m.loadLookback()
        #expect(m.look != nil)
        let t2 = RoutedTransport()
        let m2 = RecordsModel(api: t2.api())
        await m2.loadLookback()
        #expect(m2.lookFailed)
    }

    @Test func anOlderLookbackNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/reflection") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"enabled":false}"#) }
            return (200, #"{"enabled":true}"#)
        }
        let m = RecordsModel(api: t.api())
        let first = Task { @MainActor in await m.loadLookback() }
        _ = await eventually { t.count("GET /api/reflection") == 1 }
        await m.loadLookback()
        let newer = m.look
        await gate.release()
        await first.value
        #expect(m.look == newer)
    }

    @Test func aWeekFailureIsMarked() async {
        let t = RoutedTransport()
        t.on("GET /api/journal", 500, #"{"detail":"x"}"#)
        let m = RecordsModel(api: t.api())
        await m.loadWeek()
        #expect(m.weekFailed)
        #expect(m.entries == nil)
    }

    @Test func undoingTheMemoryPassAnd404() async {
        let t = RoutedTransport()
        t.on("POST /api/reflection/memory/undo", "{}")
        t.on("GET /api/reflection", "{}")
        let m = RecordsModel(api: t.api())
        m.undoMemory()
        await m.jobs.settled()
        #expect(m.memoryClosed == 1)
        t.on("POST /api/reflection/memory/undo", 404, #"{"detail":"gone"}"#)
        m.undoMemory()
        await m.jobs.settled()
        #expect(m.memoryClosed == 2)
        t.on("POST /api/reflection/memory/undo", 500, #"{"detail":"x"}"#)
        m.undoMemory()
        await m.jobs.settled()
        #expect(m.memoryClosed == 2)
        #expect(!m.undoing)
    }

    @Test func memoryOpenFailureIsMarked() async {
        let t = RoutedTransport()
        let m = RecordsModel(api: t.api())
        m.openMemory()
        await m.jobs.settled()
        #expect(m.memFailed)
    }
}
