import SwiftUI
import KeloCore


/// Which AI service and model answer, the fallback model (never on the main model's provider, as the server enforces), the API key
/// or server address for the service, its CLI, and the phone sign-in when the server enables it (Android my/ModelPage.kt).
public struct ModelPage: View {
    private static let addEndpointValue = "__add_endpoint__"
    /// A newer-CLI notice opened the page for that provider's CLI: when another service is selected, its box (with 업데이트) shows on
    /// its own card at the top, so the main and fallback choice stay as they are.
    var cliProvider: String? = nil
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var vm: ModelSettingsModel
    @State private var keyInput = ""
    @State private var confirmRemoveKey = false
    @State private var urlInput = ""
    @State private var editingUrl = false
    @State private var addingEndpoint = false
    @State private var addName = ""
    @State private var addUrl = ""
    @State private var editingName = false
    @State private var renameInput = ""
    @State private var confirmDeleteEndpoint = false

    public init(api: KeloApi, cache: ScreenCache, prefs: Prefs, host: any ProfileHost, cliProvider: String? = nil) {
        self.cliProvider = cliProvider
        _vm = State(initialValue: ModelSettingsModel(api: api, cache: cache, prefs: prefs, host: host))
    }

    private var current: ModelProvider? { vm.current }

    public var body: some View {
        let provider = vm.provider, providers = vm.providers, fb = vm.fb, bg = vm.bg, gate = vm.gate
        let unagreed = ProviderConsentRules.pending(main: provider, fallback: fb.provider, providers: providers).filter { gate.needs($0) }
        SubPage(L(R.string.model_title), onBack: { dismiss() }) {
            if !unagreed.isEmpty && gate.asking == nil {
                MutedText(L(R.string.model_unofficial_warning, unagreed.map(\.displayName).joined(separator: ", ")), small: true)
            }
            // the CLI a newer-CLI notice is about, when another service is selected below (the selected one shows its own box there)
            if let f = providers.first(where: { $0.name == CliUpdateRules.focus(cliProvider, selected: provider,
                                                                                  withCli: providers.filter { $0.cli != nil }.map(\.name)) }),
               let cli = f.cli {
                MyCard {
                    FieldLabel(f.displayName)
                    cliBox(cli, f, signInHere: false)
                }
            }
            MyCard {
                FieldLabel(L(R.string.model_picker_provider))
                SelectField(title: L(R.string.model_picker_provider), options: providerOptions(), selected: provider,
                            groupLabel: L(R.string.model_picker_unofficial)) { picked in
                    if picked == Self.addEndpointValue {
                        addingEndpoint = true
                        vm.clearEndpointMsg()
                    } else {
                        vm.pickProvider(picked)
                    }
                }
                HStack {
                    Spacer()
                    LinkButton(L(R.string.endpoint_add)) {
                        addingEndpoint = true
                        vm.clearEndpointMsg()
                    }
                }
                if addingEndpoint {
                    KeyBox {
                        FieldLabel(L(R.string.endpoint_name))
                        KeloTextField(L(R.string.endpoint_name_hint), text: $addName, maxLength: 40)
                        FieldLabel(L(R.string.model_url_label))
                        KeloTextField(L(R.string.model_url_placeholder), text: $addUrl, keyboard: .url, maxLength: 300)
                        HStack(spacing: 12) {
                            PrimaryButton(L(R.string.save), enabled: !vm.savingEndpoint && !addName.trimmingCharacters(in: .whitespaces).isEmpty && !addUrl.trimmingCharacters(in: .whitespaces).isEmpty) {
                                vm.addEndpoint(name: addName, baseUrl: addUrl) {
                                    addingEndpoint = false
                                    addName = ""
                                    addUrl = ""
                                }
                            }
                            LinkButton(L(R.string.cancel)) {
                                addingEndpoint = false
                                addName = ""
                                addUrl = ""
                                vm.clearEndpointMsg()
                            }
                        }
                        if !vm.endpointMsg.isEmpty { ErrorText(vm.endpointMsg, small: true) }
                    }
                }
                ChatsGoLine(provider: provider, company: current?.company)
                if let cur = current {
                    if let cli = cur.cli { cliBox(cli, cur) }
                    if SignInRules.offered(signin: cur.signin, signedIn: cur.signedIn) {
                        KeyBox(first: cur.cli == nil) {
                            SignInBox(provider: cur, api: vm.api, gate: gate) { vm.signedIn() }
                        }
                    }
                    if SignOutRules.offered(cur.signedIn) {
                        KeyBox(first: cur.cli == nil) {
                            SignOutBox(provider: cur, api: vm.api, mainProvider: vm.mainProvider) { outcome, result, detail in
                                vm.signedOut(cur, outcome, result, detail)
                            }
                        }
                    }
                    if let so = vm.signOut, so.0 == cur.name { SignOutMessage(outcome: so.1, detail: vm.signOutDetail) }
                    if cur.takesUrl { urlBox(cur) }
                    if cur.takesKey || cur.takesUrl { keyBox(cur) }
                    if cur.oauth && cur.loggedIn != true && !cur.signin {
                        Text(emphasized(L(R.string.model_oauth_login_needed), "python -m assistant.providers.oauth login \(cur.name)", code: true))
                            .font(KeloFont.small).foregroundStyle(c.danger).fixedSize(horizontal: false, vertical: true)
                    }
                }
                if !provider.isEmpty {
                    HStack {
                        FieldLabel(L(R.string.model_picker_model))
                        Spacer(minLength: 8)
                        LinkButton(vm.slowModels ? L(R.string.loading) : L(R.string.refresh)) { vm.refreshModels() }
                    }
                    modelPicker(provider: provider, list: vm.models, loading: vm.loadingModels, value: Binding(get: { vm.model }, set: { vm.setModel($0) }),
                                custom: Binding(get: { vm.custom }, set: { vm.setCustom($0) }))
                    if let e = vm.models?.error { ErrorText(e, small: true) }
                    if vm.models?.stale == true { MutedText(L(R.string.model_list_stale), small: true) }
                    effortField
                } else {
                    Text(emphasized(L(R.string.model_server_default_note), ".env", code: true))
                        .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                Text(L(R.string.model_sheet_fallback)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                MutedText(L(R.string.model_fallback_help), small: true)
                SelectField(title: L(R.string.model_sheet_fallback), options: fallbackOptions(), selected: fb.provider,
                            groupLabel: L(R.string.model_picker_unofficial)) { picked in
                    vm.pickFallback(picked)
                }
                if !fb.provider.isEmpty {
                    modelPicker(provider: fb.provider, list: vm.fbModels, loading: vm.fbLoading, value: Binding(get: { vm.fb.model }, set: { vm.setFbModel($0) }),
                                custom: Binding(get: { vm.fbCustom }, set: { vm.setFbCustom($0) }))
                }
                if vm.fbNote { MutedText(L(R.string.model_sheet_fallback_cleared), small: true) }
                // background work (look-back, memory, journal, ...): any provider, the main one too; empty = the main model
                Text(L(R.string.model_background_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                MutedText(L(R.string.model_background_help), small: true)
                SelectField(title: L(R.string.model_background_title), options: backgroundOptions(), selected: bg.provider,
                            groupLabel: L(R.string.model_picker_unofficial)) { picked in
                    vm.pickBackground(picked)
                }
                if !bg.provider.isEmpty {
                    modelPicker(provider: bg.provider, list: vm.bgModels, loading: vm.bgLoading, value: Binding(get: { vm.bg.model }, set: { vm.setBgModel($0) }),
                                custom: Binding(get: { vm.bgCustom }, set: { vm.setBgCustom($0) }))
                }
                if let f = UsageLogic.fallbackShown(vm.bgState, provider: bg.provider, model: bg.model) {
                    MutedText(L(R.string.model_background_fell_back, MyFormat.monthDayTime(f.at)), small: true)
                }
                PrimaryButton(L(R.string.save), enabled: !vm.saving && ModelRules.complete(ModelChoice(provider, vm.model)) && ModelRules.complete(fb) && ModelRules.complete(bg),
                              busy: vm.saving) { vm.save() }
                if !vm.err.isEmpty { ErrorText(vm.err, small: true) }
            }
        }
        .task { await vm.start() }
        .onChange(of: vm.savedTick) { _, _ in dismiss() }   // saved: back to 마이
        .onChange(of: vm.provider) { _, _ in urlInput = ""; editingUrl = false; editingName = false; confirmDeleteEndpoint = false }   // another service: its own address field starts empty
        .keloSheet(isPresented: Binding(get: { gate.asking != nil }, set: { if !$0 && gate.asking != nil { gate.decline() } })) {
            ProviderConsentSheet(gate: gate)
        }
        .alert(vm.askInstall.map { L(R.string.model_cli_install_confirm, $0.displayName) } ?? "",
               isPresented: Binding(get: { vm.askInstall != nil }, set: { if !$0 { vm.clearAskInstall() } })) {
            Button(L(R.string.model_cli_install)) { if let p = vm.askInstall { vm.install(p) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .keloConfirm(L(R.string.model_key_delete_confirm), isPresented: $confirmRemoveKey, yes: L(R.string.delete)) { vm.removeKey() }
        .keloConfirm(L(R.string.endpoint_delete_confirm, current?.displayName ?? ""), isPresented: $confirmDeleteEndpoint, yes: L(R.string.delete)) {
            if let cur = current { vm.deleteEndpoint(cur.name) }
        }
    }

    // ---- pieces ---------------------------------------------------------------------------------------------------------------------

    private func providerOptions() -> [SelectOption] {
        let sorted = vm.providers.sorted { a, b in
            let ka = (ProviderConsentRules.group(a), a.configured == true ? 0 : 1, a.displayName.lowercased())
            let kb = (ProviderConsentRules.group(b), b.configured == true ? 0 : 1, b.displayName.lowercased())
            return ka < kb
        }
        let none = vm.modelReady == false ? R.string.model_picker_choose_provider : R.string.model_sheet_server_default
        return [SelectOption(value: "", label: L(none))] + sorted.map { p in
            let label: String = switch p.configured {
            case true?: L(R.string.model_picker_ready, p.displayName)
            case false?: L(R.string.model_picker_needs_setup, p.displayName)
            case nil: p.displayName
            }
            return SelectOption(value: p.name, label: label, group: ProviderConsentRules.group(p))
        } + [SelectOption(value: Self.addEndpointValue, label: L(R.string.endpoint_add), group: 1)]
    }

    private func fallbackOptions() -> [SelectOption] {
        let others = ModelRules.fallbackProviders(vm.providers, main: vm.provider)
            .sorted { (ProviderConsentRules.group($0), $0.displayName.lowercased()) < (ProviderConsentRules.group($1), $1.displayName.lowercased()) }
        return [SelectOption(value: "", label: L(R.string.model_sheet_no_fallback))]
            + others.map { SelectOption(value: $0.name, label: $0.displayName, group: ProviderConsentRules.group($0)) }
    }

    private func backgroundOptions() -> [SelectOption] {
        let all = vm.providers.sorted { (ProviderConsentRules.group($0), $0.displayName.lowercased()) < (ProviderConsentRules.group($1), $1.displayName.lowercased()) }
        return [SelectOption(value: "", label: L(R.string.model_background_same))]
            + all.map { SelectOption(value: $0.name, label: $0.displayName, group: ProviderConsentRules.group($0)) }
    }

    /// The thinking level, as in the chat's model sheet; a server that turned it away: its sentence, quietly
    @ViewBuilder private var effortField: some View {
        switch EffortRules.effort(current, facts: vm.effortFacts) {
        case .levels(let levels):
            FieldLabel(L(R.string.chat_effort))
            SelectField(title: L(R.string.chat_effort), options: levels.map { SelectOption(value: $0, label: Self.effortWord($0)) },
                        selected: EffortRules.shownLevel(levels, vm.effort)) { v in vm.setEffort(v) }
            MutedText(L(R.string.chooser_effort_hint), small: true)
        case .fromModel(let n), .refused(let n):
            MutedText(n, small: true)
        case .none:
            EmptyView()
        }
    }

    private static func effortWord(_ v: String) -> String {
        switch v {
        case "": L(R.string.chat_effort_default)
        case "low": L(R.string.chat_effort_low)
        case "medium": L(R.string.chat_effort_medium)
        case "high": L(R.string.chat_effort_high)
        case "xhigh": L(R.string.chat_effort_xhigh)
        case "max": L(R.string.chat_effort_max)
        default: v
        }
    }

    @ViewBuilder private func modelPicker(provider p: String, list: ModelList?, loading: Bool, value: Binding<String>, custom: Binding<Bool>) -> some View {
        // nothing kept yet and still loading: the chosen model alone, by name (never the raw id in a text field)
        let shownList = list ?? (loading ? ModelCatalog.placeholder(provider: p, model: value.wrappedValue, as: ModelList.self) : nil)
        if let l = shownList, !l.models.isEmpty {
            let names = ModelNames.displayAll(l.models, labels: l.labels)
            let opts = (value.wrappedValue.isEmpty ? [SelectOption(value: "", label: L(R.string.model_picker_choose_model), enabled: false)] : [])
                + l.models.map { SelectOption(value: $0, label: names[$0] ?? $0) }
                + [SelectOption(value: ModelSettingsModel.customValue, label: L(R.string.model_picker_custom))]
            SelectField(title: L(R.string.model_picker_model), options: opts, selected: custom.wrappedValue ? ModelSettingsModel.customValue : value.wrappedValue) { v in
                if v == ModelSettingsModel.customValue { custom.wrappedValue = true } else { custom.wrappedValue = false; value.wrappedValue = v }
            }
        }
        if custom.wrappedValue || (shownList?.models.isEmpty ?? true) {
            KeloTextField(L(R.string.model_picker_custom_placeholder), text: value, keyboard: .ascii, maxLength: 200)
        }
    }

    @ViewBuilder private func cliBox(_ cli: CliStatus, _ p: ModelProvider, signInHere: Bool = true) -> some View {
        KeyBox {
            let state: String = {
                if vm.installing { return L(R.string.model_cli_installing) }
                if !cli.installed { return L(cli.remote ? R.string.model_cli_unreachable : R.string.model_cli_not_installed) }
                if cli.loggedIn == false { return L(R.string.model_cli_login_needed) }
                if cli.loggedIn == true { return L(cli.remote ? R.string.model_cli_ok_remote : R.string.model_cli_ok_local) }
                return L(R.string.model_cli_ok_unknown)
            }()
            MutedText(state, small: true)
            if !cli.installed && cli.installable && !vm.installing { SecondaryButton(L(R.string.model_cli_install)) { vm.install(p) } }
            let signInBelow = signInHere && p.signin && cli.installed && cli.loggedIn != true
            if (!cli.installed || cli.loggedIn != true) && !vm.installing && !cli.hint.isEmpty && !signInBelow { MutedText(cli.hint, small: true) }
            if cli.installed {
                let parts = [cli.version.flatMap { $0.isEmpty ? nil : L(R.string.model_cli_version, $0) },
                             cli.latestVersion.flatMap { cli.updateAvailable && !$0.isEmpty ? L(R.string.model_cli_update_available, $0) : nil },
                             cli.updateHint.flatMap { $0.isEmpty || cli.updatable ? nil : $0 }].compactMap { $0 }   // the button does it
                if !parts.isEmpty { MutedText(parts.joined(separator: L(R.string.usage_group_joiner)), small: true) }
            }
            if vm.updating == p.name { MutedText(L(R.string.model_cli_updating), small: true) }
            else if CliUpdateRules.showButton(cli, busy: vm.installing || vm.updating != nil) { SecondaryButton(L(R.string.model_cli_update_button)) { vm.updateCli(p) } }
            if let u = vm.updateOutcome, u.0 == p.name {
                switch u.1 {
                case .updated(let product, let version): MutedText(L(R.string.model_cli_update_done, product, version), small: true)
                case .unchanged(let product): MutedText(L(R.string.model_cli_update_unchanged, product), small: true)
                case .failed(let error): ErrorText(L(R.string.model_cli_update_failed, error), small: true)
                case .running: EmptyView()
                }
            }
            if !vm.installMsg.isEmpty { MutedText(vm.installMsg, small: true) }
        }
    }

    @ViewBuilder private func urlBox(_ cur: ModelProvider) -> some View {
        KeyBox {
            let isCustom = cur.name == "custom"
            let isUser = cur.userEndpoint
            if isUser || isCustom {
                if editingName {
                    FieldLabel(L(R.string.endpoint_name))
                    KeloTextField(L(R.string.endpoint_name_hint), text: $renameInput, maxLength: 40)
                    HStack(spacing: 12) {
                        PrimaryButton(L(R.string.save), enabled: !renameInput.trimmingCharacters(in: .whitespaces).isEmpty || isCustom) {
                            vm.renameEndpoint(cur.name, name: renameInput) {
                                editingName = false
                            }
                        }
                        LinkButton(L(R.string.cancel)) {
                            editingName = false
                            vm.clearEndpointMsg()
                        }
                    }
                } else {
                    HStack(spacing: 12) {
                        LinkButton(L(R.string.endpoint_rename)) {
                            renameInput = cur.displayName
                            editingName = true
                            vm.clearEndpointMsg()
                        }
                        if isUser {
                            LinkButton(L(R.string.endpoint_delete), danger: true) {
                                confirmDeleteEndpoint = true
                                vm.clearEndpointMsg()
                            }
                        }
                    }
                }
            }
            let saved = cur.baseUrl ?? ""
            if !saved.isEmpty && !editingUrl {
                MutedText(L(R.string.model_url_set, saved), small: true)
                LinkButton(L(R.string.model_url_change)) { urlInput = saved; vm.clearUrlMsg(); editingUrl = true }
            } else {
                FieldLabel(L(R.string.model_url_label))
                KeloTextField(L(R.string.model_url_placeholder), text: $urlInput, keyboard: .url, maxLength: 300) { saveUrl() }
                PrimaryButton(L(R.string.save), enabled: !vm.savingUrl && !urlInput.trimmingCharacters(in: .whitespaces).isEmpty) { saveUrl() }
                MutedText(L(R.string.model_url_help), small: true)
                if !saved.isEmpty { LinkButton(L(R.string.cancel)) { editingUrl = false; urlInput = ""; vm.clearUrlMsg() } }
            }
            if !vm.urlMsg.isEmpty { ErrorText(vm.urlMsg, small: true) }
            if !vm.endpointMsg.isEmpty && !addingEndpoint { ErrorText(vm.endpointMsg, small: true) }
        }
    }

    @ViewBuilder private func keyBox(_ cur: ModelProvider) -> some View {
        KeyBox(first: !cur.takesUrl) {
            if cur.takesUrl { FieldLabel(L(R.string.model_key_optional)) }
            switch KeyRules.state(cur.keySource) {
            case .inApp:
                let hint = cur.keyHint ?? ""
                MutedText(hint.isEmpty ? L(R.string.model_key_set) : L(R.string.model_key_in_app, hint), small: true)
                LinkButton(L(R.string.model_key_delete), danger: true) { confirmRemoveKey = true }
            case .env:
                MutedText(L(R.string.model_key_env), small: true)
            case .none:
                MutedText(L(R.string.model_key_none), small: true)
                KeloTextField(L(R.string.model_key_paste), text: $keyInput, secure: true, keyboard: .ascii, maxLength: 512)
                PrimaryButton(L(R.string.save), enabled: KeyRules.keyReady(keyInput)) { vm.saveKey(keyInput) { keyInput = "" } }
            }
            if !vm.keyMsg.isEmpty { MutedText(vm.keyMsg, small: true) }
        }
    }

    // ---- actions ----------------------------------------------------------------------------------------------------------------------

    private func saveUrl() {
        vm.saveUrl(urlInput) { urlInput = ""; editingUrl = false }
    }
}
