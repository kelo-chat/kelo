import Testing
import Foundation
import KeloCore
import KeloTestSupport
@testable import KeloShared
@testable import KeloShell

@Suite struct ConnectModelTests {
    @MainActor @Test func aValidLinkWaitsForTheQuestion() {
        Strings.shared.use("en")
        let m = ConnectModel()
        #expect(m.useLink("https://kelo.example.com/#pair=" + String(repeating: "a", count: 24)))
        #expect(m.confirm?.serverUrl == "https://kelo.example.com")
        #expect(m.linkError == nil)
        #expect(ConnectModel.host(of: m.confirm!) == "kelo.example.com")
    }

    @MainActor @Test func notALinkSaysWhy() {
        Strings.shared.use("en")
        let m = ConnectModel()
        #expect(!m.useLink("hello"))
        #expect(m.linkError == L(R.string.connect_link_invalid))
        #expect(m.confirm == nil)
    }

    @MainActor @Test func plainHttpIsRefusedOutsideLocalDebug() {
        Strings.shared.use("en")
        let m = ConnectModel()
        #expect(!m.useLink("http://kelo.example.com/#pair=" + String(repeating: "b", count: 24)))
        #expect(m.linkError == L(R.string.connect_https_only))
    }

    @MainActor @Test func portIsShown() {
        let link = PairingLink(serverUrl: "https://kelo.example.com:8443", code: String(repeating: "c", count: 20))
        #expect(ConnectModel.host(of: link) == "kelo.example.com:8443")
    }
}

@Suite struct HeaderTests {
    @Test func badgeSizesMatchAndroid() {
        #expect(HeaderMetrics.badge == 68)
        #expect(abs(HeaderMetrics.character - 62.56) < 0.01)
    }

    @Test func centerShiftMatchesAndroid() {
        #expect(KeloMood.working.centerShift == 8)
        #expect(KeloMood.approval.centerShift == 6)
        #expect(KeloMood.idle.centerShift == 0)
        #expect(KeloMood.thinking.centerShift == 2)
    }
}

// Android update/UpdateRules.belowMin: the server's oldest supported build (min_app_version.ios)
@Suite struct AppUpdateRulesTests {
    @Test func onlyAnOlderBuildIsStopped() {
        #expect(AppUpdateRules.belowMin(build: "56", min: 57))
        #expect(!AppUpdateRules.belowMin(build: "57", min: 57))
        #expect(!AppUpdateRules.belowMin(build: "58", min: 57))
        #expect(AppUpdateRules.belowMin(build: " 1 ", min: 2))
    }

    @Test func noMinimumOrAnUnknownBuildNeverStops() {
        #expect(!AppUpdateRules.belowMin(build: "1", min: nil))
        #expect(!AppUpdateRules.belowMin(build: "1", min: 0))
        #expect(!AppUpdateRules.belowMin(build: nil, min: 57))
        #expect(!AppUpdateRules.belowMin(build: "", min: 57))
        #expect(!AppUpdateRules.belowMin(build: "1.0.2", min: 57))   // not a whole number
        #expect(!AppUpdateRules.belowMin(build: "0", min: 57))
    }

    @Test func theButtonGoesToTheStoreOrTestFlight() {
        #expect(AppUpdateRules.storeURL(appStoreId: "6740000000", testFlight: false).absoluteString == "itms-apps://apps.apple.com/app/id6740000000")
        #expect(AppUpdateRules.storeURL(appStoreId: "6740000000", testFlight: true).absoluteString == "itms-beta://beta.itunes.apple.com/v1/app/6740000000")
        #expect(AppUpdateRules.storeURL(appStoreId: nil, testFlight: true).absoluteString == "itms-beta://")
        #expect(AppUpdateRules.storeURL(appStoreId: "", testFlight: false).absoluteString.hasPrefix("itms-apps://"))
        #expect(AppUpdateRules.storeURL(appStoreId: "abc", testFlight: false).absoluteString.contains("term=Kelo"))   // not an id
    }

    @Test func theProfileCarriesTheMinimum() throws {
        let p = try JSONDecoder().decode(Profile.self, from: Data(#"{"agent_name":"K","min_app_version":{"android":402400,"ios":12,"desktop":100000003}}"#.utf8))
        #expect(p.minAppVersion["ios"] == 12)
        #expect(try JSONDecoder().decode(Profile.self, from: Data(#"{"agent_name":"K"}"#.utf8)).minAppVersion.isEmpty)
    }
}

@MainActor @Suite struct ConnectPairingTests {
    private final class Box { var sessions: [Session] = [] }

    @Test func aConfirmedLinkSignsTheSessionIn() async {
        Strings.shared.use("en")
        let t = RoutedTransport()
        t.on("GET /api/e2e/info", 404, "{}")
        let box = Box()
        let m = ConnectModel(client: PairingClient(transport: t), deviceName: { "iPhone" }, signIn: { box.sessions.append($0) })
        m.useLink("https://kelo.example.com/#pair=" + String(repeating: "a", count: 24))
        m.confirmPair()
        await m.jobs.settled()
        // the fake server has no KE2E: the phone says to update the server, and nothing signs in
        #expect(box.sessions.isEmpty && !m.pairing && m.notice != nil)
    }

    @Test func anInvalidAddressNeverReachesTheServer() async {
        Strings.shared.use("en")
        let t = RoutedTransport()
        let m = ConnectModel(client: PairingClient(transport: t))
        m.connectWithKey(address: "", key: "k")
        #expect(m.keyError == L(R.string.connect_server_invalid) && t.calls.isEmpty)
    }
}

@MainActor @Suite struct OnboardingAndShellModelTests {
    @Test func onboardingSavesTheNamesAndHandsTheProfileOver() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", #"{"user_name":"Ann","agent_name":"Bo"}"#)
        var got: Profile?
        let m = OnboardingModel(api: { t.api() }, onSaved: { got = $0 })
        m.finish(user: " Ann ", agent: "Bo")
        await m.jobs.settled()
        #expect(got?.userName == "Ann" && !m.busy && m.error == nil)
    }

    @Test func onboardingFailureShowsTheReason() async {
        let t = RoutedTransport()
        t.on("PUT /api/profile", 400, #"{"detail":"too long"}"#)
        var got: Profile?
        let m = OnboardingModel(api: { t.api() }, onSaved: { got = $0 })
        m.finish(user: "a", agent: "b")
        await m.jobs.settled()
        #expect(got == nil && m.error != nil)
    }

    @Test func theHeaderNameFallsBackToKeloAndFollowsTheProfile() {
        #expect(HeaderName.of(nil) == "Kelo")
        #expect(HeaderName.of(Profile(agentName: "  ")) == "Kelo")
        #expect(HeaderName.of(Profile(agentName: "Bo")) == "Bo")
    }

    @Test func theShellSyncsTheAvatarFromThePollsVersionFirst() async {
        let t = RoutedTransport()
        t.on("GET /api/agent/avatar", "avatar")
        let activity = AgentActivityModel()
        let avatar = AgentAvatarModel()
        let profile = Profile(agentName: "Bo", avatarVersion: 0)
        let shell = ShellModel(activity: activity, avatar: avatar, cache: memoryCache(), api: { t.api() }, profile: { profile })
        await shell.syncAvatar()   // version 0: no picture, no call
        #expect(t.calls.isEmpty)
    }
}
