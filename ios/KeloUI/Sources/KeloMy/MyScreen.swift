import SwiftUI
import KeloCore
import KeloShared

/// 마이 tab (Android my/MyScreen.kt): one grouped settings list (account and devices, the agent, the model and its usage,
/// connections, security, data, the app, and for the server's owner its operator settings). It shows only what belongs to the
/// person (their own uploads; never the assistant's skills or notes). Sub pages push full screen (the shell hides its header and
/// tabs while the stack is not empty). The vault opens only after the phone's own check (Face ID, Touch ID or the passcode).
public struct MyScreen: View {
    @Environment(AppModel.self) private var app
    public init() {}

    public var body: some View {
        NavigationStack(path: app.router.path(.my)) {
            MyHome()
                .keloShellInsets()
                .navigationDestination(for: MyRoute.self) { route in
                    MyDestination(route: route)
                }
        }
    }
}

/// Pushes and pops the My tab's stack.
@MainActor struct MyNav {
    let app: AppModel
    func push(_ r: MyRoute) { app.router.paths[.my, default: NavigationPath()].append(r) }
    func pop() {
        guard var p = app.router.paths[.my], !p.isEmpty else { return }
        p.removeLast()
        app.router.paths[.my] = p
    }
    /// The vault, after the phone's own check.
    func openVault() {
        Task { @MainActor in
            guard let noLock = await VaultGate.open(reason: L(R.string.my_ios_vault_reason)) else { return }
            push(.vault(noLock: noLock))
        }
    }
}

struct MyDestination: View {
    let route: MyRoute
    var body: some View {
        Group {
            switch route {
            case .profile: ProfilePage()
            case .agent: AgentPage()
            case .permissions: PermissionsPage()
            case .records: RecordsPage()
            case .notify: NotifyPage()
            case .model: ModelPage()
            case .modelCli(let p): ModelPage(cliProvider: p)
            case .usage: UsagePage()
            case .devices: DevicesPage()
            case .vault(let noLock): VaultPage(noLock: noLock)
            case .vaultItem(let name): VaultItemPage(name: name)
            case .vaultAi(let name): AiKeyPage(name: name)
            case .vaultAddLogin: AddLoginPage()
            case .vaultLog: VaultLogPage()
            case .resources: ResourcesPage()
            case .cleanup: CleanupPage()
            case .documents: DocumentsPage()
            case .document(let name): FileViewer(name: name)
            case .privacy: PrivacyPage()
            case .language: LanguagePage()
            case .wallet: WalletPage()
            case .operator: OperatorPage()
            case .serverUpdate: ServerUpdatePage()
            case .keyguard(let code): KeyguardPage(startWithCode: code)
            }
        }
        .keloHideNavigationBar()
    }
}

/// The numbers kept from last time show at once; each part replaces its old value when it arrives.
@MainActor @Observable final class MyOverviewModel {
    var o = MyOverview()
    var offline = false

    func load(app: AppModel) async {
        if let kept: MyOverview = app.cache.get("my") { o = kept }
        guard let api = app.api else { return }
        let usage = Task { try await api.get("/api/usage", as: MyUsage.self) }
        let devices = Task { try await api.devices() }
        let vault = Task { try await api.vault() }
        let uploads = Task { try await api.request("GET", "/api/uploads") }
        let resources = Task { try await api.resources() }
        let about = Task { try await api.about() }
        let walletN = Task { try await api.get("/api/wallet/notifications?limit=1", as: WalletNotifications.self) }
        let wallet = Task { try await api.get("/api/wallet/cards", as: WalletInfo.self) }
        let op = Task { try await api.get("/api/operator/status", as: OperatorStatus.self) }
        let serverUpdate = Task { await ServerUpdateRules.load(api) }
        var failures: [Error?] = []
        func step<T>(_ t: Task<T, Error>, _ apply: (T) -> Void) async {
            switch await t.result {
            case .success(let v): apply(v); failures.append(nil)
            case .failure(let e): failures.append(e)
            }
        }
        await step(usage) { o.usage = $0 }
        await step(devices) { o.devices = $0.devices.count }
        await step(vault) { o.creds = $0.items; o.locked = $0.locked }
        await step(uploads) { o.documents = $0.arrayValue?.count }
        await step(resources) {
            o.sizes = $0.profile
            o.storage = StorageRules.key(StorageRules.state($0.alert))
            o.storagePercent = $0.alert.percent
        }
        await step(about) { o.version = $0.version }
        await step(walletN) { o.walletUnseen = $0.unseen }
        await step(wallet) { o.cards = $0.cards.count }
        // the Operator section only for the server that runs the shared services (Android MyScreen: /api/operator/status)
        switch await op.result {
        case .success(let st): o.operator = OperatorRules.operatorFrom(nil, st.isOperator)
        case .failure(let e):
            if let a = e as? ApiError, a.status == 403 || a.status == 404 {
                var older: Int? = -1
                if a.status == 404 {   // an older server: the owner's own token can read the operator settings
                    do { _ = try await api.request("GET", "/api/operator/firebase"); older = nil }
                    catch let f as ApiError where f.status == 403 { older = 403 } catch {}
                }
                if let shown = OperatorRules.operatorFrom(a.status, nil, fallback: { OperatorRules.ownerFrom(older) }) { o.operator = shown }
            }
        }
        // the server update: only the owner's device, only a server that has it (403 / 404 / unavailable: nothing)
        switch await serverUpdate.value {
        case .shown(let st): o.serverUpdate = st
        case .hidden: o.serverUpdate = nil
        case .failed: break   // not reached: keep what was shown
        }
        offline = MyOverview.offline(failures)
        if failures.contains(where: { $0 == nil }) { app.cache.put("my", o) }
    }

    /// While a server update runs, its card's progress moves: asked every few seconds while 마이 shows (failures pass: the server
    /// restarts on the way).
    func followServerUpdate(app: AppModel) async {
        while o.serverUpdate?.isRunning == true, !Task.isCancelled {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard let api = app.api, !Task.isCancelled else { return }
            switch await ServerUpdateRules.load(api) {
            case .shown(let st): o.serverUpdate = st
            case .hidden: o.serverUpdate = nil
            case .failed: break
            }
        }
    }
}

/// The size profile in words (Android profileText).
@MainActor func profileText(_ profile: String, long: Bool) -> String? {
    switch profile {
    case "recommended": L(R.string.resources_recommended)
    case "minimum": L(R.string.resources_minimum)
    case "unlimited": long ? L(R.string.resources_unlimited_long) : L(R.string.resources_unlimited)
    case "custom": L(R.string.resources_custom)
    default: nil
    }
}

struct MyHome: View {
    @Environment(AppModel.self) private var app
    @Environment(\.kelo) private var c
    @State private var model = MyOverviewModel()
    @State private var confirmClear = false
    @State private var confirmLogout = false
    @State private var toast: String?
    @State private var notifyOn = NotifyPermission.cached

    private var nav: MyNav { MyNav(app: app) }

    var body: some View {
        let profile = app.profile ?? Profile()
        let o = model.o
        let connected = o.creds.filter { $0.hasSecret && ($0.kind == VaultEntry.kindApi || $0.kind == VaultEntry.kindEmail) }
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Color.clear.frame(height: 4).id("top")
                    // the server update, for the owner: a newer version, a run under way, a recent run that went wrong
                    if let card = ServerUpdateRules.card(o.serverUpdate, now: Date().timeIntervalSince1970) {
                        ServerUpdateCardView(card: card) { nav.push(.serverUpdate) }
                            .padding(.horizontal, 8).padding(.top, 8)
                    }
                    SectionLabel(L(R.string.my_section_account))
                    MenuRow(.user, L(R.string.profile_title), value: profile.userName.isEmpty ? L(R.string.my_name_missing) : profile.userName) { nav.push(.profile) }
                    MenuRow(.phone, L(R.string.devices_title), value: o.devices.map { L(R.string.count_devices, $0) }) { nav.push(.devices) }
                    // a storage warning replaces the size profile, in red when something stops soon
                    let storage = StorageRules.fromKey(o.storage)
                    let warning: String? = switch storage {
                    case .hostLow: L(R.string.storage_row_host)
                    case .critical: L(R.string.storage_row_critical)
                    case .warn: L(R.string.storage_row_warn, o.storagePercent ?? 80)
                    case .ok: nil
                    }
                    MenuRow(.chip, L(R.string.resources_title), value: warning ?? o.sizes.map { profileText($0, long: false) ?? $0 },
                            valueColor: warning == nil ? nil : (StorageRules.urgent(storage) ? c.danger : c.warn)) { nav.push(.resources) }
                        .accessibilityIdentifier("kelo.resources.row")
                    if let su = o.serverUpdate, ServerUpdateRules.visible(su) {
                        MenuRow(.update, L(R.string.server_update_title), value: ServerUpdateRules.rowValue(su)) { nav.push(.serverUpdate) }
                            .accessibilityIdentifier("kelo.serverUpdate.row")
                    }

                    SectionLabel(L(R.string.my_section_agent))
                    MenuRow(.spark, L(R.string.my_agent_title), value: profile.agentName) { nav.push(.agent) }

                    SectionLabel(L(R.string.my_section_model))
                    MenuRow(.chat, L(R.string.model_title),
                            value: profile.model.isEmpty ? L(profile.modelReady == false ? R.string.model_not_chosen : R.string.model_sheet_server_default)
                                : ModelNames.display(profile.model, label: nil)) { nav.push(.model) }
                    MenuRow(.pulse, L(R.string.usage_title), value: UsageText.summary(o.usage, model: profile.model)) { nav.push(.usage) }

                    SectionLabel(L(R.string.my_section_connections))
                    MenuRow(.plug, L(R.string.my_services), value: P(R.plurals.my_services_count, connected.count, connected.count)) {
                        app.router.go(.connectors)
                    }

                    SectionLabel(L(R.string.my_section_security))
                    let bad = o.creds.contains { $0.isBad }
                    MenuRow(.key, L(R.string.vault_title),
                            value: bad ? L(R.string.my_vault_check) : (o.locked ? L(R.string.my_vault_locked) : (o.creds.isEmpty ? nil : L(R.string.count_items, o.creds.count))),
                            valueColor: bad ? c.danger : nil) { nav.openVault() }
                    MenuRow(.card, L(R.string.wallet_title),
                            value: (o.cards ?? 0) > 0 ? P(R.plurals.wallet_card_count, o.cards!, o.cards!) : nil,
                            badge: (o.walletUnseen ?? 0) > 0 ? o.walletUnseen : nil) { nav.push(.wallet) }
                    MenuRow(.shield, L(R.string.agent_tab_permissions), value: L(R.string.my_perm_value)) { nav.push(.permissions) }
                    // the key of passwords, keys and cards on the owner's phones (Keyguard; owner only, the page says so otherwise)
                    MenuRow(.faceID, L(R.string.keyguard_title), value: app.locked ? L(R.string.keyguard_locked_title) : nil,
                            valueColor: app.locked ? c.danger : nil) { nav.push(.keyguard(code: false)) }
                    MenuRow(.lock, L(R.string.my_privacy_title)) { nav.push(.privacy) }

                    SectionLabel(L(R.string.my_section_data))
                    MenuRow(.file, L(R.string.docs_title), value: o.documents.map { L(R.string.count_items, $0) }) { nav.push(.documents) }
                    MenuRow(.book, L(R.string.my_records_title), value: L(R.string.my_records_value)) { nav.push(.records) }
                    MenuRow(.trash, L(R.string.my_clear_chat), danger: true) { confirmClear = true }

                    SectionLabel(L(R.string.my_section_app))
                    MenuRow(.bell, L(R.string.notify_my_row), value: notifyOn ? L(R.string.notify_state_on) : L(R.string.notify_state_off),
                            valueColor: notifyOn ? nil : c.danger) { nav.push(.notify) }
                    MenuRow(.globe, L(R.string.my_language),
                            value: Languages.override(app.languageChoice).map(Languages.name) ?? L(R.string.language_system)) { nav.push(.language) }

                    if o.operator == true {
                        SectionLabel(L(R.string.my_section_operator))
                        MenuRow(.cloud, L(R.string.operator_page_title)) { nav.push(.operator) }
                    }

                    Color.clear.frame(height: 16)
                    MenuRow(.arrowRight, L(R.string.my_logout), danger: true) { confirmLogout = true }
                    let versionLine = [AppInfo.version.isEmpty ? nil : L(R.string.my_version, AppInfo.version),
                                       (o.version ?? "").isEmpty ? nil : L(R.string.my_server_version, o.version!)].compactMap { $0 }.joined(separator: "\n")
                    if !versionLine.isEmpty {
                        Text(versionLine).font(KeloFont.small).foregroundStyle(c.muted).padding(.leading, 16).padding(.top, 12)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Color.clear.frame(height: 24)
                }
                .keloPageWidth()
                .padding(.horizontal, 8)
            }
            .onTabReselect(.my) { withAnimation { proxy.scrollTo("top", anchor: .top) } }
        }
        .background(c.bg.ignoresSafeArea())
        .overlay(alignment: .bottom) { StaleHint(visible: model.offline) { Task { await model.load(app: app) } }.padding(.bottom, 12) }
        .task { await model.load(app: app) }
        .task(id: model.o.serverUpdate?.isRunning == true) { await model.followServerUpdate(app: app) }
        .task { notifyOn = await NotifyPermission.isOn() }
        .onAppear { handleRequest() }
        .onChange(of: app.router.myPageRequest) { _, _ in handleRequest() }
        .keloConfirm(L(R.string.my_logout_confirm), isPresented: $confirmLogout, yes: L(R.string.my_logout)) { app.signOut() }
        .keloConfirm(L(R.string.my_clear_chat_confirm), isPresented: $confirmClear, yes: L(R.string.delete)) { clearChat() }
        .toast($toast)
    }

    private func handleRequest() {
        guard let r = app.router.myPageRequest else { return }
        app.router.myPageRequest = nil
        if r == .vault { nav.openVault(); return }
        if r == .walletAdd {   // the card form, behind the wallet's own lock (a fresh wallet page, so it opens there)
            WalletPlaces.go(.add)
            app.router.paths[.my] = NavigationPath([MyRoute.wallet])
            return
        }
        if r == .model, let cli = app.router.modelCli {   // a newer-CLI notice: the page with that CLI's box
            app.router.modelCli = nil
            nav.push(.modelCli(cli))
            return
        }
        if r == .cleanup {   // a storage warning's 정리하기: the cleanup screen over the resources page
            app.router.paths[.my] = NavigationPath([MyRoute.resources, MyRoute.cleanup])
            return
        }
        if let route = MyRoute.of(r) { nav.push(route) }
    }

    private func clearChat() {
        Task {
            do {
                try await app.requireApi().clearMessages()   // the chat and what came of it (the server stops running work first)
                app.historyWasCleared()                      // the kept chat is emptied and the screens drop what they hold
                NotificationCheck.clearShown()               // shown notifications name tasks that are gone
                toast = L(R.string.my_clear_chat_done)
            } catch {
                toast = app.message(for: error)
            }
        }
    }
}

/// The app's own version: Kelo's release (version.json) and the App Store build number ("0.9.0 (26111011)"). No update check here:
/// iPhone updates come from the App Store / TestFlight (the Android sideload and desktop apps check https://kelo.chat/download/latest.json).
enum AppInfo {
    static var version: String { AppInfoLine.current }

    static func line(short: String?, build: String?) -> String { AppInfoLine.line(version: short ?? "", build: build) }
}
