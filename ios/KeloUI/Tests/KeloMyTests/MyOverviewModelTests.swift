import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloMy

@MainActor @Suite struct MyOverviewModelTests {
    private func make(_ t: RoutedTransport, limit: UInt64 = 5_000_000_000, signedOut: @escaping () -> Void = {}, cleared: @escaping () -> Void = {}) -> MyOverviewModel {
        MyOverviewModel(api: t.api(), cache: memoryCache(), onSignedOut: signedOut, onHistoryCleared: cleared, removeLimit: limit)
    }

    @Test func signOutRemovesThisPhoneThenSignsOutLocally() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", #"{"devices":[{"id":4,"name":"x"}],"current":4}"#)
        t.on("DELETE /api/devices/4", "{}")
        var out = 0
        let m = make(t, signedOut: { out += 1 })
        m.signOut()
        await m.jobs.settled()
        #expect(t.count("DELETE /api/devices/4") == 1)
        #expect(out == 1)
    }

    @Test func aFailingServerStillSignsOut() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", 500, #"{"detail":"boom"}"#)
        var out = 0
        let m = make(t, signedOut: { out += 1 })
        m.signOut()
        await m.jobs.settled()
        #expect(out == 1)
        #expect(t.count("DELETE /api/devices/4") == 0)
    }

    @Test func aSlowServerIsWaitedForOnlyTheLimit() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/devices") { _, _ in await gate.wait(); return (200, #"{"devices":[],"current":1}"#) }
        var out = 0
        let m = make(t, limit: 50_000_000, signedOut: { out += 1 })
        m.signOut()
        let done = await eventually { out == 1 }
        #expect(done)
        await gate.release()
        await m.jobs.settled()
    }

    @Test func loadFillsTheNumbersAndKeepsThemForNextTime() async {
        let t = RoutedTransport()
        t.on("GET /api/devices", #"{"devices":[{"id":1,"name":"a"},{"id":2,"name":"b"}],"current":1}"#)
        t.on("GET /api/uploads", "[1,2,3]")
        let m = make(t)
        await m.load()
        #expect(m.o.devices == 2)
        #expect(m.o.documents == 3)
    }

    @Test func everythingFailingMarksItOffline() async {
        let t = RoutedTransport()
        t.down = true
        let m = make(t)
        await m.load()
        #expect(m.offline)
    }

    @Test func anOlderLoadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/devices") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"devices":[{"id":1,"name":"a"}],"current":1}"#) }
            return (200, #"{"devices":[{"id":1,"name":"a"},{"id":2,"name":"b"}],"current":1}"#)
        }
        let m = make(t)
        m.reload()
        _ = await eventually { t.count("GET /api/devices") == 1 }
        await m.load()
        #expect(m.o.devices == 2)
        await gate.release()
        await m.jobs.settled()
        #expect(m.o.devices == 2)
    }

    @Test func clearingTheChatTellsTheScreensAndFailureShowsAMessage() async {
        let t = RoutedTransport()
        t.on("DELETE /api/messages", "{}")
        var cleared = 0
        let m = make(t, cleared: { cleared += 1 })
        m.clearChat()
        await m.jobs.settled()
        #expect(cleared == 1)
        #expect(m.notice != nil)
        t.on("DELETE /api/messages", 500, #"{"detail":"nope"}"#)
        m.clearNotice()
        m.clearChat()
        await m.jobs.settled()
        #expect(cleared == 1)
        #expect(m.notice != nil)
    }
}

@MainActor @Suite struct AgentPageModelTests {
    private let polite = #"{"agent_name":"Kelo","tone":"polite"}"#
    private let friendly = #"{"agent_name":"Kelo","tone":"friendly"}"#

    @Test func loadTakesTheServersProfile() async {
        let t = RoutedTransport()
        t.on("GET /api/profile", friendly)
        let host = FakeProfileHost()
        let m = AgentPageModel(api: t.api(), host: host)
        await m.load()
        #expect(host.profile?.tone == "friendly")
    }

    @Test func aProfileReadFromBeforeATonePickNeverPutsTheOldToneBack() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/profile") { _, _ in await gate.wait(); return (200, #"{"agent_name":"Kelo","tone":"polite"}"#) }
        t.on("PUT /api/profile", friendly)
        let host = FakeProfileHost()
        let m = AgentPageModel(api: t.api(), host: host)
        let loading = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/profile") == 1 }
        m.pickTone("friendly")
        await m.jobs.settled()
        #expect(host.profile?.tone == "friendly")
        await gate.release()
        await loading.value
        #expect(host.profile?.tone == "friendly")
    }

    @Test func aFailedPickGoesBackToTheOldToneWithAMessage() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", 500, #"{"detail":"x"}"#)
        let host = FakeProfileHost()
        let m = AgentPageModel(api: t.api(), host: host)
        m.pickTone("friendly")
        #expect(m.shownTone == "friendly")
        await m.jobs.settled()
        #expect(m.shownTone == "polite")
        #expect(m.notice != nil)
    }

    @Test func removingThePictureTellsTheHost() async {
        let t = RoutedTransport()
        t.on("DELETE /api/agent/avatar", "{}")
        let host = FakeProfileHost()
        let m = AgentPageModel(api: t.api(), host: host)
        m.removePicture()
        await m.jobs.settled()
        #expect(host.avatar?.version == 0)
    }
}

@MainActor @Suite struct ProfileModelTests {
    @Test func savingHandsTheProfileToTheHost() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", #"{"user_name":"Ann","agent_name":"Bo"}"#)
        let host = FakeProfileHost()
        let m = ProfileModel(api: t.api(), host: host)
        m.save(user: " Ann ", agent: "Bo")
        await m.jobs.settled()
        #expect(m.saved)
        #expect(host.profile?.userName == "Ann")
        m.edited()
        #expect(!m.saved)
    }

    @Test func aFailureShowsTheError() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", 400, #"{"detail":"too long"}"#)
        let host = FakeProfileHost()
        let m = ProfileModel(api: t.api(), host: host)
        m.save(user: "a", agent: "b")
        await m.jobs.settled()
        #expect(!m.saved)
        #expect(!m.error.isEmpty)
    }
}
