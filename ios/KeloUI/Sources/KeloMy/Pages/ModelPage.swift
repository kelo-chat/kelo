import SwiftUI
import KeloCore
import KeloShared

private let customValue = "__custom__"

/// Which AI service and model answer, the fallback model (never on the main model's provider, as the server enforces), the API key
/// or server address for the service, its CLI, and the phone sign-in when the server enables it (Android my/ModelPage.kt).
struct ModelPage: View {
    /// A newer-CLI notice opened the page for that provider's CLI: when another service is selected, its box (with 업데이트) shows on
    /// its own card at the top, so the main and fallback choice stay as they are.
    var cliProvider: String? = nil
    @Environment(AppModel.self) private var app
    @Environment(\.dismiss) private var dismiss
    @Environment(\.kelo) private var c
    @State private var started = false
    @State private var provider = ""
    @State private var model = ""
    @State private var effort = ""
    @State private var effortTouched = false
    @State private var fb = ModelChoice.none
    @State private var fbNote = false
    @State private var bg = ModelChoice.none                 // the background work model; none = the main model
    @State private var bgModels: ModelList?
    @State private var bgCustom = false
    @State private var bgState: BackgroundModelState?
    @State private var providers: [MyProvider] = []
    @State private var gate = ConsentGate()
    @State private var saving = false
    @State private var err = ""
    @State private var models: ModelList?
    @State private var fbModels: ModelList?
    @State private var loadingModels = false
    /// a refresh over `ModelCatalog.slowMs`: the 새로고침 link says it is loading
    @State private var slowModels = false
    @State private var fbLoading = false
    @State private var bgLoading = false
    /// the profile's choice and the last provider and model lists (kept on the phone) are in place
    @State private var prefilled = false
    @State private var custom = false
    @State private var fbCustom = false
    @State private var keyInput = ""
    @State private var keyMsg = ""
    @State private var installing = false
    @State private var installMsg = ""
    @State private var updating: String?                                   // the CLI provider being updated
    @State private var updateOutcome: (String, CliUpdateRules.Outcome)?    // provider name, how it went
    @State private var asked: Set<String> = []
    @State private var askInstall: MyProvider?
    @State private var confirmRemoveKey = false
    @State private var signOut: (String, SignOutRules.Outcome)?
    @State private var signOutDetail = ""
    @State private var urlInput = ""
    @State private var editingUrl = false
    @State private var urlMsg = ""
    @State private var savingUrl = false

    private var current: MyProvider? { providers.first { $0.name == provider } }

    var body: some View {
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
                    gate.request(providers.first { $0.name == picked }) { pickProvider(picked) }
                }
                ChatsGoLine(provider: provider, company: current?.company)
                if let cur = current {
                    if let cli = cur.cli { cliBox(cli, cur) }
                    if SignInRules.offered(signin: cur.signin, signedIn: cur.signedIn) {
                        KeyBox(first: cur.cli == nil) {
                            SignInBox(provider: cur, gate: gate) {
                                signOut = nil
                                Task { await loadProviders(); loadModels(provider, refresh: true) }
                            }
                        }
                    }
                    if SignOutRules.offered(cur.signedIn) {
                        KeyBox(first: cur.cli == nil) {
                            SignOutBox(provider: cur, mainProvider: app.profile?.provider ?? "") { outcome, result, detail in
                                signOut = (cur.name, outcome); signOutDetail = detail
                                if SignOutRules.done(outcome) {
                                    if result?.fallbackCleared == true && fb.provider == cur.name { fb = .none }
                                    Task {
                                        await loadProviders()
                                        if let p = try? await app.requireApi().profile() {
                                            app.setProfile(p)
                                            bg = ModelChoice(p.backgroundProvider, p.backgroundModel)   // a background model on that sign-in is emptied
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if let so = signOut, so.0 == cur.name { SignOutMessage(outcome: so.1, detail: signOutDetail) }
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
                        LinkButton(slowModels ? L(R.string.loading) : L(R.string.refresh)) { if !loadingModels { loadModels(provider, refresh: true) } }
                    }
                    modelPicker(provider: provider, list: models, loading: loadingModels, value: $model, custom: $custom)
                    if let e = models?.error { ErrorText(e, small: true) }
                    if models?.stale == true { MutedText(L(R.string.model_list_stale), small: true) }
                    effortField
                } else {
                    Text(emphasized(L(R.string.model_server_default_note), ".env", code: true))
                        .font(KeloFont.small).foregroundStyle(c.muted).fixedSize(horizontal: false, vertical: true)
                }
                Text(L(R.string.model_sheet_fallback)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                MutedText(L(R.string.model_fallback_help), small: true)
                SelectField(title: L(R.string.model_sheet_fallback), options: fallbackOptions(), selected: fb.provider,
                            groupLabel: L(R.string.model_picker_unofficial)) { picked in
                    gate.request(providers.first { $0.name == picked }) {
                        fbNote = false
                        if picked != fb.provider { fb = ModelChoice(picked, ""); fbCustom = false; loadFallbackModels(picked) }
                    }
                }
                if !fb.provider.isEmpty {
                    modelPicker(provider: fb.provider, list: fbModels, loading: fbLoading, value: Binding(get: { fb.model }, set: { fb.model = $0 }), custom: $fbCustom)
                }
                if fbNote { MutedText(L(R.string.model_sheet_fallback_cleared), small: true) }
                // background work (look-back, memory, journal, ...): any provider, the main one too; empty = the main model
                Text(L(R.string.model_background_title)).font(KeloFont.titleSmall).foregroundStyle(c.ink)
                MutedText(L(R.string.model_background_help), small: true)
                SelectField(title: L(R.string.model_background_title), options: backgroundOptions(), selected: bg.provider,
                            groupLabel: L(R.string.model_picker_unofficial)) { picked in
                    gate.request(providers.first { $0.name == picked }) {
                        if picked != bg.provider { bg = ModelChoice(picked, ""); bgCustom = false; loadBackgroundModels(picked) }
                    }
                }
                if !bg.provider.isEmpty {
                    modelPicker(provider: bg.provider, list: bgModels, loading: bgLoading, value: Binding(get: { bg.model }, set: { bg.model = $0 }), custom: $bgCustom)
                }
                if let f = UsageLogic.fallbackShown(bgState, provider: bg.provider, model: bg.model) {
                    MutedText(L(R.string.model_background_fell_back, MyFormat.monthDayTime(f.at)), small: true)
                }
                PrimaryButton(L(R.string.save), enabled: !saving && ModelRules.complete(ModelChoice(provider, model)) && ModelRules.complete(fb) && ModelRules.complete(bg),
                              busy: saving) { save() }
                if !err.isEmpty { ErrorText(err, small: true) }
            }
        }
        .onAppear { prefill() }
        .task {
            guard !started else { return }
            started = true
            let p = app.profile ?? Profile()
            prefill()
            await loadProviders()
            loadModels(provider)
            if !fb.provider.isEmpty { loadFallbackModels(fb.provider) }
            if !bg.provider.isEmpty { loadBackgroundModels(bg.provider) }
            // the background model's last failure, if any (an older server has no such page: nothing shows)
            bgState = (try? await app.requireApi().get("/api/usage/summary?days=1", as: UsageSummary.self))?.background
            // a saved unofficial choice not agreed to yet: asked once, kept either way
            if let p = ProviderConsentRules.pending(main: p.provider, fallback: p.fallbackProvider, providers: providers).first(where: { !gate.alreadyAsked($0) }) {
                gate.request(p) {}
            }
        }
        .onChange(of: providers) { _, list in
            guard let p = list.first(where: { $0.name == provider }), let cli = p.cli, !cli.installed, cli.installable, !asked.contains(p.name) else { return }
            asked.insert(p.name)
            askInstall = p
        }
        .keloSheet(isPresented: Binding(get: { gate.asking != nil }, set: { if !$0 && gate.asking != nil { gate.decline() } })) {
            ConsentSheet(gate: gate)
        }
        .alert(askInstall.map { L(R.string.model_cli_install_confirm, $0.displayName) } ?? "",
               isPresented: Binding(get: { askInstall != nil }, set: { if !$0 { askInstall = nil } })) {
            Button(L(R.string.model_cli_install)) { if let p = askInstall { install(p) } }
            Button(L(R.string.cancel), role: .cancel) {}
        }
        .keloConfirm(L(R.string.model_key_delete_confirm), isPresented: $confirmRemoveKey, yes: L(R.string.delete)) { removeKey() }
    }

    // ---- pieces ---------------------------------------------------------------------------------------------------------------------

    private func providerOptions() -> [SelectOption] {
        let sorted = providers.sorted { a, b in
            let ka = (ProviderConsentRules.group(a), a.configured == true ? 0 : 1, a.displayName.lowercased())
            let kb = (ProviderConsentRules.group(b), b.configured == true ? 0 : 1, b.displayName.lowercased())
            return ka < kb
        }
        let none = app.profile?.modelReady == false ? R.string.model_picker_choose_provider : R.string.model_sheet_server_default
        return [SelectOption(value: "", label: L(none))] + sorted.map { p in
            let label: String = switch p.configured {
            case true?: L(R.string.model_picker_ready, p.displayName)
            case false?: L(R.string.model_picker_needs_setup, p.displayName)
            case nil: p.displayName
            }
            return SelectOption(value: p.name, label: label, group: ProviderConsentRules.group(p))
        }
    }

    private func fallbackOptions() -> [SelectOption] {
        let others = ModelRules.fallbackProviders(providers, main: provider)
            .sorted { (ProviderConsentRules.group($0), $0.displayName.lowercased()) < (ProviderConsentRules.group($1), $1.displayName.lowercased()) }
        return [SelectOption(value: "", label: L(R.string.model_sheet_no_fallback))]
            + others.map { SelectOption(value: $0.name, label: $0.displayName, group: ProviderConsentRules.group($0)) }
    }

    private func backgroundOptions() -> [SelectOption] {
        let all = providers.sorted { (ProviderConsentRules.group($0), $0.displayName.lowercased()) < (ProviderConsentRules.group($1), $1.displayName.lowercased()) }
        return [SelectOption(value: "", label: L(R.string.model_background_same))]
            + all.map { SelectOption(value: $0.name, label: $0.displayName, group: ProviderConsentRules.group($0)) }
    }

    private func loadBackgroundModels(_ p: String) {
        let kept = ModelCatalog.keptList(app.cache, p, as: ModelList.self)
        if bgModels?.provider != p { bgModels = kept }   // that provider's own last list at once
        bgLoading = true
        Task {
            let m = await freshList(p, kept: kept)
            guard p == bg.provider else { return }
            bgLoading = false
            bgModels = m
            bgCustom = m.models.isEmpty || (!bg.model.isEmpty && !m.models.contains(bg.model))
        }
    }

    /// The profile's choice and the last provider and model lists (kept on the phone, `ModelCatalog`) at once, before anything is
    /// fetched: the fields show names straight away and the fresh answers replace them in place.
    private func prefill() {
        guard !prefilled else { return }
        prefilled = true
        let p = app.profile ?? Profile()
        provider = p.provider; model = p.model; effort = p.effort; fb = ModelRules.initialFallback(p)
        bg = ModelChoice(p.backgroundProvider, p.backgroundModel)
        if providers.isEmpty, let raw = ModelCatalog.keptProviders(app.cache) { providers = MyProvider.list(raw) }
        func kept(_ provider: String, _ chosen: String) -> (ModelList?, Bool) {
            let l = ModelCatalog.keptList(app.cache, provider, as: ModelList.self)
            return (l, l.map { !chosen.isEmpty && !$0.models.contains(chosen) } ?? false)
        }
        (models, custom) = kept(provider, model)
        (fbModels, fbCustom) = kept(fb.provider, fb.model)
        (bgModels, bgCustom) = kept(bg.provider, bg.model)
        // the lists are fetched right after: until then a field with nothing kept shows the chosen model by name
        loadingModels = !provider.isEmpty
        fbLoading = !fb.provider.isEmpty
        bgLoading = !bg.provider.isEmpty
    }

    /// A provider's fresh list, or after a failure the kept one (marked stale), or with nothing kept the reason.
    private func freshList(_ p: String, refresh: Bool = false, kept: ModelList?) async -> ModelList {
        do {
            let m = try await ModelCatalog.fetch(app.requireApi(), app.cache, provider: p, refresh: refresh, as: ModelList.self)
            return ModelCatalog.shown(m, kept) ?? m
        } catch {
            let failed = ModelList.catalogFailure(provider: p, error: L(R.string.model_picker_list_failed))
            return ModelCatalog.shown(failed, kept) ?? failed
        }
    }

    /// The thinking level, as in the chat's model sheet; a server that turned it away: its sentence, quietly
    @ViewBuilder private var effortField: some View {
        switch ModelEffortRules.effort(current, facts: models?.provider == provider ? models?.info[model] : nil) {
        case .levels(let levels):
            FieldLabel(L(R.string.chat_effort))
            SelectField(title: L(R.string.chat_effort), options: levels.map { SelectOption(value: $0, label: Self.effortWord($0)) },
                        selected: ModelEffortRules.shownLevel(levels, effort)) { v in effort = v; effortTouched = true }
            MutedText(L(R.string.chooser_effort_hint), small: true)
        case .note(let n):
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
                + [SelectOption(value: customValue, label: L(R.string.model_picker_custom))]
            SelectField(title: L(R.string.model_picker_model), options: opts, selected: custom.wrappedValue ? customValue : value.wrappedValue) { v in
                if v == customValue { custom.wrappedValue = true } else { custom.wrappedValue = false; value.wrappedValue = v }
            }
        }
        if custom.wrappedValue || (shownList?.models.isEmpty ?? true) {
            KeloTextField(L(R.string.model_picker_custom_placeholder), text: value, keyboard: .ascii, maxLength: 200)
        }
    }

    @ViewBuilder private func cliBox(_ cli: CliStatus, _ p: MyProvider, signInHere: Bool = true) -> some View {
        KeyBox {
            let state: String = {
                if installing { return L(R.string.model_cli_installing) }
                if !cli.installed { return L(cli.remote ? R.string.model_cli_unreachable : R.string.model_cli_not_installed) }
                if cli.loggedIn == false { return L(R.string.model_cli_login_needed) }
                if cli.loggedIn == true { return L(cli.remote ? R.string.model_cli_ok_remote : R.string.model_cli_ok_local) }
                return L(R.string.model_cli_ok_unknown)
            }()
            MutedText(state, small: true)
            if !cli.installed && cli.installable && !installing { SecondaryButton(L(R.string.model_cli_install)) { install(p) } }
            let signInBelow = signInHere && p.signin && cli.installed && cli.loggedIn != true
            if (!cli.installed || cli.loggedIn != true) && !installing && !cli.hint.isEmpty && !signInBelow { MutedText(cli.hint, small: true) }
            if cli.installed {
                let parts = [cli.version.flatMap { $0.isEmpty ? nil : L(R.string.model_cli_version, $0) },
                             cli.latestVersion.flatMap { cli.updateAvailable && !$0.isEmpty ? L(R.string.model_cli_update_available, $0) : nil },
                             cli.updateHint.flatMap { $0.isEmpty || cli.updatable ? nil : $0 }].compactMap { $0 }   // the button does it
                if !parts.isEmpty { MutedText(parts.joined(separator: L(R.string.usage_group_joiner)), small: true) }
            }
            if updating == p.name { MutedText(L(R.string.model_cli_updating), small: true) }
            else if CliUpdateRules.showButton(cli, busy: installing || updating != nil) { SecondaryButton(L(R.string.model_cli_update_button)) { updateCli(p) } }
            if let u = updateOutcome, u.0 == p.name {
                switch u.1 {
                case .updated(let product, let version): MutedText(L(R.string.model_cli_update_done, product, version), small: true)
                case .unchanged(let product): MutedText(L(R.string.model_cli_update_unchanged, product), small: true)
                case .failed(let error): ErrorText(L(R.string.model_cli_update_failed, error), small: true)
                case .running: EmptyView()
                }
            }
            if !installMsg.isEmpty { MutedText(installMsg, small: true) }
        }
    }

    @ViewBuilder private func urlBox(_ cur: MyProvider) -> some View {
        KeyBox {
            let saved = cur.baseUrl ?? ""
            if !saved.isEmpty && !editingUrl {
                MutedText(L(R.string.model_url_set, saved), small: true)
                LinkButton(L(R.string.model_url_change)) { urlInput = saved; urlMsg = ""; editingUrl = true }
            } else {
                FieldLabel(L(R.string.model_url_label))
                KeloTextField(L(R.string.model_url_placeholder), text: $urlInput, keyboard: .url, maxLength: 300) { saveUrl() }
                PrimaryButton(L(R.string.save), enabled: !savingUrl && !urlInput.trimmingCharacters(in: .whitespaces).isEmpty) { saveUrl() }
                MutedText(L(R.string.model_url_help), small: true)
                if !saved.isEmpty { LinkButton(L(R.string.cancel)) { editingUrl = false; urlInput = ""; urlMsg = "" } }
            }
            if !urlMsg.isEmpty { ErrorText(urlMsg, small: true) }
        }
    }

    @ViewBuilder private func keyBox(_ cur: MyProvider) -> some View {
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
                PrimaryButton(L(R.string.save), enabled: KeyRules.keyReady(keyInput)) { saveKey() }
            }
            if !keyMsg.isEmpty { MutedText(keyMsg, small: true) }
        }
    }

    // ---- actions ----------------------------------------------------------------------------------------------------------------------

    private func pickProvider(_ v: String) {
        let (next, cleared) = ModelRules.afterMainChange(ModelChoice(v, model), fb)
        fb = next
        fbNote = cleared
        if cleared { fbModels = nil; fbCustom = false }
        if v != provider {
            urlInput = ""; editingUrl = false; urlMsg = ""
            model = ""; custom = false
            models = ModelCatalog.keptList(app.cache, v, as: ModelList.self)   // that provider's own last list, never the previous one's
        }
        provider = v
        loadModels(v)
    }

    private func loadProviders() async {
        if let raw = try? await ModelCatalog.fetchProviders(app.requireApi(), app.cache) { providers = MyProvider.list(raw) }
    }

    /// The kept list stays while the fresh one loads and is replaced in place; the 새로고침 link says "loading" only after a second.
    private func loadModels(_ p: String, refresh: Bool = false) {
        guard !p.isEmpty else { models = nil; loadingModels = false; slowModels = false; return }
        let kept = ModelCatalog.keptList(app.cache, p, as: ModelList.self)
        if models?.provider != p { models = kept }   // another provider: its own last list at once, never the previous one's
        loadingModels = true
        slowModels = false
        Task {
            let hint = Task { @MainActor in
                try? await Task.sleep(nanoseconds: ModelCatalog.slowNanos)
                if !Task.isCancelled && p == provider { slowModels = true }
            }
            let m = await freshList(p, refresh: refresh, kept: kept)
            hint.cancel()
            guard p == provider else { return }   // the user picked another provider meanwhile
            models = m
            if m.error == nil || m.stale { custom = m.models.isEmpty || (!model.isEmpty && !m.models.contains(model)) }
            loadingModels = false
            slowModels = false
        }
    }

    private func loadFallbackModels(_ p: String) {
        let kept = ModelCatalog.keptList(app.cache, p, as: ModelList.self)
        if fbModels?.provider != p { fbModels = kept }   // that provider's own last list at once
        fbLoading = true
        Task {
            let m = await freshList(p, kept: kept)
            guard p == fb.provider else { return }
            fbLoading = false
            fbModels = m
            fbCustom = m.models.isEmpty || (!fb.model.isEmpty && !m.models.contains(fb.model))
        }
    }

    /// 업데이트 in the CLI box (owner only; Android my/ModelPage.kt updateCli): the old version keeps working when it fails.
    private func updateCli(_ p: MyProvider) {
        guard updating == nil else { return }
        updating = p.name; updateOutcome = nil
        Task {
            if let api = try? app.requireApi() {
                let st = await CliUpdateRules.run(api, provider: p.name)
                updateOutcome = (p.name, CliUpdateRules.outcome(st, fallbackProduct: p.displayName))
            }
            updating = nil
            await loadProviders()
        }
    }

    private func install(_ p: MyProvider) {
        installing = true; installMsg = ""
        Task {
            do {
                try await app.requireApi().request("POST", "/api/providers/\(KeloApi.segment(p.name))/install")
                installMsg = L(R.string.model_cli_installed_msg)
            } catch {
                installMsg = app.message(for: error)
            }
            installing = false
            await loadProviders()
        }
    }

    private func saveKey() {
        keyMsg = ""
        Task {
            do {
                try await app.requireApi().request("PUT", "/api/providers/\(KeloApi.segment(provider))/key",
                                                   body: ["api_key": .string(keyInput.trimmingCharacters(in: .whitespacesAndNewlines))])
                keyInput = ""
                await loadProviders()
                loadModels(provider, refresh: true)
            } catch {
                keyMsg = app.message(for: error)
            }
        }
    }

    private func removeKey() {
        Task {
            _ = try? await app.requireApi().request("DELETE", "/api/providers/\(KeloApi.segment(provider))/key")
            keyMsg = ""
            await loadProviders()
            loadModels(provider, refresh: true)
        }
    }

    private func saveUrl() {
        if let problem = EndpointRules.check(urlInput) {
            urlMsg = switch problem {
            case .empty: L(R.string.model_url_empty)
            case .spaces: L(R.string.model_url_spaces)
            case .scheme: L(R.string.model_url_scheme)
            case .host: L(R.string.model_url_host)
            }
            return
        }
        let p = provider
        urlMsg = ""; savingUrl = true
        Task {
            do {
                try await app.requireApi().request("PUT", "/api/providers/\(KeloApi.segment(p))/endpoint",
                                                   body: ["base_url": .string(urlInput.trimmingCharacters(in: .whitespaces))])
                urlInput = ""; editingUrl = false
                await loadProviders()
                loadModels(p, refresh: true)
            } catch {
                urlMsg = app.message(for: error)
            }
            savingUrl = false
        }
    }

    private func save() {
        guard !saving else { return }
        err = ""; saving = true
        Task {
            do {
                var fields = [
                    "provider": provider, "model": model.trimmingCharacters(in: .whitespaces),
                    "fallback_provider": fb.provider, "fallback_model": fb.model.trimmingCharacters(in: .whitespaces),
                    "background_provider": bg.provider, "background_model": bg.provider.isEmpty ? "" : bg.model.trimmingCharacters(in: .whitespaces),
                ]
                if effortTouched { fields["effort"] = effort }
                let p = try await app.requireApi().updateProfile(fields)
                app.setProfile(p)
                saving = false
                dismiss()   // saved: back to 마이 (an error keeps the page open with the reason)
            } catch let e as ApiError {
                saving = false
                if let n = ProviderConsentRules.requiredBy(e), var needed = providers.first(where: { $0.name == n }) {
                    needed.consent = nil
                    gate.request(needed) { save() }
                } else {
                    err = e.isNetwork ? L(R.string.connect_failed) : e.detail
                }
            } catch {
                saving = false
                err = app.message(for: error)
            }
        }
    }
}
