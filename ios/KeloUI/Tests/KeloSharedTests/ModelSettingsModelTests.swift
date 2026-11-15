import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloShared

@MainActor @Suite struct ModelSettingsModelTests {
    private func prefs() -> Prefs { Prefs(defaults: UserDefaults(suiteName: "kelo-test-\(UUID().uuidString)")!) }
    private func make(_ t: RoutedTransport, _ p: Profile = Profile(provider: "a", model: "m1")) -> (ModelSettingsModel, FakeProfileHost) {
        let host = FakeProfileHost(p)
        return (ModelSettingsModel(api: t.api(), cache: memoryCache(), prefs: prefs(), host: host), host)
    }
    private func routes(_ t: RoutedTransport) {
        t.on("GET /api/providers", #"[{"name":"a","display_name":"A"},{"name":"b","display_name":"B"}]"#)
        t.on("GET /api/models") { c, _ in
            let p = c.query.contains("provider=b") ? "b" : "a"
            return (200, #"{"provider":"\#(p)","models":["\#(p)1","\#(p)2"]}"#)
        }
        t.on("GET /api/usage/summary", "{}")
    }

    @Test func startLoadsProvidersAndTheMainList() async {
        let t = RoutedTransport()
        routes(t)
        let (m, host) = make(t, Profile(provider: "a", model: "a1"))
        defer { withExtendedLifetime(host) {} }
        await m.start()
        await m.listsSettled()
        #expect(m.providers.count == 2)
        #expect(m.models?.models == ["a1", "a2"])
        #expect(!m.loadingModels)
    }

    @Test func aListForAProviderPickedAwayFromIsNeverShown() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/providers", #"[{"name":"a"},{"name":"b"}]"#)
        t.on("GET /api/models") { c, _ in
            if c.query.contains("provider=a") { await gate.wait(); return (200, #"{"provider":"a","models":["a1"]}"#) }
            return (200, #"{"provider":"b","models":["b1"]}"#)
        }
        let (m, host) = make(t, Profile(provider: "a", model: "a1"))
        defer { withExtendedLifetime(host) {} }
        m.loadModels("a")
        _ = await eventually { t.count("GET /api/models") == 1 }
        m.pickProvider("b")
        await m.listsSettled()
        #expect(m.models?.provider == "b")
        await gate.release()
        await m.listsSettled()
        #expect(m.models?.provider == "b")
    }

    @Test func aFailedListShowsTheReasonNotAPreviousProvidersList() async {
        let t = RoutedTransport()
        t.on("GET /api/models", 500, #"{"detail":"x"}"#)
        let (m, host) = make(t)
        defer { withExtendedLifetime(host) {} }
        m.loadModels("a")
        await m.listsSettled()
        #expect(m.models?.error != nil)
        #expect(!m.loadingModels)
    }

    @Test func savingHandsTheProfileToTheHostAndTicks() async {
        let t = RoutedTransport()
        routes(t)
        t.on("PUT /api/profile", #"{"provider":"a","model":"a2"}"#)
        let (m, host) = make(t)
        m.setModel("a2")
        m.save()
        await m.jobs.settled()
        #expect(m.savedTick == 1)
        #expect(host.profile?.model == "a2")
        #expect(!m.saving)
    }

    @Test func aSaveFailureShowsTheReasonAndKeepsThePageOpen() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", 400, #"{"detail":"bad model"}"#)
        let (m, host) = make(t)
        defer { withExtendedLifetime(host) {} }
        m.save()
        await m.jobs.settled()
        #expect(m.savedTick == 0)
        #expect(!m.err.isEmpty)
    }

    @Test func aKeyFailureShowsItsMessage() async {
        let t = RoutedTransport()
        t.on("PUT /api/providers/a/key", 400, #"{"detail":"bad key"}"#)
        let (m, host) = make(t)
        defer { withExtendedLifetime(host) {} }
        var saved = 0
        m.saveKey("k", saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 0)
        #expect(!m.keyMsg.isEmpty)
    }

    @Test func anAddressWithoutAHostIsRefusedLocally() async {
        let t = RoutedTransport()
        let (m, host) = make(t)
        defer { withExtendedLifetime(host) {} }
        m.saveUrl("", saved: {})
        #expect(!m.urlMsg.isEmpty)
        #expect(t.calls.isEmpty)
    }

    @Test func providerDecodesUserEndpoint() throws {
        let json = #"{"name":"custom-ollama","display_name":"Ollama","user_endpoint":true}"#.data(using: .utf8)!
        let p = try JSONDecoder().decode(ModelProvider.self, from: json)
        #expect(p.userEndpoint)
        let defJson = #"{"name":"anthropic"}"#.data(using: .utf8)!
        let defP = try JSONDecoder().decode(ModelProvider.self, from: defJson)
        #expect(!defP.userEndpoint)
    }

    @Test func addEndpointSendsNameAndBaseUrl() async {
        let t = RoutedTransport()
        routes(t)
        var body: String?
        t.on("POST /api/providers/custom-endpoints") { c, _ in
            body = c.body
            return (201, #"{"id":"custom-home","name":"Home","base_url":"http://192.168.0.10:11434/v1"}"#)
        }
        t.on("GET /api/providers", #"[{"name":"a"},{"name":"custom-home","display_name":"Home","user_endpoint":true}]"#)
        let (m, host) = make(t)
        defer { withExtendedLifetime(host) {} }
        var done = 0
        m.addEndpoint(name: "Home", baseUrl: "http://192.168.0.10:11434/v1", done: { done += 1 })
        await m.jobs.settled()
        #expect(done == 1)
        #expect(body?.contains("Home") == true)
        #expect(body?.contains("http://192.168.0.10:11434/v1") == true)
        #expect(m.provider == "custom-home")
    }

    @Test func deleteInUseEndpointShowsMessage() async {
        let t = RoutedTransport()
        routes(t)
        t.on("DELETE /api/providers/custom-endpoints/custom-home", 409, #"{"code":"in_use","detail":{"code":"in_use"}}"#)
        let (m, host) = make(t)
        defer { withExtendedLifetime(host) {} }
        m.deleteEndpoint("custom-home")
        await m.jobs.settled()
        #expect(m.endpointMsg == L(R.string.endpoint_in_use))
    }
}

@MainActor @Suite struct SignInAndConsentModelTests {
    private func prefs() -> Prefs { Prefs(defaults: UserDefaults(suiteName: "kelo-test-\(UUID().uuidString)")!) }

    @Test func signOutReportsTheOutcomeAndAFailure() async {
        let t = RoutedTransport()
        t.on("DELETE /api/providers/a/login", "{}")
        let m = SignOutModel(api: t.api(), provider: ModelProvider(name: "a"))
        var results = 0
        m.signOut(onResult: { _, _, _ in results += 1 })
        await m.jobs.settled()
        #expect(results == 1)
        #expect(!m.busy)
        t.on("DELETE /api/providers/a/login", 500, #"{"detail":"x"}"#)
        m.signOut(onResult: { _, _, _ in results += 1 })
        await m.jobs.settled()
        #expect(results == 2)
    }

    @Test func consentAgreeRecordsItAndADeclineRunsNo() async {
        let t = RoutedTransport()
        t.on("PUT /api/providers/u/consent", "{}")
        let gate = ConsentGate(api: t.api(), prefs: prefs())
        let p = ModelProvider(name: "u", unofficial: true, consentVersion: 1)
        var yes = 0, no = 0
        gate.request(p, onDecline: { no += 1 }, then: { yes += 1 })
        #expect(gate.asking?.name == "u")
        gate.agree()
        await gate.jobs.settled()
        #expect(yes == 1)
        #expect(gate.accepted.contains("u"))
        let gate2 = ConsentGate(api: t.api(), prefs: prefs())
        gate2.request(p, onDecline: { no += 1 }, then: { yes += 1 })
        gate2.decline()
        #expect(no == 1)
        #expect(yes == 1)
    }

    @Test func aConsentFailureShowsTheErrorAndDoesNotContinue() async {
        let t = RoutedTransport()
        t.on("PUT /api/providers/u/consent", 500, #"{"detail":"x"}"#)
        let gate = ConsentGate(api: t.api(), prefs: prefs())
        var yes = 0
        gate.request(ModelProvider(name: "u", unofficial: true, consentVersion: 1), then: { yes += 1 })
        gate.agree()
        await gate.jobs.settled()
        #expect(yes == 0)
        #expect(gate.error != nil)
    }
}
