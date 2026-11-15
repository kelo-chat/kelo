import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

@MainActor @Suite struct ServerUpdateModelTests {
    @Test func loadShowsTheStatus() async {
        let t = RoutedTransport()
        t.on("GET /api/server-update", #"{"state":"ok","current":"1.0","update_available":true}"#)
        let m = ServerUpdateModel(api: t.api(), cache: memoryCache())
        await m.load()
        #expect(m.status?.updateAvailable == true)
        #expect(!m.hidden)
    }

    @Test func anOlderServerOrAnotherDeviceSeesNothing() async {
        let t = RoutedTransport()
        t.on("GET /api/server-update", 404, #"{"detail":"none"}"#)
        let m = ServerUpdateModel(api: t.api(), cache: memoryCache())
        await m.load()
        #expect(m.hidden)
        #expect(m.status == nil)
    }

    @Test func aFailedCheckShowsTheServersWordsOrAConnectionMessage() async {
        let t = RoutedTransport()
        t.on("POST /api/server-update/check", 409, #"{"detail":"busy"}"#)
        let m = ServerUpdateModel(api: t.api(), cache: memoryCache())
        m.check()
        await m.jobs.settled()
        #expect(m.problem != nil)
        #expect(!m.checking)
    }

    @Test func followingEndsWhenItsTaskIsCancelled() async {
        let t = RoutedTransport()
        t.on("GET /api/server-update", #"{"state":"ok","job":{"state":"running"}}"#)
        let m = ServerUpdateModel(api: t.api(), cache: memoryCache())
        await m.load()
        let follow = Task { @MainActor in await m.follow() }
        try? await Task.sleep(nanoseconds: 100_000_000)
        follow.cancel()
        await follow.value
        let n = t.count("GET /api/server-update")
        try? await Task.sleep(nanoseconds: 200_000_000)
        #expect(t.count("GET /api/server-update") == n)
    }
}
