import Testing
import Foundation
import KeloCore
@testable import KeloConnectors
import KeloShared

private func decode<T: Decodable>(_ json: String, as: T.Type) throws -> T {
    try JSONDecoder().decode(T.self, from: Data(json.utf8))
}

private func row(_ id: String, status: String = "available", connect: String? = "key", featured: Bool = false, rank: Int? = nil,
                 item: String = "", category: String = "서비스") -> ConnectorInfo {
    ConnectorInfo(id: id, name: id.capitalized, description: "\(id) things", category: category, status: status, connect: connect,
                  item: item, featured: featured, rank: rank)
}

private func cred(_ name: String, hasSecret: Bool = true, kind: String = "", lastResult: String? = nil, hosts: [String] = [],
                  hint: String? = nil, label: String = "") -> VaultEntry {
    VaultEntry(name: name, label: label, kind: kind, hosts: hosts, hasSecret: hasSecret, usernameHint: hint, lastResult: lastResult)
}

@Suite struct ConnectorModelTests {
    @Test func decodesEveryFieldTheServerSends() throws {
        let x = try decode("""
        {"id":"google_calendar","name":"Google Calendar","description":"d","category":"서비스","status":"available","connect":"google",
         "via":"","item":"","site":"","steps":[],"featured":true,"rank":3,"featured_regions":["KR"],
         "fallback":{"connect":"key","via":"google","item":"google-sa","blurb":"b","connected":false},
         "method":"google","account":"me@example.com","connect_state":"pending_review","note":"wait","support_url":"https://help"}
        """, as: ConnectorInfo.self)
        #expect(x.fallback?.via == "google")
        #expect(x.method == "google" && x.account == "me@example.com")
        #expect(x.connectState == "pending_review" && x.note == "wait" && x.supportUrl == "https://help")
        #expect(x.rank == 3 && x.featuredRegions == ["KR"])
        #expect(x.core.id == "google_calendar" && x.core.featured)
    }

    @Test func missingFieldsTakeDefaultsAndNullConnectIsNil() throws {
        let x = try decode(#"{"id":"stripe","connect":null,"rank":null}"#, as: ConnectorInfo.self)
        #expect(x.connect == nil && x.rank == nil && x.status == "" && x.fallback == nil)
        let blank = try decode(#"{"id":"x","connect":""}"#, as: ConnectorInfo.self)
        #expect(blank.connect == nil)
    }

    @Test func listLeavesOutRowsWithoutId() {
        let list = KeloApi.lenientList(.array([["id": "a"], ["name": "no id"], ["id": "b"]]), of: ConnectorInfo.self)
        #expect(list.map(\.id) == ["a", "b"])
    }

    @Test func googleStateAndOperatorApp() throws {
        let s = try decode(#"{"available":true,"accounts":[{"name":"g1","address":"a@b","can_write":true},{"address":"x"}],"pending_review":true}"#,
                           as: GoogleCalendarState.self)
        #expect(s.accounts.count == 1 && s.accounts[0].canWrite && s.pendingReview)
        let a = try decode(#"{"client_id":"cid","has_secret":true,"open_to_all":true}"#, as: OperatorApp.self)
        #expect(a.clientId == "cid" && a.hasSecret && a.openToAll && !a.isBroker)
    }
}

@Suite struct ConnectorLogicTests {
    @Test func waysAndPanels() {
        #expect(ConnectWay.of(connect: "device") == .code)
        #expect(ConnectWay.of(connect: "google") == .google)
        #expect(ConnectWay.of(connect: nil) == nil)
        var gmail = row("gmail", status: "connected", connect: "mail")
        gmail.method = "google"
        #expect(ConnectWay.shown(gmail) == .google)
        gmail.status = "available"
        #expect(ConnectWay.shown(gmail) == .mail)
        #expect(PanelKind.of(row("github", connect: "device")) == .device(provider: "github"))
        var ms = row("outlook_mail", connect: "device"); ms.via = "microsoft"
        #expect(PanelKind.of(ms) == .device(provider: "microsoft"))
        #expect(PanelKind.of(row("x", status: "unsupported", connect: nil)) == .none(unsupported: true))
        #expect(PanelKind.of(row("x", status: "coming_soon", connect: nil)) == .none(unsupported: false))
        #expect(ConnectorLogic.wayKey(nil) == "connector_method_none")
        #expect(ConnectorLogic.wayKey(.google) == "connector_method_google")
    }

    @Test func badgesFollowReconnectReviewThenStatus() {
        let broken = row("github", status: "connected", item: "github-api")
        #expect(ConnectorLogic.rowBadge(broken, creds: [cred("github-api", hasSecret: false)]) == .reconnect)
        #expect(ConnectorLogic.rowBadge(broken, creds: [cred("github-api", lastResult: "rejected")]) == .reconnect)
        #expect(ConnectorLogic.rowBadge(broken, creds: [cred("github-api")]) == .status(.connected))
        var review = row("gmail", connect: "mail"); review.connectState = "pending_review"
        #expect(ConnectorLogic.rowBadge(review, creds: []) == .review)
        review.status = "connected"
        #expect(ConnectorLogic.rowBadge(review, creds: []) == .status(.connected))
        #expect(ConnectorLogic.rowBadge(row("x", status: "weird"), creds: []) == nil)
        #expect(ConnectorLogic.badgeKey(.comingSoon) == "connector_status_coming_soon")
        #expect(ConnectorLogic.reconnectKey(.refused) == "connector_reconnect_refused")
    }

    @Test func accountPrefersTheServersWord() {
        var x = row("github", status: "connected", item: "github-api")
        let creds = [cred("github-api", label: "GitHub · @someone")]
        #expect(ConnectorLogic.account(x, creds: creds) == "@someone")
        x.account = "me@example.com"
        #expect(ConnectorLogic.account(x, creds: creds) == "me@example.com")
    }

    @Test func formRules() {
        #expect(!ConnectorLogic.clientIdReady("abc"))
        #expect(ConnectorLogic.clientIdReady("  Iv1.abcdef "))
        #expect(ConnectorLogic.mailReady(address: "a@b", password: "p"))
        #expect(!ConnectorLogic.mailReady(address: " ", password: "p"))
        #expect(!ConnectorLogic.loginReady(user: "u", password: ""))
        #expect(ConnectorLogic.keyFileText(Data("{}".utf8)) == "{}")
        #expect(ConnectorLogic.keyFileText(Data(count: ConnectorLogic.maxKeyFile + 1)) == nil)
        #expect(ConnectorLogic.showsSupportLink("https://x") && !ConnectorLogic.showsSupportLink("http://x"))
    }

    @Test func savedLoginForASite() {
        let items = [cred("site-1", kind: "login", hosts: ["shop.example"], hint: "kim"),
                     cred("site-2", kind: "login", hosts: ["other.example"], hint: nil)]
        #expect(ConnectorLogic.savedLogin(host: "shop.example", items: items, defaultText: "saved") == "kim")
        #expect(ConnectorLogic.savedLogin(host: "other.example", items: items, defaultText: "saved") == "saved")
        #expect(ConnectorLogic.savedLogin(host: "none.example", items: items, defaultText: "saved") == nil)
        #expect(ConnectorLogic.savedLogin(host: "", items: items, defaultText: "saved") == nil)
    }

    @Test func sectionKeysMapToAndroidNames() {
        #expect(ConnectorLogic.sectionKey("connector.group.work") == "connector_group_work")
        #expect(ConnectorLogic.sectionKey("connectors.popular") == "connectors_popular")
        #expect(ConnectorLogic.sectionKey(nil) == nil)
    }
}

@Suite struct ConnectorsStateTests {
    private func sample() -> ConnectorsState {
        var s = ConnectorsState()
        s.loaded([
            row("github", connect: "device", featured: true, rank: 1),
            row("notion", connect: "mcp", featured: true, rank: 0),
            row("stripe", status: "connected", connect: "key", item: "stripe-key"),
            row("figma", connect: "key"),
            row("linear", connect: "mcp"),
        ], creds: [cred("stripe-key")])
        return s
    }

    @Test func homeShowsPinnedThenPopularByRank() {
        let s = sample()
        #expect(!s.full)
        let secs = s.sections
        #expect(secs.map(\.key) == ["connected", "popular"])
        #expect(secs[1].items.map(\.id) == ["notion", "github"])
        #expect(secs[0].titleKey == "connectors_pinned")
        #expect(s.hiddenCount == 2 && s.showsShowAll)
        #expect(!s.showsChips && s.showsPhoneData)
    }

    @Test func fullListGroupsAndFilters() {
        var s = sample()
        s.setShowAll(true)
        #expect(s.full && s.showsBackToPopular && !s.showsShowAll)
        let keys = s.sections.map(\.key)
        #expect(keys.first == "connected")
        #expect(keys.contains("grp:WORK") && keys.contains("grp:DEV"))
        #expect(s.showsChips)
        s.filter = "grp:DEV"
        #expect(s.sections.flatMap(\.items).map(\.id) == ["github"])
        #expect(!s.showsPhoneData)
        s.setShowAll(false)
        #expect(s.filter == nil)
    }

    @Test func searchCoversEverythingAndHidesExtras() {
        var s = sample()
        s.query = "lin"
        #expect(s.searching)
        #expect(s.sections.flatMap(\.items).map(\.id) == ["linear"])
        #expect(!s.showsShowAll && !s.showsChips && !s.showsPhoneData)
        s.query = "zzz"
        #expect(s.shownCount == 0)
    }

    @Test func olderServerWithoutPopularGetsTheFullList() {
        var s = ConnectorsState()
        s.loaded([row("a"), row("b")], creds: nil)
        #expect(s.home == nil && s.full)
    }

    @Test func failuresKeepWhatIsShown() {
        var s = ConnectorsState()
        s.failedToLoad()
        #expect(s.failed && !s.offline && s.loaded)
        s = sample()
        s.failedToLoad()
        #expect(!s.failed && s.offline)
        s.loaded(s.list, creds: nil)
        #expect(!s.offline && s.creds.count == 1)
        #expect(s.find("figma")?.id == "figma" && s.find("nope") == nil && s.find(nil) == nil)
    }
}

@Suite struct DeviceFlowTests {
    private func flow(_ status: String) -> OAuthFlow { OAuthFlow(id: "f", status: status) }

    @Test func clockAsksEveryThirdSecond() {
        var c = DeviceFlowClock()
        #expect([c.tick(), c.tick(), c.tick(), c.tick(), c.tick(), c.tick()] == [false, false, true, false, false, true])
    }

    @Test func answersStopOrContinue() {
        #expect(DeviceFlowClock.after(flow("pending")) == .keepPolling)
        #expect(DeviceFlowClock.after(flow("connected")) == .connected)
        #expect(DeviceFlowClock.after(flow("denied")) == .ended)
        #expect(DeviceFlowClock.after(flow("expired")) == .ended)
    }

    @Test func countdown() {
        let d = DeviceFlowClock.deadline(nowMs: 1_000, expiresIn: 900)
        #expect(d == 901_000)
        #expect(DeviceFlowClock.countdown(deadlineMs: d, nowMs: 1_000) == "15:00")
        #expect(DeviceFlowClock.countdown(deadlineMs: d, nowMs: 2_000_000) == "0:00")
    }

    @Test func panelSteps() {
        let none = OAuthProvider(id: "github", hasClientId: false)
        #expect(DevicePanelStep.of(provider: none, flow: nil, byCode: false, editingClientId: false) == .register)
        let ready = OAuthProvider(id: "github", hasClientId: true)
        #expect(DevicePanelStep.of(provider: ready, flow: nil, byCode: false, editingClientId: false) == .startCode)
        #expect(DevicePanelStep.of(provider: ready, flow: nil, byCode: false, editingClientId: true) == .register)
        #expect(DevicePanelStep.of(provider: ready, flow: flow("pending"), byCode: false, editingClientId: false) == .waitingForCode)
        #expect(DevicePanelStep.of(provider: ready, flow: flow("denied"), byCode: false, editingClientId: false) == .startCode)
        let web = OAuthProvider(id: "github", hasClientId: true, web: true)
        #expect(DevicePanelStep.of(provider: web, flow: nil, byCode: false, editingClientId: false) == .webSignIn)
        #expect(DevicePanelStep.of(provider: web, flow: nil, byCode: true, editingClientId: false) == .startCode)
        let connected = OAuthProvider(id: "github", hasClientId: true, connected: true)
        #expect(DevicePanelStep.of(provider: connected, flow: nil, byCode: false, editingClientId: false) == .connected)
        #expect(DevicePanelStep.of(provider: connected, flow: flow("connected"), byCode: false, editingClientId: false) == .connected)
        #expect(DevicePanelStep.of(provider: connected, flow: flow("pending"), byCode: false, editingClientId: false) == .waitingForCode)
    }
}

@Suite struct GoogleAndMailPanelTests {
    private func gcal(pending: Bool = false, fallback: ConnectorFallback? = nil) -> ConnectorInfo {
        var c = row("google_calendar", connect: "google")
        if pending { c.connectState = "pending_review" }
        c.fallback = fallback
        return c
    }

    @Test func googleButtons() {
        let acct = GoogleCalendarAccount(name: "g", address: "a@b", canWrite: true)
        let bad = GoogleCalendarAccount(name: "h", address: "c@d", problem: true)
        var p = GooglePanel(gcal(), state: GoogleCalendarState(available: true))
        #expect(p.button == .signIn && p.primary && !p.showsReviewNote)
        p = GooglePanel(gcal(), state: GoogleCalendarState(available: true, accounts: [acct]))
        #expect(p.button == .addAccount && !p.primary)
        p = GooglePanel(gcal(), state: GoogleCalendarState(available: true, accounts: [acct, bad]))
        #expect(p.button == .signInAgain && p.primary)
        p = GooglePanel(gcal(), state: GoogleCalendarState(available: false, reason: "no client"))
        #expect(p.button == nil && p.showsNotReady)
        p = GooglePanel(gcal(), state: GoogleCalendarState(available: false, accounts: [acct]))
        #expect(!p.showsNotReady)
    }

    @Test func googleReviewHidesTheButton() {
        var p = GooglePanel(gcal(pending: true), state: GoogleCalendarState(available: true))
        #expect(p.pending && p.button == nil && p.showsReviewNote)
        p = GooglePanel(gcal(), state: GoogleCalendarState(available: true, accounts: [GoogleCalendarAccount(name: "g")], pendingReview: true))
        #expect(p.pending && !p.showsReviewNote)
        let fb = ConnectorFallback(connect: "key", via: "google", item: "sa")
        #expect(!GooglePanel(gcal(pending: true, fallback: fb), state: GoogleCalendarState()).showsFallback)
        var fbOn = fb; fbOn.connected = true
        #expect(GooglePanel(gcal(pending: true, fallback: fbOn), state: GoogleCalendarState()).showsFallback)
        #expect(GooglePanel(gcal(fallback: fb), state: GoogleCalendarState()).showsFallback)
    }

    @Test func mailForms() {
        let gmail = row("gmail", connect: "mail")
        #expect(MailPanelForm.of(gmail, accounts: 0, adding: false, byPassword: false, googleWeb: true) == .googleSignIn)
        #expect(MailPanelForm.of(gmail, accounts: 0, adding: false, byPassword: true, googleWeb: true) == .passwordForm)
        #expect(MailPanelForm.of(gmail, accounts: 1, adding: false, byPassword: false, googleWeb: true) == .accountsOnly)
        var review = gmail; review.connectState = "pending_review"
        #expect(MailPanelForm.of(review, accounts: 0, adding: false, byPassword: false, googleWeb: true) == .reviewNote)
        let imap = row("imap_email", connect: "mail")
        #expect(MailPanelForm.of(imap, accounts: 0, adding: false, byPassword: false, googleWeb: true) == .passwordForm)
        #expect(MailPanelForm.of(imap, accounts: 2, adding: true, byPassword: false, googleWeb: false) == .passwordForm)
        #expect(MailPanelForm.defaultProvider(connectorId: "gmail", providers: []) == "gmail")
        #expect(MailPanelForm.defaultProvider(connectorId: "imap_email", providers: [MailProvider(id: "daum")]) == "daum")
    }
}

@Suite struct PhoneDataTests {
    @Test func iPhoneShowsOnlyCalendarAndContacts() {
        #expect(PhoneDataRow.shown() == [.calendar, .contacts, .dialer])
        #expect(PhoneDataRow.shown(from: ["messages", "call_log", "notifications", "health"]).isEmpty)
        #expect(PhoneDataRow.calendar.titleKey == "device_calendar")
    }
}

@Suite struct ConnectorStringKeyTests {
    /// Every string name the rules hand to the views exists in English and Korean.
    @MainActor @Test func dynamicKeysExist() {
        var names: [String] = Badge.allCases.map(ConnectorLogic.badgeKey)
        names += [Reconnect.missing, .refused, .server].map(ConnectorLogic.reconnectKey)
        names += [nil, ConnectWay.code, .signIn, .google, .mail, .key, .browser].map(ConnectorLogic.wayKey)
        names += ConnectorGroup.allCases.compactMap { ConnectorLogic.sectionKey($0.labelKey) }
        names += ["connector.group.reconnect", "connectors.pinned", "connectors.popular"].compactMap(ConnectorLogic.sectionKey)
        names += PhoneDataRow.allCases.map(\.titleKey)
        names += SecretLoginMode.allCases.flatMap { ["vault_mode_\($0.rawValue)", "vault_mode_\($0.rawValue)_help"] }
        for lang in ["en", "ko"] {
            let s = Strings(code: lang)
            for n in names { #expect(s.raw(n) != nil, "missing \(n) in \(lang)") }
        }
    }
}
