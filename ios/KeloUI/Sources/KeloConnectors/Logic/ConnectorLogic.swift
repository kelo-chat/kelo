import Foundation
import KeloCore

// The connectors tab's rules apart from the views (Android ConnectorRules.kt / ConnectorsViewModel.kt). KeloCore's ConnectorList
// does the grouping, search, home view and reconnect checks; this adds what the newer server fields need and the panels' rules.

/// How a connector is (or would be) connected, with the Google sign-in Android added after KeloCore's ConnectMethod.
public enum ConnectWay: String, Sendable, Equatable {
    case code, signIn, google, mail, caldav, key, browser

    public static func of(connect: String?) -> ConnectWay? {
        switch connect {
        case "device": .code
        case "mcp": .signIn
        case "google": .google
        case "mail": .mail
        case "caldav": .caldav
        case "key": .key
        case "browser": .browser
        default: nil
        }
    }

    /// How this one IS connected: the server says so when a service has two ways (Gmail: Google sign-in or app password).
    public static func current(_ x: ConnectorInfo) -> ConnectWay? {
        switch x.method {
        case "google": .google
        case "password": x.connect == "caldav" ? .caldav : .mail
        case "key": .key
        default: of(connect: x.connect)
        }
    }

    /// The detail page's 연결 방법 row: how it is connected when connected, else how it would be.
    public static func shown(_ x: ConnectorInfo) -> ConnectWay? { x.status == "connected" ? current(x) : of(connect: x.connect) }
}

/// Which panel the detail page shows (Android ConnectPanel's `when (c.connect)`).
public enum PanelKind: Equatable, Sendable {
    case device(provider: String)
    case key
    case browser
    case mcp
    case mail
    case caldav
    case google
    /// No way to connect here: the server's reason for an unsupported one, else "not yet".
    case none(unsupported: Bool)

    public static func of(_ x: ConnectorInfo) -> PanelKind {
        switch x.connect {
        case "device": .device(provider: x.via.isEmpty ? x.id : x.via)
        case "key": .key
        case "browser": .browser
        case "mcp": .mcp
        case "mail": .mail
        case "caldav": .caldav
        case "google": .google
        default: .none(unsupported: x.status == "unsupported")
        }
    }
}

public enum ConnectorLogic {
    /// Gmail / Google Calendar while the Google sign-in waits for Google's review and nothing is connected yet.
    public static func pendingReview(_ x: ConnectorInfo) -> Bool { x.connectState == "pending_review" && x.status != "connected" }

    /// Who is connected: the server's own word first, else the keychain item's label ("GitHub · @someone" -> "@someone").
    public static func account(_ x: ConnectorInfo, creds: [VaultEntry]) -> String {
        let a = x.account.trimmingCharacters(in: .whitespacesAndNewlines)
        return a.isEmpty ? ConnectorList.account(x.core, creds: creds) : a
    }

    public static func reconnect(_ x: ConnectorInfo, creds: [VaultEntry]) -> Reconnect? { ConnectorList.reconnect(x.core, creds: creds) }

    /// The badge on a row: reconnect, review pill, or the status badge (nil = nothing shown for an unknown status).
    public enum RowBadge: Equatable, Sendable { case reconnect, review, status(Badge) }

    public static func rowBadge(_ x: ConnectorInfo, creds: [VaultEntry]) -> RowBadge? {
        if reconnect(x, creds: creds) != nil { return .reconnect }
        if pendingReview(x) { return .review }
        return ConnectorRules.badge(x.status).map { .status($0) }
    }

    /// The Android string name of a status badge (KeloCore's `Badge.labelKey` predates the generated R names).
    public static func badgeKey(_ b: Badge) -> String {
        switch b {
        case .connected: "connector_status_connected"
        case .disabled: "connector_status_disabled"
        case .comingSoon: "connector_status_coming_soon"
        case .available: "connector_status_available"
        case .browser, .autofill: "connector_status_browser"
        case .unsupported: "connector_status_unsupported"
        }
    }

    /// A section's or chip's title key in R.string (KeloCore gives dotted keys).
    public static func sectionKey(_ labelKey: String?) -> String? {
        switch labelKey {
        case nil: nil
        case "connector.group.reconnect": "connector_group_reconnect"
        case "connectors.pinned": "connectors_pinned"
        case "connectors.popular": "connectors_popular"
        case let k?: k.replacingOccurrences(of: ".", with: "_")
        }
    }

    public static func reconnectKey(_ r: Reconnect) -> String { "connector_reconnect_\(r.rawValue)" }

    public static func wayKey(_ w: ConnectWay?) -> String {
        switch w {
        case nil: "connector_method_none"
        case .code: "connector_method_code"
        case .signIn: "connector_method_sign_in"
        case .google: "connector_method_google"
        case .mail: "connector_method_mail"
        case .caldav: "connector_method_caldav"
        case .key: "connector_method_key"
        case .browser: "connector_method_browser"
        }
    }

    /// A client id long enough to be one (the device-flow registration form).
    public static func clientIdReady(_ s: String) -> Bool { s.trimmingCharacters(in: .whitespacesAndNewlines).count >= 6 }

    /// The mail form can be sent.
    public static func mailReady(address: String, password: String) -> Bool {
        !address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !password.isEmpty
    }

    /// The largest key file read from the document picker (a Google service account JSON is a few KB).
    public static let maxKeyFile = 64 * 1024

    /// A picked key file's text, nil when too large or not UTF-8.
    public static func keyFileText(_ data: Data) -> String? {
        guard data.count <= maxKeyFile else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// The secure login form can be sent.
    public static func loginReady(user: String, password: String) -> Bool {
        !user.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !password.isEmpty
    }

    /// The browser site's saved login line: the username hint of a login kept for the site's host, nil when none is kept.
    public static func savedLogin(host: String, items: [VaultEntry], defaultText: String) -> String? {
        guard !host.isEmpty, let it = items.first(where: { $0.isLogin && $0.hosts.contains(host) }) else { return nil }
        let hint = it.usernameHint?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return hint.isEmpty ? defaultText : hint
    }

    /// Only https support links are shown (an operator's link).
    public static func showsSupportLink(_ url: String) -> Bool { url.hasPrefix("https://") }
}

/// What the 커넥터 list shows (Android ConnectorsState + the list's `remember` blocks).
public struct ConnectorsState: Equatable, Sendable {
    public var list: [ConnectorInfo] = []
    public var loaded = false
    public var failed = false
    /// The last refresh did not reach the server: the list shown is the one kept from before.
    public var offline = false
    public var query = ""
    /// The chosen filter chip: a group key, or nil for all.
    public var filter: String?
    /// The full list with its group chips (else: connected and popular services only).
    public var showAll = false
    public var creds: [VaultEntry] = []

    public init(list: [ConnectorInfo] = [], loaded: Bool = false) { self.list = list; self.loaded = loaded }

    /// One part of the list, with the full rows.
    public struct Section: Equatable, Sendable, Identifiable {
        public let key: String
        /// An R.string name, or nil when `title` is the server's own category text.
        public let titleKey: String?
        public let title: String
        public let items: [ConnectorInfo]
        public var id: String { key }
    }

    private var byId: [String: ConnectorInfo] { Dictionary(list.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a }) }
    private var cores: [Connector] { list.map(\.core) }

    public var searching: Bool { !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    public var home: [ConnectorSection]? { ConnectorList.home(cores, creds: creds) }
    /// The full list: asked for, or an older server that marks nothing as popular.
    public var full: Bool { showAll || home == nil }
    public var shownCount: Int { ConnectorList.search(cores, query: query).count }

    public var sections: [Section] {
        let shown = ConnectorList.search(cores, query: query)
        let raw: [ConnectorSection]
        if searching { raw = ConnectorList.sections(shown, creds: creds) }
        else if !full { raw = home ?? [] }
        else { raw = ConnectorList.sections(shown, creds: creds, filter: filter) }
        let ids = byId
        return raw.map { s in
            Section(key: s.key, titleKey: ConnectorLogic.sectionKey(s.labelKey), title: s.title, items: s.items.compactMap { ids[$0.id] })
        }
    }

    public var chips: [Section] {
        ConnectorList.filters(cores).map { Section(key: $0.key, titleKey: ConnectorLogic.sectionKey($0.labelKey), title: $0.title, items: []) }
    }

    /// The group chips show only on the full list, not while searching, and only when there is more than one group.
    public var showsChips: Bool { full && !searching && chips.count > 1 }
    public var showsBackToPopular: Bool { full && !searching && home != nil }
    public var hiddenCount: Int { ConnectorList.hiddenCount(cores, creds: creds) }
    public var showsShowAll: Bool { !full && !searching && hiddenCount > 0 }
    /// The phone's own data rows show under the list when nothing narrows it.
    public var showsPhoneData: Bool { !searching && filter == nil }

    public func find(_ id: String?) -> ConnectorInfo? { id.flatMap { byId[$0] } }

    public mutating func setShowAll(_ on: Bool) {
        showAll = on
        if !on { filter = nil }
    }

    /// A fresh list from the server.
    public mutating func loaded(_ l: [ConnectorInfo], creds c: [VaultEntry]?) {
        list = l; loaded = true; failed = false; offline = false
        if let c { creds = c }
    }

    /// The refresh failed: with nothing shown it is an error, else the kept list stays with the offline hint.
    public mutating func failedToLoad() {
        loaded = true; failed = list.isEmpty; offline = !list.isEmpty
    }
}


/// The device-flow panel's clock (Android DeviceConnect's LaunchedEffect): the countdown ticks every second, the server is asked
/// every third second, and polling stops when the attempt ends.
public struct DeviceFlowClock: Equatable, Sendable {
    public static let tickSeconds: UInt64 = 1
    public static let pollEvery = 3

    public private(set) var ticks = 0
    public init() {}

    /// One second passed; true when this tick should ask the server.
    public mutating func tick() -> Bool {
        ticks += 1
        return ticks % DeviceFlowClock.pollEvery == 0
    }

    /// What to do with a fresh answer from the server.
    public enum Next: Equatable, Sendable { case keepPolling, connected, ended }

    public static func after(_ flow: OAuthFlow) -> Next {
        if flow.pending { return .keepPolling }
        return flow.status == "connected" ? .connected : .ended
    }

    /// The deadline (ms since 1970) of an attempt that expires in `expiresIn` seconds.
    public static func deadline(nowMs: Int64, expiresIn: Int) -> Int64 { nowMs + Int64(expiresIn) * 1000 }

    /// The countdown line's m:ss.
    public static func countdown(deadlineMs: Int64, nowMs: Int64) -> String {
        ConnectorRules.mmss(ConnectorRules.secondsLeft(deadlineMs: deadlineMs, nowMs: nowMs))
    }
}

/// The device-flow panel's screens, in order (Android DeviceConnect's branches).
public enum DevicePanelStep: Equatable, Sendable {
    case connected
    case waitingForCode
    /// The web sign-in button first (GitHub with an OAuth app), the code as the fallback.
    case webSignIn
    /// No client id yet, or changing it.
    case register
    case startCode

    public static func of(provider: OAuthProvider, flow: OAuthFlow?, byCode: Bool, editingClientId: Bool) -> DevicePanelStep {
        if provider.connected && (flow == nil || flow?.status == "connected") { return .connected }
        if let flow, flow.pending { return .waitingForCode }
        if provider.web && !byCode { return .webSignIn }
        if !provider.hasClientId || editingClientId { return .register }
        return .startCode
    }
}

/// The Google Calendar panel's choices (Android GoogleConnect).
public struct GooglePanel: Equatable, Sendable {
    public enum Button: Equatable, Sendable { case signIn, signInAgain, addAccount }

    public let pending: Bool
    /// The review note (and the phone calendar as the other way) shows.
    public let showsReviewNote: Bool
    /// The sign-in button and its label; nil when there is none (waiting for review, or the server cannot use it).
    public let button: Button?
    /// The main (filled) button rather than the plain one.
    public let primary: Bool
    /// The server's reason the Google sign-in cannot be used, shown when nothing is connected.
    public let showsNotReady: Bool
    /// The service account as the other way.
    public let showsFallback: Bool

    public init(_ c: ConnectorInfo, state s: GoogleCalendarState) {
        pending = ConnectorLogic.pendingReview(c) || s.pendingReview
        let empty = s.accounts.isEmpty
        let problem = s.accounts.contains { $0.problem }
        showsReviewNote = pending && (empty || problem)
        if pending || !s.available {
            button = nil
        } else if empty {
            button = .signIn
        } else if problem {
            button = .signInAgain
        } else {
            button = .addAccount
        }
        primary = empty || problem
        showsNotReady = !pending && !s.available && empty
        if let fb = c.fallback { showsFallback = fb.connect == "key" && (!pending || fb.connected) } else { showsFallback = false }
    }
}

/// The Gmail / mail panel's choices (Android MailConnect).
public enum MailPanelForm: Equatable, Sendable {
    /// Mailboxes are connected and no new one is being added.
    case accountsOnly
    /// Gmail while Google's review is pending: the note, and the app password as a small link.
    case reviewNote
    case googleSignIn
    case passwordForm

    public static func of(_ c: ConnectorInfo, accounts: Int, adding: Bool, byPassword: Bool, googleWeb: Bool) -> MailPanelForm {
        let form = accounts == 0 || adding
        guard form else { return .accountsOnly }
        let gmail = c.id == "gmail"
        if gmail && ConnectorLogic.pendingReview(c) && !byPassword { return .reviewNote }
        if gmail && googleWeb && !byPassword { return .googleSignIn }
        return .passwordForm
    }

    /// The provider picked first: Gmail on the Gmail connector, else the first the server offers.
    public static func defaultProvider(connectorId: String, providers: [MailProvider]) -> String {
        if connectorId == "gmail" { return "gmail" }
        return providers.first?.id ?? "naver"
    }
}

/// The phone's own data under the list (Android "이 기기에서", device/DeviceSection). An iPhone app can read only the calendar
/// (EventKit) and contacts (Contacts framework); texts, call history, notifications, the dialer and Health Connect have no iOS
/// API an app may use, so they are never shown here. The rows open 마이 → 권한, where the sync lives.
public enum PhoneDataRow: String, CaseIterable, Sendable, Identifiable {
    case calendar, contacts, dialer
    public var id: String { rawValue }

    /// Android's DeviceFeature keys, in Android's order.
    public static let androidKeys = ["calendar", "contacts", "messages", "dialer", "notifications", "call_log", "health"]

    /// The rows an iPhone shows from Android's list (anything without an iOS equivalent is left out).
    public static func shown(from keys: [String] = androidKeys) -> [PhoneDataRow] { keys.compactMap(PhoneDataRow.init(rawValue:)) }

    public var titleKey: String {
        switch self {
        case .calendar: "device_calendar"
        case .contacts: "device_contacts"
        case .dialer: "device_dialer"
        }
    }
}
