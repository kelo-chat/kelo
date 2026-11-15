import Foundation
import Testing
@testable import KeloCore

/// Port of android connectors/ConnectorRulesTest.kt. R.string ids became `labelKey` strings.
@Suite struct ConnectorRulesTests {
    private let list = [
        Connector(id: "github", name: "GitHub", description: "코드 저장소와 이슈", category: "개발", status: "connected", connect: "device"),
        Connector(id: "notion", name: "Notion", description: "문서와 데이터베이스", category: "문서", status: "available", connect: "mcp"),
        Connector(id: "gmail", name: "Gmail", description: "메일 찾기와 보내기", category: "메일", status: "available", connect: "mail"),
        Connector(id: "linear", name: "Linear", description: "이슈 관리", category: "개발", status: "coming_soon"),
        Connector(id: "naver", name: "네이버", description: "브라우저로 쓰는 사이트", category: "웹", status: "browser", connect: "browser"),
    ]

    @Test func anEmptyQueryShowsEverything() {
        #expect(ConnectorRules.filter(list, query: "") == list)
    }

    @Test func queryMatchesNameOrDescriptionCaseIgnored() {
        #expect(ConnectorRules.filter(list, query: "git").map(\.id) == ["github"])
        #expect(ConnectorRules.filter(list, query: "GITHUB").map(\.id) == ["github"])
        #expect(ConnectorRules.filter(list, query: "이슈").map(\.id) == ["github", "linear"])
        #expect(ConnectorRules.filter(list, query: "데이터").map(\.id) == ["notion"])
    }

    @Test func queryIsNotTrimmed() {
        // "name description" holds one space between them, so a space alone still matches everything
        #expect(ConnectorRules.filter(list, query: " ").count == list.count)
        #expect(ConnectorRules.filter(list, query: "  ").isEmpty)
    }

    @Test func groupsKeepTheOrderCategoriesFirstAppearIn() {
        let g = ConnectorRules.groups(list)
        #expect(g.map(\.0) == ["개발", "문서", "메일", "웹"])
        #expect(g.first?.1.map(\.id) == ["github", "linear"])
    }

    @Test func statusMapsToTheBadgeLabels() {
        #expect(ConnectorRules.badge("connected") == .connected)
        #expect(ConnectorRules.badge("connected")?.labelKey == "connector.status.connected")
        #expect(ConnectorRules.badge("available")?.labelKey == "connector.status.available")
        #expect(ConnectorRules.badge("browser")?.labelKey == "connector.status.browser")
        #expect(ConnectorRules.badge("autofill")?.labelKey == "connector.status.autofill")
        #expect(ConnectorRules.badge("coming_soon")?.labelKey == "connector.status.coming_soon")
        #expect(ConnectorRules.badge("disabled")?.labelKey == "connector.status.disabled")
        #expect(ConnectorRules.badge("unsupported")?.labelKey == "connector.status.unsupported")
        #expect(ConnectorRules.badge("something_new") == nil)
    }

    @Test(arguments: ["connected", "disabled", "coming_soon", "available", "browser", "autofill", "unsupported"])
    func everyStatusTheServerSendsHasABadge(_ status: String) {
        #expect(ConnectorRules.badge(status)?.rawValue == status)
    }

    @Test func findFollowsTheListAndGivesNilWhenGone() {
        #expect(ConnectorRules.find(list, id: "gmail")?.id == "gmail")
        #expect(ConnectorRules.find(list, id: "zoom") == nil)
        #expect(ConnectorRules.find(list, id: nil) == nil)
    }

    @Test func countdownText() {
        #expect(ConnectorRules.mmss(0) == "0:00")
        #expect(ConnectorRules.mmss(9) == "0:09")
        #expect(ConnectorRules.mmss(899) == "14:59")
        #expect(ConnectorRules.mmss(-5) == "0:00")
        #expect(ConnectorRules.secondsLeft(deadlineMs: 20_000, nowMs: 10_000) == 10)
        #expect(ConnectorRules.secondsLeft(deadlineMs: 10_000, nowMs: 20_000) == 0)
    }

    @Test func endedFlows() {
        #expect(ConnectorRules.ended(nil) == nil)
        #expect(ConnectorRules.ended(OAuthFlow(id: "1", status: "pending")) == nil)
        #expect(ConnectorRules.ended(OAuthFlow(id: "1", status: "connected")) == nil)
        #expect(ConnectorRules.ended(OAuthFlow(id: "1", status: "denied"))?.status == "denied")
        #expect(ConnectorRules.ended(OAuthFlow(id: "1", status: "expired"))?.status == "expired")
    }

    @Test func hostKeyAccountKeyReadiness() {
        #expect(ConnectorRules.host("https://www.naver.com/login") == "www.naver.com")
        #expect(ConnectorRules.host("not a url") == "")
        #expect(ConnectorRules.keyAccount("Google · bot@proj.iam.gserviceaccount.com") == "bot@proj.iam.gserviceaccount.com")
        #expect(ConnectorRules.keyAccount("Zoom") == "")
        let form = KeyForm(id: "zoom", fields: [KeyField(id: "a"), KeyField(id: "b", secret: true)])
        #expect(!ConnectorRules.keyReady(form, values: ["a": "x", "b": " "]))
        #expect(ConnectorRules.keyReady(form, values: ["a": "x", "b": "y"]))
    }

    @Test func gmailConnectorListsOnlyGmail() {
        let accounts = [MailAccount(name: "m1", provider: "gmail"), MailAccount(name: "m2", provider: "naver")]
        let providers = [MailProvider(id: "gmail"), MailProvider(id: "naver"), MailProvider(id: "daum")]
        #expect(ConnectorRules.mailAccounts(connectorId: "gmail", accounts).map(\.name) == ["m1"])
        #expect(ConnectorRules.mailAccounts(connectorId: "mail", accounts).count == 2)
        #expect(ConnectorRules.mailChoices(connectorId: "gmail", providers).map(\.id) == ["gmail"])
        #expect(ConnectorRules.mailChoices(connectorId: "mail", providers).count == 3)
    }

    @Test func serverAnswersDecodeWithDefaults() throws {
        let d = JSONDecoder()
        let l = try d.decode([Connector].self, from: Data(
            #"[{"id":"x","name":"X","description":"d","category":"c","status":"available","connect":null,"via":"","item":"","site":"","extra":1}]"#.utf8))
        #expect(l[0].connect == nil)
        #expect(l[0].steps.isEmpty)
        let f = try d.decode(OAuthFlow.self, from: Data(
            #"{"id":"f","provider":"github","status":"pending","message":"","account":"","user_code":"AB-CD","verification_uri":"https://github.com/login/device","expires_in":900}"#.utf8))
        #expect(f.userCode == "AB-CD")
        #expect(f.expiresIn == 900)
        #expect(f.pending)
    }
}

/// Port of android connectors/ConnectorListTest.kt. R.string ids became `labelKey` strings.
@Suite struct ConnectorListTests {
    private let list = [
        Connector(id: "github", name: "GitHub", description: "저장소와 이슈", category: "서비스", status: "connected", connect: "device", item: "github-api"),
        Connector(id: "notion", name: "Notion", description: "문서와 데이터베이스", category: "서비스", status: "available", connect: "mcp", item: "notion-mcp"),
        Connector(id: "stripe", name: "Stripe", description: "결제 내역", category: "서비스", status: "connected", connect: "mcp", item: "stripe-mcp"),
        Connector(id: "agoda", name: "Agoda", description: "숙소 예약", category: "서비스", status: "browser", connect: "browser"),
        Connector(id: "spotify", name: "Spotify", description: "음악", category: "서비스", status: "browser", connect: "browser"),
        Connector(id: "brand_new", name: "Brand New", description: "새 서비스", category: "서비스", status: "available", connect: "mcp", item: "brand-new"),
    ]
    private let creds = [
        VaultEntry(name: "github-api", label: "GitHub · @someone", kind: "api", hasSecret: true),
        VaultEntry(name: "stripe-mcp", label: "Stripe", kind: "api", hasSecret: true, lastResult: "failed", lastResultDetail: "401 invalid token"),
        VaultEntry(name: "brand-new", label: "Brand New", kind: "api", hasSecret: false),
        VaultEntry(name: "login-agoda", label: "Agoda", kind: "login", hasSecret: false),
    ]

    @Test func knownIdsFallIntoLocalGroupsTheRestIntoOther() {
        #expect(ConnectorGroups.of("github") == .dev)
        #expect(ConnectorGroups.of("notion") == .work)
        #expect(ConnectorGroups.of("stripe") == .money)
        #expect(ConnectorGroups.of("agoda") == .shopping)
        #expect(ConnectorGroups.of("spotify") == .media)
        #expect(ConnectorGroups.of("brand_new") == .other)
        #expect(ConnectorGroup.other.labelKey == "connector.group.other")
    }

    @Test func reconnectComesFromTheVaultItem() {
        #expect(ConnectorList.reconnect(list[0], creds: creds) == nil)          // fine
        #expect(ConnectorList.reconnect(list[2], creds: creds) == .refused)     // refused last time
        #expect(ConnectorList.reconnect(list[5], creds: creds) == .missing)     // item without a value
        #expect(ConnectorList.reconnect(list[1], creds: creds) == nil)          // never connected
        #expect(ConnectorList.reconnect(list[3], creds: creds) == nil)          // no item at all
        var needs = list[1]
        needs.status = "needs_reconnect"
        #expect(ConnectorList.reconnect(needs, creds: []) == .server)
    }

    @Test func aSiteLoginWaitingToBeFilledIsNotABrokenConnection() {
        let x = Connector(id: "box", name: "Box", description: "", category: "", status: "browser", connect: "browser", item: "login-agoda")
        #expect(ConnectorList.reconnect(x, creds: creds) == nil)
    }

    @Test func thoseNeedingANewConnectionComeFirstThenTheConnectedOnesThenLocalGroupsInOrder() {
        let s = ConnectorList.sections(list, creds: creds)
        #expect(s.map(\.key) == ["reconnect", "connected", "grp:WORK", "grp:MEDIA", "grp:SHOPPING"])
        #expect(s.first?.items.map(\.id) == ["stripe", "brand_new"])
        #expect(s.first?.labelKey == "connector.group.reconnect")
        #expect(s[1].items.map(\.id) == ["github"])
        #expect(s[1].labelKey == "connectors.pinned")
    }

    @Test func aFilterKeepsOneGroupPinnedOnesIncluded() {
        let dev = ConnectorList.sections(list, creds: creds, filter: "grp:DEV")
        #expect(dev.map(\.key) == ["connected"])
        #expect(dev.count == 1)
        #expect(dev.first?.items.map(\.id) == ["github"])
        let work = ConnectorList.sections(list, creds: creds, filter: "grp:WORK")
        #expect(work.map(\.key) == ["grp:WORK"])
    }

    @Test func filterChipsFollowTheGroupsPresent() {
        #expect(ConnectorList.filters(list).map(\.key) == ["grp:WORK", "grp:DEV", "grp:MONEY", "grp:MEDIA", "grp:SHOPPING", "grp:OTHER"])
    }

    @Test func serverCategoriesWinWhenThereIsMoreThanOne() {
        let l = [Connector(id: "a", name: "A", category: "개발"), Connector(id: "b", name: "B", category: "문서"), Connector(id: "c", name: "C", category: "개발")]
        let s = ConnectorList.sections(l)
        #expect(s.map(\.key) == ["cat:개발", "cat:문서"])
        #expect(s[0].title == "개발")
        #expect(s[0].labelKey == nil)
        #expect(s[0].items.map(\.id) == ["a", "c"])
    }

    @Test func searchCoversNameDescriptionAndIdTrimmed() {
        #expect(ConnectorList.search(list, query: " git ").map(\.id) == ["github"])
        #expect(ConnectorList.search(list, query: "brand_").map(\.id) == ["brand_new"])
        #expect(ConnectorList.search(list, query: "숙소").map(\.id) == ["agoda"])
        #expect(ConnectorList.search(list, query: "").count == list.count)
    }

    @Test func pinnedMeansConnectedOrNeedingANewConnection() {
        #expect(list.filter { ConnectorList.pinned($0, creds: creds) }.map(\.id) == ["github", "stripe", "brand_new"])
    }

    @Test func accountComesFromTheLabelAfterTheDot() {
        #expect(ConnectorList.account(list[0], creds: creds) == "@someone")
        #expect(ConnectorList.account(list[2], creds: creds) == "")
        #expect(ConnectorList.account(list[3], creds: creds) == "")
    }

    @Test func connectionMethods() {
        #expect(ConnectorList.method("device") == .code)
        #expect(ConnectorList.method("mcp") == .signIn)
        #expect(ConnectorList.method("mail") == .mail)
        #expect(ConnectorList.method("key") == .key)
        #expect(ConnectorList.method("browser") == .browser)
        #expect(ConnectorList.method("autofill") == .autofill)
        #expect(ConnectorList.method(nil) == nil)
    }

    @Test func tileLetters() {
        #expect(ConnectorList.initials("GitHub") == "G")
        #expect(ConnectorList.initials("Zoho CRM") == "ZC")
        #expect(ConnectorList.initials("1Password") == "1")
        #expect(ConnectorList.initials("Cal.com") == "C")
        #expect(ConnectorList.initials("Atlassian") == "A")
        #expect(ConnectorList.initials("tl;dv") == "T")
        #expect(ConnectorList.initials("") == "?")
    }

    @Test func tileHueIsStableAndOnTheWheel() {
        let ids = ["github", "notion", "stripe", "zoom", "x", ""]
        for id in ids {
            let h = ConnectorList.hue(id)
            #expect((0...359).contains(h))
            #expect(h == ConnectorList.hue(id))
        }
        #expect(Set(ids.map(ConnectorList.hue)).count > 3)
    }

    @Test func theOpeningViewIsConnectedFirstThenPopularByRank() throws {
        let l = list.map { c -> Connector in
            var c = c
            switch c.id {
            case "agoda": c.featured = true; c.rank = 3
            case "spotify": c.featured = true; c.rank = 1
            case "github": c.featured = true; c.rank = 0     // connected: pinned, not repeated under popular
            default: break
            }
            return c
        }
        let s = try #require(ConnectorList.home(l, creds: creds))
        #expect(s.map(\.key) == ["reconnect", "connected", "popular"])
        #expect(s.last?.items.map(\.id) == ["spotify", "agoda"])
        #expect(s.last?.labelKey == "connectors.popular")
        #expect(ConnectorList.hiddenCount(l, creds: creds) == 1)                 // notion is neither connected nor popular
        #expect(ConnectorList.home(list, creds: creds) == nil)                   // an older server marks nothing: the full list
    }

    /// The server's catalog (catalog.py, 152 services) as of this version.
    private let catalog = "adobe_workfront agoda ahrefs airtable amplitude apify apollo apple_healthkit asana atlassian attio basecamp beehiiv betterstack bitly box brex buildkite calendly canva clickhouse clickup close cloudflare_observability cloudflare_workers cloudinary coinmarketcap contentful customer_io datadog deepl descript digitalocean dropbox egnyte evernote exa expedia expo facebook_cli fathom figma firecrawl fireflies framer freshbooks github gitlab gmail google_calendar google_contacts google_docs google_drive google_forms google_health_connect google_sheets google_slides google_tasks grab_mcp grafana_cloud greenhouse hugging_face ibkr imap_email incident_io instacart instagram jotform kit krisp launchdarkly linear lucid mailerlite make mapbox mercury messenger_read meta_catalog_search metricool mintlify miro mixpanel monday motherduck neon netlify notion onepassword opentable otter outlook_calendar outlook_contacts outlook_mail pandadoc paypal philips_hue places planetscale posthog postman prisma pulumi quickbooks railway ramp read_ai resend restream sanity sentry shop-pay shopback_mcp shopify shopify-ucp slack spotify stripe stripe-link supabase superhuman_docs superhuman_mail tavily threads ticketmaster ticktick tiktok_ads tldv todoist trello typefully v0 vercel webflow webull wix wordpress_com workable workos zapier zoho_books zoho_crm zoho_desk zoho_projects zoho_show zoom naver kakao coupang baemin toss daangn".split(separator: " ").map(String.init)

    @Test func everyCatalogServiceHasALocalGroup() {
        #expect(catalog.count == 152)
        let missing = catalog.filter { !ConnectorGroups.known($0) }
        #expect(missing.isEmpty, "not grouped: \(missing)")
        #expect(ConnectorGroups.of("coupang") == .shopping)
        #expect(ConnectorGroups.of("toss") == .money)
        #expect(ConnectorGroups.of("naver") == .work)
    }

    @Test(arguments: ["haveibeenpwned", "kahoot", "padlet", "oreilly", "splice", "tomorrow_io", "turkish_airlines", "coindesk", "morningstar",
                      "craft", "whimsical", "cal_com", "roam_research", "sonos", "tally"])
    func removedServicesAreNoLongerMapped(_ id: String) {
        #expect(!ConnectorGroups.known(id))
    }

    @Test func onlyThePermissionAConnectionWasGivenIsShown() {
        let scopes = [OAuthScope(id: "all", label: "Mail, calendar and contacts", detail: "Read and send"), OAuthScope(id: "read", label: "Read only")]
        var outlook = OAuthProvider(id: "microsoft", connected: true, scopes: scopes, grantedScope: "read")
        #expect(ConnectorRules.grantedScope(outlook)?.label == "Read only")
        outlook.grantedScope = "all"
        #expect(ConnectorRules.grantedScope(outlook)?.id == "all")
        outlook.grantedScope = ""
        #expect(ConnectorRules.grantedScope(outlook) == nil)
        outlook.grantedScope = "gone"
        #expect(ConnectorRules.grantedScope(outlook) == nil)
        outlook.grantedScope = "read"
        outlook.connected = false
        #expect(ConnectorRules.grantedScope(outlook) == nil)
        #expect(ConnectorRules.grantedScope(nil) == nil)
    }
}
