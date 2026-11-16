import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloShared
@testable import KeloChat

@MainActor private func prefs() -> Prefs { Prefs(defaults: UserDefaults(suiteName: "kelo-chat-\(UUID().uuidString)")!) }

private let unofficial = ModelProvider(name: "u", displayName: "U", unofficial: true, vendor: "V", consentVersion: 1)

@MainActor @Suite struct ChatConsentAndChooserTests {
    @Test func consentAgreeRecordsItDeclineRunsNoAndAFailureStaysAsked() async {
        let t = RoutedTransport()
        t.on("PUT /api/providers/u/consent", "{}")
        let gate = ConsentGate(api: t.api(), prefs: prefs())
        var yes = 0, no = 0
        gate.request(unofficial, onDecline: { no += 1 }, then: { yes += 1 })
        #expect(gate.asking?.name == "u")
        gate.agree()
        await gate.jobs.settled()
        #expect(yes == 1 && gate.accepted.contains("u"))
        let g2 = ConsentGate(api: t.api(), prefs: prefs())
        g2.request(unofficial, onDecline: { no += 1 }, then: { yes += 1 })
        g2.decline()
        #expect(no == 1 && yes == 1)
        t.on("PUT /api/providers/u/consent", 500, #"{"detail":"x"}"#)
        let g3 = ConsentGate(api: t.api(), prefs: prefs())
        g3.request(unofficial, then: { yes += 1 })
        g3.agree()
        await g3.jobs.settled()
        #expect(yes == 1 && g3.error != nil && g3.asking != nil)
    }

    @Test func aPickSavesTheProfileAndAFailureShowsTheReason() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", #"{"provider":"a","model":"a2"}"#)
        let host = FakeProfileHost(Profile(provider: "a", model: "a1"))
        let m = ModelChooserModel(api: t.api(), cache: memoryCache(), host: host)
        var after = 0
        m.switchTo(ModelChoice("a", "a2"), after: { after += 1 }, needConsent: { _, _ in })
        await m.jobs.settled()
        #expect(after == 1 && host.profile?.model == "a2" && m.switching == nil)
        t.on("PUT /api/profile", 400, #"{"detail":"bad model"}"#)
        m.switchTo(ModelChoice("a", "a3"), after: { after += 1 }, needConsent: { _, _ in })
        await m.jobs.settled()
        #expect(after == 1 && !m.err.isEmpty)
    }

    @Test func theModelInUseClosesWithoutACall() async {
        let t = RoutedTransport()
        let host = FakeProfileHost(Profile(provider: "a", model: "a1"))
        let m = ModelChooserModel(api: t.api(), cache: memoryCache(), host: host)
        var after = 0
        m.switchTo(ModelChoice("a", "a1"), after: { after += 1 }, needConsent: { _, _ in })
        #expect(after == 1 && t.calls.isEmpty)
    }

    @Test func aListIsFetchedOncePerOpenAndAFailureShowsTheReason() async {
        let t = RoutedTransport()
        t.on("GET /api/models", #"{"provider":"a","models":["a1","a2"]}"#)
        let host = FakeProfileHost()
        let m = ModelChooserModel(api: t.api(), cache: memoryCache(), host: host)
        m.load("a"); m.load("a")
        await m.settled()
        #expect(t.count("GET /api/models") == 1)
        #expect(m.lists["a"]?.models == ["a1", "a2"])
        let t2 = RoutedTransport()
        t2.down = true
        let m2 = ModelChooserModel(api: t2.api(), cache: memoryCache(), host: host)
        m2.load("b")
        await m2.settled()
        #expect(m2.lists["b"]?.error != nil)
    }

}

@MainActor @Suite struct ChatSignInModelTests {
    @Test func startingASessionThenFinishingIt() async {
        let t = RoutedTransport()
        t.on("POST /api/providers/u/login/start", #"{"session":"s1","state":"pending","url":"https://auth.test","expires_in":60}"#)
        t.on("POST /api/providers/u/login/finish", #"{"session":"s1","state":"done"}"#)
        let gate = ConsentGate(api: t.api(), prefs: prefs())
        var signedIn = 0
        let m = SignInModel(api: t.api(), provider: ModelProvider(name: "u"), gate: gate, onSignedIn: { signedIn += 1 })
        m.start()
        await m.jobs.settled()
        #expect(m.s?.state == "pending")
        m.finish(code: " abc ")
        await m.jobs.settled()
        #expect(signedIn == 1)
    }

    @Test func aFailureShowsTheReasonAndAnExpiredSessionIsMarked() async {
        let t = RoutedTransport()
        t.on("POST /api/providers/u/login/start", 500, #"{"detail":"x"}"#)
        let gate = ConsentGate(api: t.api(), prefs: prefs())
        let m = SignInModel(api: t.api(), provider: ModelProvider(name: "u"), gate: gate, onSignedIn: {})
        m.start()
        await m.jobs.settled()
        #expect(!m.err.isEmpty && !m.busy)
        t.on("POST /api/providers/u/login/start", #"{"session":"s1","state":"pending","expires_in":60}"#)
        t.on("POST /api/providers/u/login/finish", 404, #"{"detail":"gone"}"#)
        m.start()
        await m.jobs.settled()
        m.finish(code: "x")
        await m.jobs.settled()
        #expect(m.s?.state == "expired")
    }

    @Test func followingEndsWithItsTask() async {
        let t = RoutedTransport()
        t.on("POST /api/providers/u/login/start", #"{"session":"s1","state":"pending","mode":"device_code","user_code":"A","expires_in":60}"#)
        t.on("GET /api/providers/u/login/status", #"{"session":"s1","state":"pending","mode":"device_code"}"#)
        let gate = ConsentGate(api: t.api(), prefs: prefs())
        let m = SignInModel(api: t.api(), provider: ModelProvider(name: "u"), gate: gate, onSignedIn: {})
        m.start()
        await m.jobs.settled()
        let follow = Task { @MainActor in await m.follow() }
        follow.cancel()
        await follow.value
        #expect(true)
    }

    @Test func signOutReportsAndAFailureIsReported() async {
        let t = RoutedTransport()
        t.on("DELETE /api/providers/u/login", "{}")
        let m = SignOutModel(api: t.api(), provider: ModelProvider(name: "u"))
        var results = 0
        m.signOut(onResult: { _, _, _ in results += 1 })
        await m.jobs.settled()
        t.on("DELETE /api/providers/u/login", 500, #"{"detail":"x"}"#)
        m.signOut(onResult: { _, _, _ in results += 1 })
        await m.jobs.settled()
        #expect(results == 2 && !m.busy)
    }

    @Test func aReportIsSentAndAFailureKeepsTheDraft() async {
        let t = RoutedTransport()
        t.on("POST /api/reports", "{}")
        let m = ReportModel(api: t.api())
        var sent = 0, kept = 0
        m.send(ReportDraft(), reply: "r", asked: nil, version: "1", sent: { sent += 1 }, failed: { _ in kept += 1 })
        await m.jobs.settled()
        #expect(sent == 1 && kept == 0)
        t.on("POST /api/reports", 500, #"{"detail":"x"}"#)
        m.send(ReportDraft(), reply: "r", asked: nil, version: "1", sent: { sent += 1 }, failed: { _ in kept += 1 })
        await m.jobs.settled()
        #expect(sent == 1 && kept == 1 && !m.err.isEmpty)
    }
}

@MainActor @Suite struct ChatLeafModelTests {
    @Test func aPictureIsDownloadedOnce() async {
        let t = RoutedTransport()
        let m = RemotePictureModel()
        await m.load("", api: t.api())
        await m.load("/api/uploads/x", api: nil)
        #expect(m.picture == nil && t.calls.isEmpty)
        await m.load("/api/uploads/x", api: t.api())   // the fake answers 404: nothing to show
        #expect(m.picture == nil)
    }

    @Test func theConnectCardWatchesUntilConnected() async {
        let t = RoutedTransport()
        t.on("GET /api/connectors", #"[{"id":"gh","name":"GitHub","status":"connected"}]"#)
        let m = ConnectCardModel(api: t.api(), service: "gh")
        await m.watch()
        #expect(m.connector?.status == "connected")
    }

    @Test func theConnectCardWatchStopsWithItsTask() async {
        let t = RoutedTransport()
        t.on("GET /api/connectors", #"[{"id":"gh","name":"GitHub","status":"available"}]"#)
        let m = ConnectCardModel(api: t.api(), service: "gh")
        let w = Task { @MainActor in await m.watch() }
        _ = await eventually { t.count("GET /api/connectors") >= 1 }
        w.cancel()
        await w.value
        let n = t.count("GET /api/connectors")
        try? await Task.sleep(nanoseconds: 100_000_000)
        #expect(t.count("GET /api/connectors") == n)
    }

    @Test func aKeychainItemIsFoundOrGoneAndCanBeDiscarded() async {
        let t = RoutedTransport()
        t.on("GET /api/vault", #"{"items":[{"name":"k","label":"K","kind":"key"}]}"#)
        t.on("DELETE /api/vault/k/draft", "{}")
        let m = SecureLookupModel()
        await m.load("k", api: t.api())
        #expect(m.state == 1 && m.item?.name == "k")
        let gone = SecureLookupModel()
        await gone.load("zz", api: t.api())
        #expect(gone.state == 2)
        let down = SecureLookupModel()
        await down.load("k", api: RoutedTransport().api())
        #expect(down.state == 2)
        m.discard("k", api: t.api())
        #expect(m.closed)
    }

    @Test func savingAKeyAndItsFailure() async throws {
        let t = RoutedTransport()
        t.on("PUT /api/vault/k/login", "{}")
        let item = try JSONDecoder().decode(ChatVaultItem.self, from: Data(#"{"name":"k","label":"K","kind":"login"}"#.utf8))
        let m = SecureEntrySaveModel()
        var saved = 0
        m.save(item, user: "u", pass: "p", mode: .afterSuccess, api: t.api(), saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        t.on("PUT /api/vault/k/login", 400, #"{"detail":"bad"}"#)
        m.save(item, user: "u", pass: "p", mode: .afterSuccess, api: t.api(), saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1 && !m.msg.isEmpty)
    }

    @Test func theSecretSheetSavesByUseAndFailsWithTheReason() async {
        let t = RoutedTransport()
        t.on("PUT /api/providers/p/key", "{}")
        let m = SecretSaveModel()
        var saved = 0
        m.save(use: .aiKey, target: "p", value: "key-key-key", others: [:], site: "", label: "", user: "", host: "", api: t.api(), saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        t.on("PUT /api/providers/p/key", 400, #"{"detail":"bad key"}"#)
        m.save(use: .aiKey, target: "p", value: "key-key-key", others: [:], site: "", label: "", user: "", host: "", api: t.api(), saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1 && m.err == "bad key" && !m.busy)
    }
}
