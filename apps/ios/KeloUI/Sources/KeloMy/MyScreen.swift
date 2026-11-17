import SwiftUI
import KeloCore
import KeloShared

/// 마이 tab (Android my/MyScreen.kt): one grouped settings list (account and devices, the agent, the model and its usage,
/// connections, security, data, the app, and for the server's owner its operator settings). It shows only what belongs to the
/// person (their own uploads; never the assistant's skills or notes). Sub pages push full screen (the shell hides its header and
/// tabs while the stack is not empty). The vault opens only after the phone's own check (Face ID, Touch ID or the passcode).
public struct MyScreen: View {
    @Environment(AppModel.self) private var app
    /// The sync of the phone's calendar and contacts: one for the app, handed down to the pages that show it.
    @Environment(\.phoneDataSync) private var phoneData
    public init() {}

    public var body: some View {
        // a new API (the language changed) builds the stack again, so every model speaks to the current one
        if let api = app.api { MyStack(api: api, app: app, phoneData: phoneData).id(ObjectIdentifier(api)) }
    }
}

/// The My tab's navigation stack: the home page and its pages, built from one API; the vault's model is shared by the vault's pages.
private struct MyStack: View {
    let api: KeloApi
    let app: AppModel
    let phoneData: PhoneDataSync
    @State private var vault: VaultModel

    init(api: KeloApi, app: AppModel, phoneData: PhoneDataSync) {
        self.api = api; self.app = app; self.phoneData = phoneData
        _vault = State(initialValue: VaultModel(api: api))
    }

    var body: some View {
        ListDetailStack<MyRoute, _, _>(tab: .my, root: {
            MyHome(api: api, app: app)
                .keloShellInsets()
        }, detail: { route in
            MyDestination(route: route, api: api, vault: vault, phoneData: phoneData)
        })
        // a login or key removed from the vault may leave data: asked here, since the item's page has closed by then
        .forgetAsk(Bindable(vault).forget, api: api)
    }
}

/// Pushes and pops the My tab's stack.
@MainActor struct MyNav {
    let app: AppModel
    func push(_ r: MyRoute) { app.router.paths[.my, default: NavigationPath()].append(r) }
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
    let api: KeloApi
    let vault: VaultModel
    let phoneData: PhoneDataSync
    @Environment(AppModel.self) private var app

    var body: some View {
        Group {
            switch route {
            case .profile: ProfilePage(api: api, host: app)
            case .agent: AgentPage(api: api, host: app)
            case .permissions: PermissionsPage(api: api, phoneData: phoneData)
            case .records: RecordsPage(api: api)
            case .notify: NotifyPage(api: api)
            case .proactiveHeld: ProactiveHeldPage(api: api)
            case .model: ModelPage(api: api, cache: app.cache, prefs: app.prefs, host: app)
            case .modelCli(let p): ModelPage(api: api, cache: app.cache, prefs: app.prefs, host: app, cliProvider: p)
            case .usage: UsagePage(api: api)
            case .devices: DevicesPage(api: api, onThisDeviceRemoved: { app.signOut() })
            case .vault(let noLock): VaultPage(noLock: noLock, api: api, vault: vault)
            case .vaultItem(let name): VaultItemPage(name: name, vault: vault)
            case .vaultAi(let name): AiKeyPage(name: name, vault: vault)
            case .vaultAddLogin: AddLoginPage(vault: vault)
            case .vaultLog: VaultLogPage(vault: vault)
            case .resources: ResourcesPage(api: api)
            case .cleanup: CleanupPage(api: api)
            case .documents: DocumentsPage(api: api)
            case .files: KeloFilesPage(api: api)
            case .actions: ActionsPage(api: api)
            case .androidDevice: AndroidDevicePage(api: api)
            case .nest: NestPage(api: api)
            case .document(let name): FileViewer(api: api, name: name)
            case .privacy: PrivacyPage()
            case .forget: ForgetPage(api: api)
            case .data: DataPage(api: api, onHistoryCleared: { app.historyWasCleared(); NotificationCheck.clearShown() })
            case .language: LanguagePage()
            case .wallet: WalletPage(api: api)
            case .operator: OperatorPage(api: api)
            case .serverUpdate: ServerUpdatePage(api: api, cache: app.cache)
            case .keyguard(let code): KeyguardPage(app: app, startWithCode: code)
            }
        }
        .keloHideNavigationBar()
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
    @Environment(\.nestSurfaceView) private var nestSurface
    @State private var model: MyOverviewModel
    /// 공지: the operator's unread notices (none at all when there are none, or the server is older)
    @State private var notices: NoticesModel
    @State private var confirmClear = false
    @State private var confirmLogout = false
    @State private var changeAddress = false
    @State private var notifyOn = NotifyPermission.cached()

    init(api: KeloApi, app: AppModel) {
        _model = State(initialValue: MyOverviewModel(api: api, cache: app.cache, onSignedOut: { app.signOut() },
                                                      onHistoryCleared: { app.historyWasCleared(); NotificationCheck.clearShown() }))
        _notices = State(initialValue: NoticesModel(api: api, language: {
            NoticesRules.language(pick: app.languageChoice, systemShown: Languages.suggested(Locale.preferredLanguages))
        }))
    }

    /// The notice open in the sheet; closing the sheet by any means closes it in the model.
    private var openedNotice: Binding<OperatorNotice?> {
        Binding(get: { notices.opened }, set: { if $0 == nil { notices.close() } })
    }

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
                    // 공지: above 계정과 기기; no area at all while nothing is unread
                    NoticesArea(model: notices)
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
                    // the Android device Kelo uses: only when the server has one set up
                    if AndroidDeviceRules.shown(model.android) {
                        MenuRow(.phone, L(R.string.android_device_title),
                                value: L(model.android?.connected == true ? R.string.android_device_connected : R.string.android_device_not_connected)) {
                            nav.push(.androidDevice)
                        }
                        .accessibilityIdentifier("kelo.androidDevice.row")
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
                    MenuRow(.trash, L(R.string.forget_title)) { nav.push(.forget) }

                    SectionLabel(L(R.string.my_section_data))
                    MenuRow(.file, L(R.string.docs_title), value: o.documents.map { L(R.string.count_items, $0) }) { nav.push(.documents) }
                    MenuRow(.file, L(R.string.files_title)) { nav.push(.files) }
                    // Nest: installs from a link, what Kelo made by talking, the package tabs (the page says what is there)
                    MenuRow(.spark, L(R.string.nest_title), value: L(R.string.nest_made_here)) { nav.push(.nest) }
                    MenuRow(.clock, L(R.string.actions_title)) { nav.push(.actions) }
                    MenuRow(.book, L(R.string.my_records_title), value: L(R.string.my_records_value)) { nav.push(.records) }
                    MenuRow(.file, L(R.string.data_title)) { nav.push(.data) }
                    MenuRow(.trash, L(R.string.my_clear_chat), danger: true) { confirmClear = true }

                    SectionLabel(L(R.string.my_section_app))
                    MenuRow(.bell, L(R.string.notify_my_row), value: notifyOn ? L(R.string.notify_state_on) : L(R.string.notify_state_off),
                            valueColor: notifyOn ? nil : c.danger) { nav.push(.notify) }
                    MenuRow(.globe, L(R.string.my_language),
                            value: Languages.override(app.languageChoice).map(Languages.name) ?? L(R.string.language_system)) { nav.push(.language) }

                    // the server this app talks to, one row (owner 2026-11-17: two rows opened the same sheet); tapping it changes the
                    // address, only on a phone that holds the server's key (an older server has none)
                    if let base = app.session?.serverUrl {
                        MenuRow(.cloud, L(R.string.server_address_row), value: base) { if app.canMoveServer { changeAddress = true } }
                    }

                    if o.operator == true {
                        SectionLabel(L(R.string.my_section_operator))
                        MenuRow(.cloud, L(R.string.operator_page_title)) { nav.push(.operator) }
                    }

                    // tabs the layout hides stay reachable from here
                    ForEach(KeloTab.allCases.filter { app.tabLayout.hides($0.rawValue) }, id: \.self) { tab in
                        MenuRow(tab.icon, tab.label) { app.router.hiddenOpen = tab }
                    }
                    // Nest sections: only while the app has a renderer (nothing otherwise)
                    if let surface = nestSurface {
                        ForEach(app.nest?.state.entries(NestPlace.mySection, surface: surface) ?? []) { e in
                            SectionLabel(e.packageName.isEmpty ? e.title : e.packageName)
                            surface.view(for: e)
                        }
                    }

                    Color.clear.frame(height: 16)
                    MenuRow(.arrowRight, L(R.string.my_logout), danger: true) { confirmLogout = true }
                    AppVersionLines(appVersion: AppInfo.version, serverVersion: o.version, source: AppInfo.source)
                    Color.clear.frame(height: 24)
                }
                .keloPageWidth()
                .padding(.horizontal, 8)
            }
            .keloTopScroll(root: true)
            .onTabReselect(.my) { withAnimation { proxy.scrollTo("top", anchor: .top) } }
        }
        .background(c.bg.ignoresSafeArea())
        .overlay(alignment: .bottom) { StaleHint(visible: model.offline) { model.reload() }.padding(.bottom, 12) }
        .task { await model.load() }
        .task { await notices.load() }
        .keloSheet(item: openedNotice) { n in NoticeSheet(notice: n, onClose: { notices.close() }) }
        .task(id: model.o.serverUpdate?.isRunning == true && app.router.shows(.my)) {
            if app.router.shows(.my) { await model.followServerUpdate() }
        }
        .task { notifyOn = await NotifyPermission.isOn() }
        .onAppear { handleRequest() }
        .onChange(of: app.router.myPageRequest) { _, _ in handleRequest() }
        .keloSheet(isPresented: $changeAddress) {
            ServerAddressSheet(current: app.session?.serverUrl ?? "", move: { await app.moveServer(to: $0) }, onMoved: {
                changeAddress = false
                model.say(L(R.string.server_address_done))
            }, onClose: { changeAddress = false })
        }
        .keloConfirm(L(R.string.my_logout_confirm), isPresented: $confirmLogout, yes: L(R.string.my_logout)) { model.signOut() }
        .keloConfirm(L(R.string.my_clear_chat_confirm), isPresented: $confirmClear, yes: L(R.string.delete)) { model.clearChat() }
        .toast(model.notice, clear: model.clearNotice)
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
}

/// The app's own version: Kelo's release (version.json) and the App Store build number ("0.9.0 (26111011)"). No update check here:
/// iPhone updates come from the App Store / TestFlight (the Android sideload and desktop apps check https://kelo.chat/download/latest.json).
enum AppInfo {
    static var version: String { AppInfoLine.current }
    /// The commit this build's source came from (Info.plist; see AppSource)
    static var source: AppSource { AppSource.current }
}

/// The version lines at the bottom of My: "App version: 0.9.3 (26111011) · ios-1.0.2" and "Server version: 0.10.2 · release-0.10.2". The
/// tag is the public release of the source: a link to it on GitHub for a clean build (it opens in the browser), plain "ios-1.0.2 (modified)"
/// for a build from changed files (`SourceLink`, docs/versioning.md). The server's tag links to kelo-chat/kelo-server.
struct AppVersionLines: View {
    let appVersion: String
    let serverVersion: String?
    let source: AppSource
    @Environment(\.kelo) private var c

    var body: some View {
        let label = SourceLink.label(tag: source.tag, dirty: source.dirty, modified: L(R.string.my_source_modified))
        VStack(alignment: .leading, spacing: 0) {
            if !appVersion.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 0) {
                    Text(L(R.string.my_version, appVersion) + (label == nil ? "" : " · ")).foregroundStyle(c.muted)
                    if let label {
                        if let url = source.url {
                            Link(label, destination: url).foregroundStyle(c.accent).underline()
                                .accessibilityLabel(L(R.string.my_source_link))
                        } else {
                            Text(label).foregroundStyle(c.muted)
                        }
                    }
                }
            }
            if let server = serverVersion, !server.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 0) {
                    let tag = SourceLink.serverTag(version: server)
                    Text(L(R.string.my_server_version, server) + (tag == nil ? "" : " · ")).foregroundStyle(c.muted)
                    if let tag, let url = SourceLink.serverUrl(version: server).flatMap(URL.init(string:)) {
                        Link(tag, destination: url).foregroundStyle(c.accent).underline()
                            .accessibilityLabel(L(R.string.my_source_link))
                    }
                }
            }
        }
        .font(KeloFont.small).padding(.leading, 16).padding(.top, 12)
        .fixedSize(horizontal: false, vertical: true)
    }
}
