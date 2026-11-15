import Foundation
import Observation
import SwiftUI

/// The four bottom tabs (Android shell/MainShell.kt TABS). The work page opens from the header, not from a tab.
public enum KeloTab: String, CaseIterable, Hashable, Sendable {
    case chat, goals, connectors, my

    public var icon: KeloIcon {
        switch self {
        case .chat: .chat
        case .goals: .goal
        case .connectors: .plug
        case .my: .user
        }
    }

    @MainActor public var label: String {
        switch self {
        case .chat: L(R.string.tab_chat)
        case .goals: L(R.string.tab_goals)
        case .connectors: L(R.string.tab_connectors)
        case .my: L(R.string.tab_my)
        }
    }
}

/// Where the work page should open (a tapped notification; Android notify/OpenTarget).
public enum WorkStart: Equatable, Sendable {
    case task(Int64), approvals, schedule(Int64), activity, schedules, watchers
}

/// A place in the app something asked to open (a notification, a chat card, another tab). Android OpenTarget.
public enum OpenTarget: Equatable, Sendable {
    case chat
    case chatAt(Int64)
    /// The chat, scrolled to a secure entry's card and briefly marked (a row in 답이 필요해요)
    case entry(String)
    case wallet
    case browserTab(String)
    case ownTab
    case work(WorkStart)
    /// 마이 → 운영자 (a beta request for Google sign-in came in)
    case operatorPage
    /// 마이 → 모델 (a newer CLI is out); the provider of that CLI, whose box the page shows ("" = just the page)
    case modelSettings(String)
    /// 마이 → 서버 업데이트 (a server update notice: newer version, a nightly run's result)
    case serverUpdate
    /// 마이 → Kelo 서버 자원 (storage is filling up)
    case storage
    /// 마이 → Kelo 서버 자원 → 정리하기 (the storage notification's action)
    case storageCleanup
    /// Keyguard: Kelo is locked after a restart: the chat with the unlock check started (keyguard_locked)
    case unlock

    /// The text a local notification carries in its userInfo ("chat", "task:12", ...), the same encoding as Android.
    public var encoded: String {
        switch self {
        case .chat: "chat"
        case .chatAt(let id): "chat:\(id)"
        case .entry(let id): "entry:\(id)"
        case .wallet: "wallet"
        case .browserTab(let id): "tab:\(id)"
        case .ownTab: "owntab"
        case .work(.task(let id)): "task:\(id)"
        case .work(.approvals): "approvals"
        case .work(.schedule(let id)): "schedule:\(id)"
        case .work(.activity): "activity"
        case .work(.schedules): "schedules"
        case .work(.watchers): "watchers"
        case .operatorPage: "operator"
        case .modelSettings(let p): p.isEmpty ? "model-settings" : "model-settings:\(p)"
        case .serverUpdate: "server-update"
        case .storage: "storage"
        case .storageCleanup: "storage-cleanup"
        case .unlock: "unlock"
        }
    }

    public static func decode(_ s: String) -> OpenTarget? {
        let parts = s.split(separator: ":", maxSplits: 1).map(String.init)
        let head = parts.first ?? ""
        let rest = parts.count > 1 ? parts[1] : ""
        switch head {
        case "chat": return rest.isEmpty ? .chat : Int64(rest).map { .chatAt($0) }
        case "wallet": return .wallet
        case "entry": return rest.isEmpty ? nil : .entry(rest)
        case "tab": return rest.isEmpty ? nil : .browserTab(rest)
        case "owntab": return .ownTab
        case "task": return Int64(rest).map { .work(.task($0)) }
        case "approvals": return .work(.approvals)
        case "schedule": return Int64(rest).map { .work(.schedule($0)) }
        case "activity": return .work(.activity)
        case "schedules": return .work(.schedules)
        case "watchers": return .work(.watchers)
        case "operator": return .operatorPage
        case "model-settings": return .modelSettings(rest)
        case "server-update": return .serverUpdate
        case "storage": return .storage
        case "storage-cleanup": return .storageCleanup
        case "unlock": return .unlock
        default: return nil
        }
    }
}

/// My page sub pages another place can ask for (Android MY_PAGE_*).
public enum MyPageRequest: String, Sendable {
    case wallet, agent, model, notifications, vault, devices, permissions, documents, usage, language, `operator`
    /// The server update page (owner only; the page itself shows nothing for another device)
    case serverUpdate
    /// Kelo 서버 자원, and its cleanup screen (a storage warning and its 정리하기 action)
    case resources, cleanup
    /// The wallet's card form, behind the wallet's own lock (a card number the chat held back goes there)
    case walletAdd
    /// 마이 → 보안 → Keyguard, and the same page opened at the recovery code (from the locked banner)
    case keyguard, keyguardCode
}

/// What a tap on a bottom tab does (Android shell/TabNav.kt).
public enum TabTap: Equatable, Sendable {
    /// Another tab: go there (its last place is kept).
    case switchTo
    /// The open tab while a sub page is showing: back to the tab's start.
    case toRoot
    /// The open tab at its start: the page scrolls to the top / closes what is open.
    case reset

    public static func of(current: KeloTab, depth: Int, tapped: KeloTab) -> TabTap {
        if current != tapped { return .switchTo }
        return depth > 0 ? .toRoot : .reset
    }
}

/// The app's navigation state: the open tab, each tab's stack, the work page and live view over everything, and requests from one
/// place to another. Pages read and change it through the environment (`@Environment(AppModel.self)` → `app.router`).
@MainActor @Observable
public final class Router {
    public var tab: KeloTab = .chat
    /// Each tab's NavigationStack path; a non-empty path hides the floating header and the tab bar (sub pages are full screen).
    public var paths: [KeloTab: NavigationPath] = [:]
    /// The work page over whatever tab is showing (opened from the header).
    public var workOpen = false
    /// Where the work page should open, handed over once.
    public var workStart: WorkStart?
    /// A browser tab's live view over everything.
    public var liveTab: String?
    /// The live view should take its tab over by itself once (opened with "Solve it" on a human check).
    public var liveTakeControl = false
    /// The connectors tab opened at one connector.
    public var connectorOpen: String?
    /// A My sub page asked for.
    public var myPageRequest: MyPageRequest?
    /// With a request for 마이 → 모델: the CLI (its provider) a newer-CLI notice is about; the page shows its box.
    public var modelCli: String?
    /// A chat message to scroll to.
    public var chatFocus: Int64?
    /// A secure entry's card to scroll to and mark for a moment (a row in 답이 필요해요).
    public var chatEntryFocus: String?
    /// Counts taps on the tab that is already open at its start (pages watch their own entry to scroll to the top).
    public private(set) var reselects: [KeloTab: Int] = [:]
    /// Who asked the header and tab bar to step aside (a full-screen page, the keyboard on the chat).
    public private(set) var chromeAskers: Set<String> = []

    public init() {}

    public static let keyboardAsker = "keyboard"

    public func path(_ t: KeloTab) -> Binding<NavigationPath> {
        Binding(get: { self.paths[t] ?? NavigationPath() }, set: { self.paths[t] = $0 })
    }

    public func depth(_ t: KeloTab) -> Int { paths[t]?.count ?? 0 }

    public var headerHidden: Bool { depth(tab) > 0 || chromeAskers.contains { $0 != Self.keyboardAsker } }
    public var barHidden: Bool { depth(tab) > 0 || !chromeAskers.isEmpty }

    public func hideChrome(_ key: String, _ hidden: Bool) {
        if hidden { chromeAskers.insert(key) } else { chromeAskers.remove(key) }
    }

    /// A bottom tab tapped.
    public func tap(_ t: KeloTab) {
        switch TabTap.of(current: tab, depth: depth(tab), tapped: t) {
        case .switchTo:
            var tx = Transaction()
            tx.disablesAnimations = true
            withTransaction(tx) { tab = t }
        case .toRoot:
            paths[t] = NavigationPath()
            if t == .connectors { connectorOpen = nil }
        case .reset:
            reselects[t, default: 0] += 1
        }
    }

    public func go(_ t: KeloTab) {
        var tx = Transaction()
        tx.disablesAnimations = true
        withTransaction(tx) { tab = t }
    }

    public func openConnector(_ id: String) {
        connectorOpen = id
        go(.connectors)
    }

    public func openMyPage(_ page: MyPageRequest) {
        myPageRequest = page
        go(.my)
    }

    public func openWork(_ start: WorkStart? = nil) {
        workStart = start
        workOpen = true
    }

    /// A place asked for from outside (a notification) or another screen.
    /// The keyguard_locked notification was opened: the locked banner starts the unlock check by itself, once.
    public var unlockRequested = false

    public func open(_ target: OpenTarget) {
        switch target {
        case .chat: liveTab = nil; go(.chat)
        case .chatAt(let id): liveTab = nil; chatFocus = id; go(.chat)
        case .entry(let id): liveTab = nil; workOpen = false; chatEntryFocus = id; go(.chat)
        case .wallet: liveTab = nil; openMyPage(.wallet)
        case .browserTab(let id): openLive(id)   // a notification never takes control
        case .ownTab: openLive("owner")
        case .work(let start): liveTab = nil; openWork(start)
        case .operatorPage: liveTab = nil; openMyPage(.operator)
        case .modelSettings(let p): liveTab = nil; modelCli = p.isEmpty ? nil : p; openMyPage(.model)
        case .serverUpdate: liveTab = nil; openMyPage(.serverUpdate)
        case .storage: liveTab = nil; openMyPage(.resources)
        case .storageCleanup: liveTab = nil; openMyPage(.cleanup)
        case .unlock: liveTab = nil; workOpen = false; unlockRequested = true; go(.chat)
        }
    }

    /// A browser tab's live view over everything; `takeControl`: take the tab over once the first picture is there.
    public func openLive(_ id: String, takeControl: Bool = false) {
        liveTakeControl = takeControl
        liveTab = id
    }

    public func closeLive() {
        liveTab = nil
        liveTakeControl = false
    }

    public func reset() {
        tab = .chat
        paths = [:]
        workOpen = false
        workStart = nil
        liveTab = nil
        liveTakeControl = false
        connectorOpen = nil
        myPageRequest = nil
        chatFocus = nil
        chatEntryFocus = nil
        chromeAskers = []
    }
}

public extension View {
    /// Hides the floating header and the tab bar while this view is on screen and `hidden` is true (Android HideShellChrome).
    func hidesShellChrome(_ key: String, _ hidden: Bool = true) -> some View { modifier(HideChromeModifier(key: key, hidden: hidden)) }

    /// Runs `action` when the user taps `tab` while already at its start (Android OnTabReselect).
    func onTabReselect(_ tab: KeloTab, perform action: @escaping () -> Void) -> some View {
        modifier(TabReselectModifier(tab: tab, action: action))
    }
}

private struct HideChromeModifier: ViewModifier {
    let key: String
    let hidden: Bool
    @Environment(AppModel.self) private var app
    func body(content: Content) -> some View {
        content
            .onAppear { app.router.hideChrome(key, hidden) }
            .onChange(of: hidden) { _, h in app.router.hideChrome(key, h) }
            .onDisappear { app.router.hideChrome(key, false) }
    }
}

private struct TabReselectModifier: ViewModifier {
    let tab: KeloTab
    let action: () -> Void
    @Environment(AppModel.self) private var app
    func body(content: Content) -> some View {
        content.onChange(of: app.router.reselects[tab] ?? 0) { _, _ in action() }
    }
}
