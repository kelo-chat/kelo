import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

private let two = #"{"devices":[{"id":1,"name":"a"},{"id":2,"name":"b"}],"current":1}"#

@MainActor @Suite struct DevicesModelTests {

    @Test func loadListsTheDevices() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", two)
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: {})
        await m.load()
        #expect(m.devices.count == 2)
        #expect(m.current == 1)
    }

    @Test func removingAnotherDeviceReloads() async {
        let t = RoutedTransport()
        t.on("GET /api/devices") { _, n in (200, n == 0 ? two : #"{"devices":[{"id":1,"name":"a"}],"current":1}"#) }
        t.on("DELETE /api/devices/2", "{}")
        var gone = 0
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: { gone += 1 })
        await m.load()
        m.remove(m.devices[1])
        await m.jobs.settled()
        #expect(m.devices.count == 1)
        #expect(gone == 0)
    }

    @Test func removingThisPhoneSignsOut() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", two)
        t.on("DELETE /api/devices/1", "{}")
        var gone = 0
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: { gone += 1 })
        await m.load()
        m.remove(m.devices[0])
        await m.jobs.settled()
        #expect(gone == 1)
    }

    @Test func aRemovalFailureShowsTheError() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", two)
        t.on("DELETE /api/devices/2", 500, #"{"detail":"x"}"#)
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: {})
        await m.load()
        m.remove(m.devices[1])
        await m.jobs.settled()
        #expect(!m.error.isEmpty)
        #expect(m.devices.count == 2)
    }

    @Test func aListReadBeforeARemovalNeverBringsTheDeviceBack() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/devices") { _, n in
            if n == 0 { await gate.wait(); return (200, two) }
            return (200, #"{"devices":[{"id":1,"name":"a"}],"current":1}"#)
        }
        t.on("DELETE /api/devices/2", "{}")
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: {})
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/devices") == 1 }
        m.remove(Device.fixture(2))
        await m.jobs.settled()
        await gate.release()
        await first.value
        #expect(m.devices.map(\.id) == [1])
    }

    @Test func addingShowsTheLinkAndANoLinkAnswerIsAnError() async {
        let t = RoutedTransport()
        t.on("POST /api/devices/pair", #"{"code":"c","expires_in":60,"link":"https://kelo.test/#pair=c"}"#)
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: {})
        m.add()
        await m.jobs.settled()
        #expect(m.pairLink == "https://kelo.test/#pair=c")
        m.closePairing()
        #expect(m.pairLink == nil)
        t.on("POST /api/devices/pair", #"{"code":"c","expires_in":60}"#)
        m.add()
        await m.jobs.settled()
        #expect(m.pairLink == nil)
        #expect(!m.error.isEmpty)
    }

    @Test func followingThePairingStopsWhenItsTaskIsCancelled() async {
        let t = RoutedTransport()
        t.on("POST /api/devices/pair", #"{"code":"c","expires_in":60,"link":"https://kelo.test/#pair=c"}"#)
        t.on("GET /api/devices", two)
        let m = DevicesModel(api: t.api(), onThisDeviceRemoved: {})
        m.add()
        await m.jobs.settled()
        let follow = Task { @MainActor in await m.followPairing() }
        _ = await eventually { m.left > 0 }
        follow.cancel()
        await follow.value   // returns: the loop does not outlive its task
        #expect(m.pairLink != nil)
    }
}

extension Device {
    static func fixture(_ id: Int64) -> Device {
        try! JSONDecoder().decode(Device.self, from: Data(#"{"id":\#(id),"name":"d"}"#.utf8))
    }
}

@MainActor @Suite struct ResourcesModelTests {
    @Test func aReadFailureIsShownAndRetryGoesOn() async {
        let t = RoutedTransport()
        t.on("GET /api/resources", 500, #"{"detail":"x"}"#)
        let m = ResourcesModel(api: t.api())
        await m.refresh()
        m.retry()
        #expect(m.attempt == 1)
    }

    @Test func pollingEndsWithItsTask() async {
        let t = RoutedTransport()
        t.on("GET /api/resources", 500, #"{"detail":"x"}"#)
        let m = ResourcesModel(api: t.api())
        let poll = Task { @MainActor in await m.poll() }
        _ = await eventually { t.count("GET /api/resources") >= 1 }
        poll.cancel()
        await poll.value
        let n = t.count("GET /api/resources")
        try? await Task.sleep(nanoseconds: 100_000_000)
        #expect(t.count("GET /api/resources") == n)
    }
}

@MainActor @Suite struct CleanupModelTests {
    private let list = #"{"groups":[{"id":"g","name":"G","bytes":10,"count":1,"items":[{"id":"a","name":"a","bytes":10}]}],"total":10,"older_than_days":14}"#

    @Test func loadAndDelete() async {
        let t = RoutedTransport()
        t.on("GET /api/storage/cleanup", list)
        t.on("POST /api/storage/cleanup", #"{"removed":["a"],"skipped":[],"freed":10,"groups":[],"total":0}"#)
        let m = CleanupModel(api: t.api())
        await m.load()
        #expect(m.list?.groups.count == 1)
        m.toggle("a")
        #expect(m.selected == ["a"])
        m.delete()
        _ = await eventually { !m.busy && m.result != nil }
        #expect(m.result?.freed == 10)
        #expect(m.selected.isEmpty)
        #expect(m.list?.groups.isEmpty == true)
    }

    @Test func aFailureShowsAKeyAndOwnerOnlyIsTold() async {
        let t = RoutedTransport()
        t.on("GET /api/storage/cleanup", 403, #"{"detail":"owner"}"#)
        let m = CleanupModel(api: t.api())
        await m.load()
        #expect(m.failed != nil)
        let owner = m.failed
        t.on("GET /api/storage/cleanup", 500, #"{"detail":"x"}"#)
        await m.load()
        #expect(m.failed != owner)
    }
}

@MainActor @Suite struct UsageModelTests {
    @Test func loadsUsageAndProviderNames() async {
        let t = RoutedTransport()
        t.on("GET /api/usage", #"{"today":{"input_tokens":5}}"#)
        t.on("GET /api/providers", #"[{"name":"p","display_name":"Pee"}]"#)
        let m = UsageModel(api: t.api())
        await m.load()
        #expect(m.usage != nil)
        #expect(m.label("p") == "Pee")
        #expect(m.label("zz") == "zz")
    }

    @Test func aFailureKeepsWhatWasShown() async {
        let t = RoutedTransport()
        t.on("GET /api/usage", #"{}"#)
        t.on("GET /api/providers", 500, "{}")
        let m = UsageModel(api: t.api())
        await m.load()
        let kept = m.usage
        t.on("GET /api/usage", 500, "{}")
        await m.load()
        #expect(m.usage == kept)
        #expect(m.providers.isEmpty)
    }

    @Test func anOlderLoadNeverOverwritesANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/usage") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"today":{"input_tokens":1}}"#) }
            return (200, #"{"today":{"input_tokens":2}}"#)
        }
        t.on("GET /api/providers", #"[]"#)
        let m = UsageModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/usage") == 1 }
        await m.load()
        let newer = m.usage
        await gate.release()
        await first.value
        #expect(m.usage == newer)
    }
}

private func pins(_ ids: [String]) -> String { #"{"items":["# + ids.map { #"{"id":"\#($0)","site":"s"}"# }.joined(separator: ",") + "]}" }

@MainActor @Suite struct PaymentPinsModelTests {

    @Test func listsAndDeletes() async {
        let t = RoutedTransport()
        t.on("GET /api/payment-pins") { _, n in (200, n == 0 ? pins(["a", "b"]) : pins(["b"])) }
        t.on("DELETE /api/payment-pins/a", "{}")
        let m = PaymentPinsModel(api: t.api())
        await m.load()
        #expect(m.pins.count == 2)
        m.delete(m.pins[0])
        await m.jobs.settled()
        #expect(m.pins.map(\.id) == ["b"])
    }

    @Test func aFailedReadKeepsTheList() async {
        let t = RoutedTransport()
        t.on("GET /api/payment-pins") { _, n in n == 0 ? (200, pins(["a"])) : (500, "{}") }
        let m = PaymentPinsModel(api: t.api())
        await m.load()
        await m.load()
        #expect(m.pins.count == 1)
    }

    @Test func anOlderReadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/payment-pins") { _, n in
            if n == 0 { await gate.wait(); return (200, pins(["old"])) }
            return (200, pins(["new"]))
        }
        let m = PaymentPinsModel(api: t.api())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/payment-pins") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.pins.map(\.id) == ["new"])
    }
}
