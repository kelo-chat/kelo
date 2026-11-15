import Foundation

// The connectors tab's rules apart from the UI (Android connectors/ConnectorRules.kt). Words on screen are Localizable keys
// (`labelKey`), filled in with Kelo's own wording when the SwiftUI app is made.

/// A connector's badge by status; nil for a status this app does not know (nothing is shown).
public enum Badge: String, CaseIterable, Sendable {
    case connected, disabled, comingSoon = "coming_soon", available, browser, autofill, unsupported
    public var labelKey: String { "connector.status.\(rawValue)" }
}

public enum ConnectorRules {
    /// `name description` containing the query (case ignored).
    public static func filter(_ list: [Connector], query: String) -> [Connector] {
        let q = query.lowercased()
        return list.filter { "\($0.name) \($0.description)".lowercased().contains(q) || q.isEmpty }
    }

    /// The shown connectors grouped by category, in the order each category first appears.
    public static func groups(_ shown: [Connector]) -> [(String, [Connector])] {
        var order: [String] = []
        var by: [String: [Connector]] = [:]
        for c in shown {
            if by[c.category] == nil { order.append(c.category) }
            by[c.category, default: []].append(c)
        }
        return order.map { ($0, by[$0]!) }
    }

    public static func badge(_ status: String) -> Badge? { Badge(rawValue: status) }

    /// The connector with this id in a fresh list (the open detail follows reloads; gone means back to the list).
    public static func find(_ list: [Connector], id: String?) -> Connector? { id.flatMap { i in list.first { $0.id == i } } }

    /// Seconds as m:ss.
    public static func mmss(_ seconds: Int64) -> String {
        let s = max(seconds, 0)
        return "\(s / 60):" + String(format: "%02d", s % 60)
    }

    /// Whole seconds left until `deadlineMs`, never below zero.
    public static func secondsLeft(deadlineMs: Int64, nowMs: Int64) -> Int64 { Int64((Double(max(deadlineMs - nowMs, 0)) / 1000).rounded()) }

    /// The one permission a connected code-connected service was given (its `granted_scope` among its `scopes`); nil when it is not
    /// connected or the choice is not known, and then the detail page shows no permission section (the connect panel has its picker).
    public static func grantedScope(_ p: OAuthProvider?) -> OAuthScope? {
        guard let p, p.connected, !p.grantedScope.isEmpty else { return nil }
        return p.scopes.first { $0.id == p.grantedScope }
    }

    /// A site's host name ("" when it is not a URL).
    public static func host(_ site: String) -> String { URLComponents(string: site)?.host ?? "" }

    /// A device-flow attempt that ended without connecting (denied, expired, error, cancelled), for the error line.
    public static func ended(_ flow: OAuthFlow?) -> OAuthFlow? {
        guard let flow, flow.status != "pending", flow.status != "connected" else { return nil }
        return flow
    }

    /// The key form's account: the part after " · " in the label.
    public static func keyAccount(_ label: String) -> String {
        let parts = label.components(separatedBy: " · ")
        return parts.count > 1 ? parts[1] : ""
    }

    /// Every field of a key form filled in.
    public static func keyReady(_ form: KeyForm, values: [String: String]) -> Bool {
        form.fields.allSatisfy { !(values[$0.id] ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    /// The mailboxes a mail connector lists: only Gmail ones on the Gmail connector.
    public static func mailAccounts(connectorId: String, _ accounts: [MailAccount]) -> [MailAccount] {
        connectorId == "gmail" ? accounts.filter { $0.provider == "gmail" } : accounts
    }

    public static func mailChoices(connectorId: String, _ providers: [MailProvider]) -> [MailProvider] {
        connectorId == "gmail" ? providers.filter { $0.id == "gmail" } : providers
    }
}

/// The groups the list falls into when the server gives every connector the same category: a small map of known ids, anything new
/// or unknown under "other".
public enum ConnectorGroup: String, CaseIterable, Sendable {
    case work = "WORK", dev = "DEV", money = "MONEY", media = "MEDIA", shopping = "SHOPPING", other = "OTHER"
    public var labelKey: String { "connector.group.\(rawValue.lowercased())" }
}

/// A part of the list: a title (a Localizable key, or the server's own category text) and its connectors.
public struct ConnectorSection: Equatable, Sendable {
    public let key: String
    public let labelKey: String?
    public let title: String
    public var items: [Connector]
}

/// How a connector is connected, in the app's words.
public enum ConnectMethod: String, Sendable {
    case code, signIn = "sign_in", mail, key, browser, autofill
    public var labelKey: String { "connector.method.\(rawValue)" }
}

/// Why a connection needs doing again.
public enum Reconnect: String, Sendable {
    /// The stored token is gone.
    case missing
    /// The service turned the stored token down the last time it was used.
    case refused
    /// The server itself says so.
    case server
    public var textKey: String { "connector.reconnect.\(rawValue)" }
}

public enum ConnectorGroups {
    /// Every service of the server's catalog (catalog.py), so none falls under "other" by accident (same table as Android).
    static let map: [String: ConnectorGroup] = {
        var m: [String: ConnectorGroup] = [:]
        func add(_ g: ConnectorGroup, _ ids: String) { ids.split(separator: " ").forEach { m[String($0)] = g } }
        add(.work, "adobe_workfront airtable apollo asana atlassian attio basecamp box calendly clickup close deepl " +
            "dropbox egnyte evernote fathom fireflies gmail google_calendar google_contacts google_docs google_drive google_forms google_sheets " +
            "google_slides google_tasks greenhouse imap_email jotform krisp linear lucid make miro monday naver notion onepassword otter " +
            "outlook_calendar outlook_contacts outlook_mail pandadoc read_ai slack superhuman_docs superhuman_mail ticktick tldv " +
            "todoist trello workable zapier zoho_crm zoho_desk zoho_projects zoho_show zoom")
        add(.dev, "amplitude apify betterstack buildkite clickhouse cloudflare_observability cloudflare_workers cloudinary " +
            "contentful datadog digitalocean exa expo firecrawl github gitlab grafana_cloud hugging_face incident_io launchdarkly mapbox " +
            "mintlify mixpanel motherduck neon netlify planetscale posthog postman prisma pulumi railway resend sanity sentry supabase " +
            "tavily v0 vercel webflow wix wordpress_com workos")
        add(.money, "brex coinmarketcap freshbooks ibkr mercury paypal quickbooks ramp shop-pay stripe stripe-link toss webull zoho_books")
        add(.media, "ahrefs beehiiv bitly canva customer_io descript facebook_cli figma framer instagram kit mailerlite " +
            "messenger_read metricool restream spotify threads tiktok_ads typefully")
        add(.shopping, "agoda baemin coupang daangn expedia grab_mcp instacart kakao meta_catalog_search opentable places " +
            "shopback_mcp shopify shopify-ucp ticketmaster")
        add(.other, "apple_healthkit google_health_connect philips_hue")
        return m
    }()

    public static func known(_ id: String) -> Bool { map[id] != nil }
    public static func of(_ id: String) -> ConnectorGroup { map[id] ?? .other }
}

/// The list's tiles, search, order and the detail's facts.
public enum ConnectorList {
    static let reconnectStatuses: Set<String> = ["needs_reconnect", "reconnect", "expired", "error"]

    /// Whether a connector needs connecting again: the server says so, or its keychain item exists but has no stored value any
    /// more, or the service refused it the last time.
    public static func reconnect(_ x: Connector, creds: [VaultEntry]) -> Reconnect? {
        if reconnectStatuses.contains(x.status) { return .server }
        if x.item.trimmingCharacters(in: .whitespaces).isEmpty { return nil }
        guard let cred = creds.first(where: { $0.name == x.item }) else { return nil }
        if cred.isLogin { return nil }   // a site login waiting to be filled in is not a broken connection
        if cred.isBad { return .refused }
        if !cred.hasSecret { return .missing }
        return nil
    }

    /// Search across name, description and id (so "google_" finds every Google one).
    public static func search(_ list: [Connector], query: String) -> [Connector] {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return list.filter { q.isEmpty || "\($0.name) \($0.description) \($0.id)".lowercased().contains(q) }
    }

    /// Connected (or needing to be connected again): pinned on top of the list.
    public static func pinned(_ x: Connector, creds: [VaultEntry]) -> Bool { x.status == "connected" || reconnect(x, creds: creds) != nil }

    /// The server's category when it sends more than one, else the local group.
    public static func groupKey(_ x: Connector, serverCategories: Bool) -> String {
        serverCategories ? "cat:" + x.category : "grp:" + ConnectorGroups.of(x.id).rawValue
    }

    static func usesServerCategories(_ list: [Connector]) -> Bool {
        Set(list.map(\.category).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }).count > 1
    }

    /// The filter chips: one per group that has connectors, in the list's order.
    public static func filters(_ list: [Connector]) -> [ConnectorSection] {
        sections(list, creds: [], pin: false).map { var s = $0; s.items = []; return s }
    }

    /// The list in parts: those to connect again, then the connected ones (both pinned on top), then the server's categories when it
    /// sends more than one, else the local groups in a fixed order. Inside a part the server's order is kept. `filter` (a group key)
    /// keeps only that group, in the pinned parts too.
    public static func sections(_ shown: [Connector], creds: [VaultEntry] = [], filter: String? = nil, pin: Bool = true) -> [ConnectorSection] {
        let server = usesServerCategories(shown)
        let inFilter = filter.map { f in shown.filter { groupKey($0, serverCategories: server) == f } } ?? shown
        let broken = pin ? inFilter.filter { reconnect($0, creds: creds) != nil } : []
        let connected = pin ? inFilter.filter { $0.status == "connected" && reconnect($0, creds: creds) == nil } : []
        let rest = inFilter.filter { c in !broken.contains(c) && !connected.contains(c) }
        var out: [ConnectorSection] = []
        if !broken.isEmpty { out.append(ConnectorSection(key: "reconnect", labelKey: "connector.group.reconnect", title: "", items: broken)) }
        if !connected.isEmpty { out.append(ConnectorSection(key: "connected", labelKey: "connectors.pinned", title: "", items: connected)) }
        if server {
            for (cat, items) in ConnectorRules.groups(rest) { out.append(ConnectorSection(key: "cat:\(cat)", labelKey: nil, title: cat, items: items)) }
        } else {
            let byGroup = Dictionary(grouping: rest) { ConnectorGroups.of($0.id) }
            for g in ConnectorGroup.allCases {
                if let items = byGroup[g] { out.append(ConnectorSection(key: "grp:\(g.rawValue)", labelKey: g.labelKey, title: "", items: items)) }
            }
        }
        return out
    }

    /// What the tab shows when it opens: the user's own connections pinned on top, then the popular services by rank. An older server
    /// that marks nothing as popular gets the full list instead (nil).
    public static func home(_ list: [Connector], creds: [VaultEntry] = []) -> [ConnectorSection]? {
        guard list.contains(where: \.featured) else { return nil }
        let pinnedSections = sections(list.filter { pinned($0, creds: creds) }, creds: creds)
        let popular = list.filter { $0.featured && !pinned($0, creds: creds) }.sorted {
            ($0.rank ?? Int.max) != ($1.rank ?? Int.max) ? ($0.rank ?? Int.max) < ($1.rank ?? Int.max) : $0.name < $1.name
        }
        return pinnedSections + (popular.isEmpty ? [] : [ConnectorSection(key: "popular", labelKey: "connectors.popular", title: "", items: popular)])
    }

    /// How many services the full list holds beyond what the opening view shows.
    public static func hiddenCount(_ list: [Connector], creds: [VaultEntry] = []) -> Int {
        list.filter { !$0.featured && !pinned($0, creds: creds) }.count
    }

    public static func method(_ connect: String?) -> ConnectMethod? {
        switch connect {
        case "device": return .code
        case "mcp": return .signIn
        case "mail": return .mail
        case "key": return .key
        case "browser": return .browser
        case "autofill": return .autofill
        default: return nil
        }
    }

    /// Who is connected, when the keychain item's label says (e.g. "GitHub · @someone" -> "@someone").
    public static func account(_ x: Connector, creds: [VaultEntry]) -> String {
        guard !x.item.trimmingCharacters(in: .whitespaces).isEmpty else { return "" }
        let label = creds.first { $0.name == x.item }?.label ?? ""
        guard let r = label.range(of: " · ") else { return "" }
        return String(label[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// One or two letters for a service's tile: "GitHub" -> "G", "Zoho CRM" -> "ZC", "1Password" -> "1".
    public static func initials(_ name: String) -> String {
        let seps: Set<Character> = [" ", "-", "_", "·", "(", ")"]
        let words = name.split(whereSeparator: { seps.contains($0) }).filter { w in
            !w.trimmingCharacters(in: .whitespaces).isEmpty && (w.first!.isLetter || w.first!.isNumber)
        }
        guard let first = words.first?.first else {
            let t = name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(1).uppercased()
            return t.isEmpty ? "?" : t
        }
        if words.count == 2, let second = words[1].first, second.isLetter { return first.uppercased() + second.uppercased() }
        return first.uppercased()
    }

    /// A hue (0..<360) for a service's tile, the same every time for the same id (FNV-1a over UTF-16 units, as on Android). No brand
    /// colours and no logos: a coloured square with letters is Kelo's own.
    public static func hue(_ id: String) -> Double {
        var h = Int32(bitPattern: 0x811C9DC5)
        for unit in id.utf16 { h = (h ^ Int32(unit)) &* 0x01000193 }
        return Double((UInt32(bitPattern: h) >> 1) % 360)
    }

    /// A tile's colour as HSL (hue, saturation, lightness): dark enough in both themes for white letters.
    public static func tileHSL(hue: Double, dark: Bool) -> (Double, Double, Double) { (hue, 0.42, dark ? 0.34 : 0.38) }
}
