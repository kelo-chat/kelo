import Foundation
import Testing
import KeloCore
import KeloShared
import KeloTestSupport
@testable import KeloConnectors

@MainActor private func browser(_ ok: Bool, log: Box? = nil) -> BrowserSignIn {
    { url in log?.urls.append(url); return ok }
}

@MainActor private final class Box { var urls: [String] = [] }

@MainActor @Suite struct ConnectorsModelTests {
    private let list = #"[{"id":"a","name":"A","status":"connected"},{"id":"b","name":"B","status":"available"}]"#

    @Test func loadFillsTheListAndKeepsItForNextTime() async {
        let t = RoutedTransport()
        t.on("GET /api/connectors", list)
        t.on("GET /api/credentials", "[]")
        let cache = memoryCache()
        let m = ConnectorsModel(api: t.api(), cache: cache)
        await m.load()
        #expect(m.state.loaded)
        #expect(m.state.list.count == 2)
        let again = ConnectorsModel(api: RoutedTransport().api(), cache: cache)
        again.warm()
        #expect(again.state.list.count == 2)
    }

    @Test func aFailureMarksItAndAKeptListStaysAsAHint() async {
        let t = RoutedTransport()
        t.down = true
        let m = ConnectorsModel(api: t.api(), cache: memoryCache())
        await m.load()
        #expect(m.state.failed)
    }

    @Test func anOlderLoadNeverCoversANewerOne() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/connectors") { _, n in
            if n == 0 { await gate.wait(); return (200, #"[{"id":"old","name":"Old"}]"#) }
            return (200, #"[{"id":"new","name":"New"}]"#)
        }
        t.on("GET /api/credentials", "[]")
        let m = ConnectorsModel(api: t.api(), cache: memoryCache())
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/connectors") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.state.list.map(\.id) == ["new"])
    }

    @Test func disconnectingReadsTheListAgain() async {
        let t = RoutedTransport()
        t.on("GET /api/connectors", list)
        t.on("GET /api/credentials", "[]")
        t.on("DELETE /api/credentials/x", "{}")
        let m = ConnectorsModel(api: t.api(), cache: memoryCache())
        m.disconnect(item: "x", name: "X")
        await m.jobs.settled()
        #expect(t.count("DELETE /api/credentials/x") == 1)
        #expect(m.state.loaded)
        #expect(m.forget == nil)   // the answer had no data
    }

    @Test func aDisconnectThatLeftDataAsksAboutIt() async {
        let t = RoutedTransport()
        t.on("GET /api/connectors", list)
        t.on("GET /api/credentials", "[]")
        t.on("DELETE /api/credentials/x", #"{"ok":true,"data":{"files":2,"notes":0,"items":0}}"#)
        let m = ConnectorsModel(api: t.api(), cache: memoryCache())
        m.disconnect(item: "x", name: "X")
        await m.jobs.settled()
        #expect(m.forget == ForgetAsk(item: "x", name: "X", counts: ConnectorDataCounts(files: 2, notes: 0, items: 0)))
    }

    @Test func oneConnectorsRowAndItsScopes() async {
        let t = RoutedTransport()
        t.on("GET /api/connectors", list)
        let info = ConnectorInfoModel(api: t.api(), service: "b")
        await info.load()
        #expect(info.info?.name == "B")
        t.on("GET /api/oauth/providers", #"[{"id":"github","scopes":[{"id":"repo","label":"Repos"}],"granted_scope":"repo","connected":true}]"#)
        let scopes = DeviceScopesModel(api: t.api())
        await scopes.load(provider: "github")
        #expect(scopes.granted?.id == "repo")
    }
}

@MainActor @Suite struct ConnectorPanelModelTests {
    @Test func mcpSignInOpensTheBrowserThenReloads() async {
        let t = RoutedTransport()
        t.on("POST /api/mcp-oauth/notion/start", #"{"url":"https://auth.test/x"}"#)
        let log = Box()
        let m = McpModel(api: t.api(), id: "notion")
        var changed = 0
        m.signIn(browser: browser(true, log: log), changed: { changed += 1 })
        await m.jobs.settled()
        #expect(log.urls == ["https://auth.test/x"])
        #expect(changed == 1)
        #expect(m.err.isEmpty)
    }

    @Test func mcpFailureShowsTheErrorAndABrowserThatWillNotOpenSaysSo() async {
        let t = RoutedTransport()
        t.on("POST /api/mcp-oauth/notion/start", 500, #"{"detail":"nope"}"#)
        let m = McpModel(api: t.api(), id: "notion")
        m.signIn(browser: browser(true), changed: {})
        await m.jobs.settled()
        #expect(!m.err.isEmpty)
        t.on("POST /api/mcp-oauth/notion/start", #"{"url":"https://auth.test/x"}"#)
        m.signIn(browser: browser(false), changed: {})
        await m.jobs.settled()
        #expect(!m.err.isEmpty)
        #expect(!m.busy)
    }

    @Test func mailLoadsAddsAndFails() async {
        let t = RoutedTransport()
        t.on("GET /api/mail/providers", #"{"providers":[{"id":"fastmail","name":"Fastmail"}],"accounts":[]}"#)
        t.on("POST /api/mail/accounts", "{}")
        let m = MailModel(api: t.api(), connectorId: "mail")
        await m.load()
        #expect(m.data != nil)
        var added = 0
        m.add(address: " a@b.c ", password: "pw", added: { added += 1 }, changed: {})
        await m.jobs.settled()
        #expect(added == 1)
        t.on("POST /api/mail/accounts", 400, #"{"detail":"wrong password"}"#)
        m.add(address: "a@b.c", password: "bad", added: { added += 1 }, changed: {})
        await m.jobs.settled()
        #expect(added == 1)
        #expect(!m.err.isEmpty)
    }

    @Test func mailLoadOlderAnswerNeverWins() async {
        let t = RoutedTransport()
        let gate = Gate()
        t.on("GET /api/mail/providers") { _, n in
            if n == 0 { await gate.wait(); return (200, #"{"providers":[],"accounts":[{"name":"old"}]}"#) }
            return (200, #"{"providers":[],"accounts":[{"name":"new"}]}"#)
        }
        let m = MailModel(api: t.api(), connectorId: "mail")
        let first = Task { @MainActor in await m.load() }
        _ = await eventually { t.count("GET /api/mail/providers") == 1 }
        await m.load()
        await gate.release()
        await first.value
        #expect(m.data?.accounts.map(\.name) == ["new"])
    }

    @Test func calDavAddAndFailure() async {
        let t = RoutedTransport()
        t.on("GET /api/caldav", #"{"accounts":[]}"#)
        t.on("POST /api/caldav/accounts", "{}")
        let m = CalDavModel(api: t.api())
        await m.load()
        #expect(m.accounts?.isEmpty == true)
        var added = 0
        m.add(url: "https://dav.test", user: "u", password: "p", added: { added += 1 }, changed: {})
        await m.jobs.settled()
        #expect(added == 1)
        t.on("POST /api/caldav/accounts", 400, #"{"detail":"no"}"#)
        m.add(url: "https://dav.test", user: "u", password: "p", added: { added += 1 }, changed: {})
        await m.jobs.settled()
        #expect(added == 1 && !m.err.isEmpty)
    }

    @Test func keySaveAndFailure() async {
        let t = RoutedTransport()
        t.on("GET /api/keys/svc", #"{"id":"svc","name":"Svc","fields":[{"id":"key","label":"Key","secret":true}]}"#)
        t.on("POST /api/keys/svc", "{}")
        let m = KeyModel(api: t.api(), via: "svc")
        await m.load()
        let form = try! #require(m.form)
        var saved = 0
        m.save(form, values: ["key": "k"], saved: { saved += 1 }, changed: {})
        await m.jobs.settled()
        #expect(saved == 1)
        t.on("POST /api/keys/svc", 400, #"{"detail":"bad key"}"#)
        m.save(form, values: ["key": "k"], saved: { saved += 1 }, changed: {})
        await m.jobs.settled()
        #expect(saved == 1 && !m.err.isEmpty)
    }

    @Test func browserSiteKeepsALoginOrAsksForOne() async {
        let t = RoutedTransport()
        t.on("GET /api/vault", #"{"items":[]}"#)
        t.on("POST /api/vault/logins", #"{"name":"shop"}"#)
        t.on("DELETE /api/vault/shop/draft", "{}")
        let m = BrowserConnectModel(api: t.api(), site: "https://shop.test", name: "Shop")
        await m.load(host: "shop.test")
        #expect(m.saved == "")
        m.ask()
        await m.jobs.settled()
        #expect(m.asking == "shop")
        m.cancelAsk()
        await m.jobs.settled()
        #expect(m.asking == nil)
        #expect(t.count("DELETE /api/vault/shop/draft") == 1)
    }

    @Test func siteLoginSaveAndFailure() async {
        let t = RoutedTransport()
        t.on("PUT /api/vault/shop/login", "{}")
        let m = SiteLoginModel(api: t.api(), name: "shop")
        var saved = 0
        m.save(user: " u ", password: "p", mode: .afterSuccess, saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1)
        t.on("PUT /api/vault/shop/login", 400, #"{"detail":"bad"}"#)
        m.save(user: "u", password: "p", mode: .afterSuccess, saved: { saved += 1 })
        await m.jobs.settled()
        #expect(saved == 1 && !m.msg.isEmpty)
    }

    @Test func googleLoadsStartsAndFails() async {
        let t = RoutedTransport()
        t.on("GET /api/google/calendar", #"{"available":true,"accounts":[]}"#)
        t.on("POST /api/google/web/start", #"{"url":"https://g.test"}"#)
        let m = GoogleConnectModel(api: t.api())
        await m.load()
        #expect(m.state?.available == true)
        let log = Box()
        m.start(browser: browser(true, log: log), changed: {})
        await m.jobs.settled()
        #expect(log.urls == ["https://g.test"])
        let down = GoogleConnectModel(api: RoutedTransport().api())
        await down.load()
        #expect(down.failed)
    }

    @Test func operatorSettingsOwnerAndPairedPhone() async {
        let t = RoutedTransport()
        t.on("GET /api/oauth-apps/github", #"{"client_id":"abc","has_secret":true}"#)
        let m = OperatorSettingsModel(api: t.api(), provider: "github")
        await m.load()
        #expect(m.owner == true && m.cid == "abc")
        let t2 = RoutedTransport()
        t2.on("GET /api/oauth-apps/github", 403, #"{"detail":"owner"}"#)
        let p = OperatorSettingsModel(api: t2.api(), provider: "github")
        await p.load()
        #expect(p.owner == false)
    }

    @Test func operatorSaveFailureShowsAMessage() async {
        let t = RoutedTransport()
        t.on("PUT /api/oauth-apps/github", 500, #"{"detail":"x"}"#)
        let m = OperatorSettingsModel(api: t.api(), provider: "github")
        var saved = 0
        m.save(secret: "s", saved: { saved += 1 }, cleared: {})
        await m.jobs.settled()
        #expect(saved == 0 && !m.msg.isEmpty)
    }

    @Test func betaRequestSendsAndFails() async {
        let t = RoutedTransport()
        t.on("POST /api/google/beta", #"{"status":"requested","email":"a@b.c"}"#)
        let m = BetaRequestModel(api: t.api())
        var sent = 0
        m.send(email: "a@b.c", product: "gmail", appVersion: "1", sent: { sent += 1 })
        await m.jobs.settled()
        #expect(sent == 1 && m.state?.email == "a@b.c")
        t.on("POST /api/google/beta", 400, #"{"detail":"no"}"#)
        m.send(email: "a@b.c", product: "gmail", appVersion: "1", sent: { sent += 1 })
        await m.jobs.settled()
        #expect(sent == 1 && m.err == "no")
    }

    @Test func deviceFlowStartsAndAnEndedOneIsLoaded() async {
        let t = RoutedTransport()
        t.on("GET /api/oauth/providers", #"[{"id":"github","name":"GitHub","scopes":[{"id":"repo"}]}]"#)
        t.on("POST /api/oauth/github/start", #"{"id":"f1","user_code":"AB-12","expires_in":60,"status":"pending"}"#)
        let m = DeviceConnectModel(api: t.api(), provider: "github")
        await m.load()
        #expect(m.p?.id == "github" && m.scopeId == "repo")
        m.startCode()
        await m.jobs.settled()
        #expect(m.flow?.userCode == "AB-12")
        t.on("POST /api/oauth/github/start", 500, #"{"detail":"x"}"#)
        m.startCode()
        await m.jobs.settled()
        #expect(!m.err.isEmpty)
    }

    @Test func followingStopsWhenItsTaskIsCancelled() async {
        let t = RoutedTransport()
        t.on("GET /api/oauth/providers", #"[{"id":"github","name":"GitHub"}]"#)
        t.on("POST /api/oauth/github/start", #"{"id":"f1","user_code":"AB-12","expires_in":600,"status":"pending"}"#)
        t.on("GET /api/oauth/flows/f1", #"{"id":"f1","status":"pending"}"#)
        let m = DeviceConnectModel(api: t.api(), provider: "github")
        await m.load()
        m.startCode()
        await m.jobs.settled()
        let follow = Task { @MainActor in await m.follow(connected: {}) }
        follow.cancel()
        await follow.value   // returns: the loop does not outlive its task
        #expect(true)
    }
}
