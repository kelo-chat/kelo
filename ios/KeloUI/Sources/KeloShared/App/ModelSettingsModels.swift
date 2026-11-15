import SwiftUI
import KeloCore


/// What a provider list remembers about consent while it is open (Android ConsentGate): what was agreed here and the question.
@MainActor @Observable public final class ConsentGate {
    public private(set) var asking: ModelProvider?
    public private(set) var accepted: Set<String> = []
    public private(set) var error: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let prefs: Prefs
    @ObservationIgnored private var onYes: () -> Void = {}
    @ObservationIgnored private var onNo: () -> Void = {}
    @ObservationIgnored let jobs = Jobs()

    public init(api: KeloApi, prefs: Prefs) { self.api = api; self.prefs = prefs }

    public func needs(_ p: ModelProvider) -> Bool { !accepted.contains(p.name) && ProviderConsentRules.needs(p) }

    /// Runs `then` at once when `p` needs no agreement, else after the user agreed (and the server recorded it).
    public func request(_ p: ModelProvider?, onDecline: @escaping () -> Void = {}, then: @escaping () -> Void) {
        guard let p, needs(p) else { then(); return }
        onYes = then; onNo = onDecline; error = nil; asking = p
    }

    public func alreadyAsked(_ p: ModelProvider) -> Bool { prefs.bool(ProviderConsentRules.askedKey(p), default: false) }
    private func markAsked(_ p: ModelProvider) { prefs.set(ProviderConsentRules.askedKey(p), true) }

    public func decline() {
        if let p = asking { markAsked(p) }
        asking = nil
        onNo()
    }

    public func agree() {
        guard let p = asking else { return }
        jobs.run { [self] in
            do {
                try await api.request("PUT", "/api/providers/\(KeloApi.segment(p.name))/consent",
                                      body: ["accepted": true, "version": .number(Double(p.consentVersion))])
                accepted.insert(p.name)
                markAsked(p)
                asking = nil
                onYes()
            } catch {
                self.error = ApiMessage.text(error)
            }
        }
    }
}

/// 로그인 for a provider reached by signing in: start (consent first), the code to type in the browser or to paste back, follow the
/// session until it ends. The page's `.task(id: session)` runs `follow()`, so nothing runs when the page is gone.
@MainActor @Observable final class SignInModel {
    private(set) var s: SignInSession?
    private(set) var busy = false
    private(set) var err = ""
    private(set) var deadline = Date()
    private(set) var now = Date()
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let provider: ModelProvider
    @ObservationIgnored private let gate: ConsentGate
    @ObservationIgnored private let onSignedIn: () -> Void
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, provider: ModelProvider, gate: ConsentGate, onSignedIn: @escaping () -> Void) {
        self.api = api; self.provider = provider; self.gate = gate; self.onSignedIn = onSignedIn
    }

    private var path: String { "/api/providers/\(KeloApi.segment(provider.name))/login" }

    func setError(_ text: String) { err = text }
    func clearError() { err = "" }

    private func take(_ next: SignInSession) {
        s = next
        if next.state == "pending" { deadline = Date().addingTimeInterval(Double(next.expiresIn)) }
        if SignInRules.finished(next) { onSignedIn() }
    }

    private func fail(_ e: Error) {
        guard let a = e as? ApiError else { err = L(R.string.signin_failed); return }
        if SignInRules.problem(a) == .already { err = L(R.string.signin_already); return }
        let r = SignInRules.readable(a)
        err = r.isEmpty ? L(R.string.signin_failed) : r
    }

    /// The button: consent first (when not given), then the session starts.
    func begin() { gate.request(provider) { [self] in start() } }

    func start() {
        guard !busy else { return }
        busy = true; err = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                take(try await api.post("\(path)/start", body: .object([:])))
            } catch let e as ApiError {
                switch SignInRules.problem(e) {
                case .consent:
                    var again = provider
                    again.consent = nil
                    gate.request(again) { [self] in start() }
                    return
                case .already: onSignedIn()
                default: break
                }
                fail(e)
            } catch {
                fail(error)
            }
        }
    }

    func finish(code: String, done: @escaping () -> Void = {}) {
        guard let cur = s else { return }
        busy = true; err = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                take(try await api.post("\(path)/finish", body: ["session": .string(cur.session), "code": .string(code.trimmingCharacters(in: .whitespacesAndNewlines))]))
                done()
            } catch {
                if let a = error as? ApiError, a.status == 404 || a.status == 410 { var x = cur; x.state = "expired"; s = x }
                fail(error)
            }
        }
    }

    func cancel() {
        guard let cur = s else { return }
        s = nil; err = ""
        jobs.run { [self] in _ = try? await api.request("POST", "\(path)/cancel", body: ["session": .string(cur.session)]) }
    }

    /// Back in the app (from the browser): the status is asked now.
    func appBecameActive() { jobs.run { [self] in await refresh() } }

    func refresh() async {
        guard let cur = s, cur.state == "pending", SignInRules.isDeviceCode(cur) else { return }
        do {
            let next = try await api.get("\(path)/status?session=\(KeloApi.query(cur.session))", as: SignInSession.self)
            if s?.session == cur.session { take(next) }   // the session is still the one asked about
        } catch let e as ApiError {
            if e.status == 404, s?.session == cur.session { var x = cur; x.state = "expired"; s = x }
        } catch {}
    }

    /// While the session is pending (the page's `.task(id: session)`): the countdown moves each second, the status is asked every 3.
    func follow() async {
        guard let cur = s, cur.state == "pending" else { return }
        var ticks = 0
        while !Task.isCancelled, s?.state == "pending" {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            now = Date()
            ticks += 1
            if ticks % 3 == 0 { await refresh() }
        }
    }
}

/// 로그아웃 of a provider's sign-in, with its result for the page to word.
@MainActor @Observable final class SignOutModel {
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let provider: ModelProvider
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, provider: ModelProvider) { self.api = api; self.provider = provider }

    func signOut(onResult: @escaping (SignOutRules.Outcome, SignOutResult?, String) -> Void) {
        busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                let r: SignOutResult = try await api.delete("/api/providers/\(KeloApi.segment(provider.name))/login")
                onResult(SignOutRules.outcome(r, nil), r, "")
            } catch let e as ApiError {
                onResult(SignOutRules.outcome(nil, e), nil, e.isNetwork ? "" : e.detail)
            } catch {
                onResult(.failed, nil, "")
            }
        }
    }
}

/// 마이 → 모델: which AI service and model answer, the fallback and the background model, the service's key, address and CLI. The page
/// reads this and calls its methods. Each list (main, fallback, background) and the providers load through their own task, and a newer
/// load of the same kind cancels the older one, so an old answer never replaces a newer one (or the list of a provider picked since).
@MainActor @Observable final class ModelSettingsModel {
    static let customValue = "__custom__"

    private(set) var provider = ""
    private(set) var model = ""
    private(set) var effort = ""
    private(set) var fb = ModelChoice.none
    private(set) var bg = ModelChoice.none                 // the background work model; none = the main model
    private(set) var fbNote = false
    private(set) var providers: [ModelProvider] = []
    private(set) var models: ModelList?
    private(set) var fbModels: ModelList?
    private(set) var bgModels: ModelList?
    private(set) var bgState: BackgroundModelState?
    private(set) var custom = false
    private(set) var fbCustom = false
    private(set) var bgCustom = false
    private(set) var loadingModels = false
    /// a refresh over `ModelCatalog.slowMs`: the 새로고침 link says it is loading
    private(set) var slowModels = false
    private(set) var fbLoading = false
    private(set) var bgLoading = false
    private(set) var saving = false
    private(set) var err = ""
    /// Goes up when the choice was saved: the page closes.
    private(set) var savedTick = 0
    // the service's box
    private(set) var keyMsg = ""
    private(set) var installing = false
    private(set) var installMsg = ""
    private(set) var updating: String?                                   // the CLI provider being updated
    private(set) var updateOutcome: (String, CliUpdateRules.Outcome)?    // provider name, how it went
    private(set) var urlMsg = ""
    private(set) var savingUrl = false
    private(set) var endpointMsg = ""
    private(set) var savingEndpoint = false
    private(set) var signOut: (String, SignOutRules.Outcome)?
    private(set) var signOutDetail = ""
    /// A provider whose CLI is not installed on the server: the page asks once whether to install it.
    private(set) var askInstall: ModelProvider?
    let gate: ConsentGate

    @ObservationIgnored let api: KeloApi
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private unowned let host: any ProfileHost
    @ObservationIgnored private var started = false
    @ObservationIgnored private var effortTouched = false
    @ObservationIgnored private var asked: Set<String> = []
    @ObservationIgnored var providersLoad: Task<Void, Never>?
    @ObservationIgnored var mainLoad: Task<Void, Never>?
    @ObservationIgnored var fbLoad: Task<Void, Never>?
    @ObservationIgnored var bgLoad: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, cache: ScreenCache, prefs: Prefs, host: any ProfileHost) {
        self.api = api; self.cache = cache; self.host = host
        gate = ConsentGate(api: api, prefs: prefs)
        prefill()
    }

    var current: ModelProvider? { providers.first { $0.name == provider } }
    var mainProvider: String { host.profile?.provider ?? "" }
    var modelReady: Bool? { host.profile?.modelReady }
    var effortFacts: ModelEffort? { models?.provider == provider ? models?.info[model] : nil }

    // ---- what the person edits -------------------------------------------------------------------------------------------------------

    func setModel(_ v: String) { model = v }
    func setFbModel(_ v: String) { fb.model = v }
    func setBgModel(_ v: String) { bg.model = v }
    func setCustom(_ v: Bool) { custom = v }
    func setFbCustom(_ v: Bool) { fbCustom = v }
    func setBgCustom(_ v: Bool) { bgCustom = v }
    func setEffort(_ v: String) { effort = v; effortTouched = true }
    func clearAskInstall() { askInstall = nil }

    // ---- loading ---------------------------------------------------------------------------------------------------------------------

    /// The profile's choice and the last provider and model lists (kept on the phone, `ModelCatalog`) at once, before anything is
    /// fetched: the fields show names straight away and the fresh answers replace them in place.
    private func prefill() {
        let p = host.profile ?? Profile()
        provider = p.provider; model = p.model; effort = p.effort; fb = ModelRules.initialFallback(p)
        bg = ModelChoice(p.backgroundProvider, p.backgroundModel)
        if let raw = ModelCatalog.keptProviders(cache) { providers = ModelProvider.list(raw) }
        func kept(_ provider: String, _ chosen: String) -> (ModelList?, Bool) {
            let l = ModelCatalog.keptList(cache, provider, as: ModelList.self)
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

    /// Opening the page: the providers, the three lists, the background model's last failure, the consent still to ask.
    func start() async {
        guard !started else { return }
        started = true
        let p = host.profile ?? Profile()
        await loadProviders()
        loadModels(provider)
        if !fb.provider.isEmpty { loadFallbackModels(fb.provider) }
        if !bg.provider.isEmpty { loadBackgroundModels(bg.provider) }
        // the background model's last failure, if any (an older server has no such page: nothing shows)
        bgState = (try? await api.get("/api/usage/summary?days=1", as: UsageSummary.self))?.background
        // a saved unofficial choice not agreed to yet: asked once, kept either way
        if let p = ProviderConsentRules.pending(main: p.provider, fallback: p.fallbackProvider, providers: providers).first(where: { !gate.alreadyAsked($0) }) {
            gate.request(p) {}
        }
    }

    func loadProviders() async {
        providersLoad?.cancel()
        let task = Task { @MainActor [self] in
            guard let raw = try? await ModelCatalog.fetchProviders(api, cache), !Task.isCancelled else { return }
            providers = ModelProvider.list(raw)
            askToInstallIfNeeded()
        }
        providersLoad = task
        await task.value
    }

    /// The selected service's CLI is missing on the server and can be installed: asked once per service.
    private func askToInstallIfNeeded() {
        guard let p = providers.first(where: { $0.name == provider }), let cli = p.cli, !cli.installed, cli.installable, !asked.contains(p.name) else { return }
        asked.insert(p.name)
        askInstall = p
    }

    /// A provider's fresh list, or after a failure the kept one (marked stale), or with nothing kept the reason.
    private func freshList(_ p: String, refresh: Bool = false, kept: ModelList?) async -> ModelList {
        do {
            let m = try await ModelCatalog.fetch(api, cache, provider: p, refresh: refresh, as: ModelList.self)
            return ModelCatalog.shown(m, kept) ?? m
        } catch {
            let failed = ModelList.catalogFailure(provider: p, error: L(R.string.model_picker_list_failed))
            return ModelCatalog.shown(failed, kept) ?? failed
        }
    }

    /// The kept list stays while the fresh one loads and is replaced in place; the 새로고침 link says "loading" only after a second.
    func loadModels(_ p: String, refresh: Bool = false) {
        mainLoad?.cancel()
        guard !p.isEmpty else { models = nil; loadingModels = false; slowModels = false; return }
        let kept = ModelCatalog.keptList(cache, p, as: ModelList.self)
        if models?.provider != p { models = kept }   // another provider: its own last list at once, never the previous one's
        loadingModels = true
        slowModels = false
        mainLoad = Task { @MainActor [self] in
            let hint = Task { @MainActor in
                try? await Task.sleep(nanoseconds: ModelCatalog.slowNanos)
                if !Task.isCancelled && p == provider { slowModels = true }
            }
            let m = await freshList(p, refresh: refresh, kept: kept)
            hint.cancel()
            guard !Task.isCancelled, p == provider else { return }   // a newer load, or another provider picked meanwhile
            models = m
            if m.error == nil || m.stale { custom = m.models.isEmpty || (!model.isEmpty && !m.models.contains(model)) }
            loadingModels = false
            slowModels = false
        }
    }

    func refreshModels() { if !loadingModels { loadModels(provider, refresh: true) } }

    private func loadFallbackModels(_ p: String) {
        fbLoad?.cancel()
        let kept = ModelCatalog.keptList(cache, p, as: ModelList.self)
        if fbModels?.provider != p { fbModels = kept }   // that provider's own last list at once
        fbLoading = true
        fbLoad = Task { @MainActor [self] in
            let m = await freshList(p, kept: kept)
            guard !Task.isCancelled, p == fb.provider else { return }
            fbLoading = false
            fbModels = m
            fbCustom = m.models.isEmpty || (!fb.model.isEmpty && !m.models.contains(fb.model))
        }
    }

    private func loadBackgroundModels(_ p: String) {
        bgLoad?.cancel()
        let kept = ModelCatalog.keptList(cache, p, as: ModelList.self)
        if bgModels?.provider != p { bgModels = kept }   // that provider's own last list at once
        bgLoading = true
        bgLoad = Task { @MainActor [self] in
            let m = await freshList(p, kept: kept)
            guard !Task.isCancelled, p == bg.provider else { return }
            bgLoading = false
            bgModels = m
            bgCustom = m.models.isEmpty || (!bg.model.isEmpty && !m.models.contains(bg.model))
        }
    }

    // ---- choosing --------------------------------------------------------------------------------------------------------------------

    func pickProvider(_ v: String) {
        gate.request(providers.first { $0.name == v }) { [self] in applyProvider(v) }
    }

    private func applyProvider(_ v: String) {
        let (next, cleared) = ModelRules.afterMainChange(ModelChoice(v, model), fb)
        fb = next
        fbNote = cleared
        if cleared { fbModels = nil; fbCustom = false }
        if v != provider {
            urlMsg = ""
            model = ""; custom = false
            models = ModelCatalog.keptList(cache, v, as: ModelList.self)   // that provider's own last list, never the previous one's
        }
        provider = v
        loadModels(v)
    }

    func pickFallback(_ picked: String) {
        gate.request(providers.first { $0.name == picked }) { [self] in
            fbNote = false
            if picked != fb.provider { fb = ModelChoice(picked, ""); fbCustom = false; loadFallbackModels(picked) }
        }
    }

    func pickBackground(_ picked: String) {
        gate.request(providers.first { $0.name == picked }) { [self] in
            if picked != bg.provider { bg = ModelChoice(picked, ""); bgCustom = false; loadBackgroundModels(picked) }
        }
    }

    // ---- the service's box -----------------------------------------------------------------------------------------------------------

    /// Signed in with a sign-in box: the lists are read again.
    func signedIn() {
        signOut = nil
        jobs.run { [self] in await loadProviders(); loadModels(provider, refresh: true) }
    }

    /// The sign-out ended: the choices that were on that sign-in are cleared by the server, so the profile is read again.
    func signedOut(_ cur: ModelProvider, _ outcome: SignOutRules.Outcome, _ result: SignOutResult?, _ detail: String) {
        signOut = (cur.name, outcome); signOutDetail = detail
        guard SignOutRules.done(outcome) else { return }
        if result?.fallbackCleared == true && fb.provider == cur.name { fb = .none }
        jobs.run { [self] in
            await loadProviders()
            if let p = try? await api.profile() {
                host.setProfile(p)
                bg = ModelChoice(p.backgroundProvider, p.backgroundModel)   // a background model on that sign-in is emptied
            }
        }
    }

    /// 업데이트 in the CLI box (owner only; Android my/ModelPage.kt updateCli): the old version keeps working when it fails.
    func updateCli(_ p: ModelProvider) {
        guard updating == nil else { return }
        updating = p.name; updateOutcome = nil
        jobs.run { [self] in
            let st = await CliUpdateRules.run(api, provider: p.name)
            updateOutcome = (p.name, CliUpdateRules.outcome(st, fallbackProduct: p.displayName))
            updating = nil
            await loadProviders()
        }
    }

    func install(_ p: ModelProvider) {
        installing = true; installMsg = ""
        jobs.run { [self] in
            do {
                try await api.request("POST", "/api/providers/\(KeloApi.segment(p.name))/install")
                installMsg = L(R.string.model_cli_installed_msg)
            } catch {
                installMsg = ApiMessage.text(error)
            }
            installing = false
            await loadProviders()
        }
    }

    func saveKey(_ key: String, saved: @escaping () -> Void) {
        keyMsg = ""
        let p = provider
        jobs.run { [self] in
            do {
                try await api.request("PUT", "/api/providers/\(KeloApi.segment(p))/key", body: ["api_key": .string(key.trimmingCharacters(in: .whitespacesAndNewlines))])
                saved()
                await loadProviders()
                loadModels(p, refresh: true)
            } catch {
                keyMsg = ApiMessage.text(error)
            }
        }
    }

    func removeKey() {
        let p = provider
        jobs.run { [self] in
            _ = try? await api.request("DELETE", "/api/providers/\(KeloApi.segment(p))/key")
            keyMsg = ""
            await loadProviders()
            loadModels(p, refresh: true)
        }
    }

    func clearUrlMsg() { urlMsg = "" }

    func saveUrl(_ url: String, saved: @escaping () -> Void) {
        if let problem = EndpointRules.check(url) {
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
        jobs.run { [self] in
            defer { savingUrl = false }
            do {
                try await api.request("PUT", "/api/providers/\(KeloApi.segment(p))/endpoint", body: ["base_url": .string(url.trimmingCharacters(in: .whitespaces))])
                saved()
                await loadProviders()
                loadModels(p, refresh: true)
            } catch {
                urlMsg = ApiMessage.text(error)
            }
        }
    }

    func clearEndpointMsg() { endpointMsg = "" }

    func addEndpoint(name: String, baseUrl: String, done: @escaping () -> Void = {}) {
        let trimmedName = name.trimmingCharacters(in: .whitespaces)
        let trimmedUrl = baseUrl.trimmingCharacters(in: .whitespaces)
        if let problem = EndpointRules.check(trimmedUrl) {
            endpointMsg = switch problem {
            case .empty: L(R.string.model_url_empty)
            case .spaces: L(R.string.model_url_spaces)
            case .scheme: L(R.string.model_url_scheme)
            case .host: L(R.string.model_url_host)
            }
            return
        }
        endpointMsg = ""; savingEndpoint = true
        jobs.run { [self] in
            defer { savingEndpoint = false }
            do {
                let created: [String: JSON] = try await api.post(
                    "/api/providers/custom-endpoints",
                    body: ["name": .string(trimmedName), "base_url": .string(trimmedUrl)]
                )
                let id = created["id"]?.stringValue ?? ""
                endpointMsg = ""
                await loadProviders()
                if !id.isEmpty {
                    applyProvider(id)
                }
                done()
            } catch let e as ApiError {
                switch e.code {
                case "name_taken": endpointMsg = L(R.string.endpoint_name_taken)
                case "too_many": endpointMsg = L(R.string.endpoint_too_many)
                default: endpointMsg = e.isNetwork ? L(R.string.connect_failed) : e.detail
                }
            } catch {
                endpointMsg = ApiMessage.text(error)
            }
        }
    }

    func renameEndpoint(_ id: String, name: String, done: @escaping () -> Void = {}) {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        endpointMsg = ""
        jobs.run { [self] in
            do {
                if id == "custom" {
                    _ = try await api.request("PUT", "/api/providers/custom/name", body: ["name": .string(trimmed)])
                } else {
                    _ = try await api.patch("/api/providers/custom-endpoints/\(KeloApi.segment(id))", body: ["name": .string(trimmed)], as: [String: JSON].self)
                }
                endpointMsg = ""
                await loadProviders()
                done()
            } catch let e as ApiError {
                if e.code == "name_taken" {
                    endpointMsg = L(R.string.endpoint_name_taken)
                } else {
                    endpointMsg = e.isNetwork ? L(R.string.connect_failed) : e.detail
                }
            } catch {
                endpointMsg = ApiMessage.text(error)
            }
        }
    }

    func deleteEndpoint(_ id: String, done: @escaping () -> Void = {}) {
        endpointMsg = ""
        jobs.run { [self] in
            do {
                _ = try await api.request("DELETE", "/api/providers/custom-endpoints/\(KeloApi.segment(id))")
                endpointMsg = ""
                await loadProviders()
                if provider == id {
                    applyProvider("")
                }
                done()
            } catch let e as ApiError {
                if e.code == "in_use" {
                    endpointMsg = L(R.string.endpoint_in_use)
                } else {
                    endpointMsg = e.isNetwork ? L(R.string.connect_failed) : e.detail
                }
            } catch {
                endpointMsg = ApiMessage.text(error)
            }
        }
    }

    // ---- saving the choice -----------------------------------------------------------------------------------------------------------

    func save() {
        guard !saving else { return }
        err = ""; saving = true
        jobs.run { [self] in
            do {
                var fields = [
                    "provider": provider, "model": model.trimmingCharacters(in: .whitespaces),
                    "fallback_provider": fb.provider, "fallback_model": fb.model.trimmingCharacters(in: .whitespaces),
                    "background_provider": bg.provider, "background_model": bg.provider.isEmpty ? "" : bg.model.trimmingCharacters(in: .whitespaces),
                ]
                if effortTouched { fields["effort"] = effort }
                let p = try await api.updateProfile(fields)
                host.setProfile(p)
                saving = false
                savedTick += 1   // saved: back to 마이 (an error keeps the page open with the reason)
            } catch let e as ApiError {
                saving = false
                if let n = ProviderConsentRules.requiredBy(e), var needed = providers.first(where: { $0.name == n }) {
                    needed.consent = nil
                    gate.request(needed) { [self] in save() }
                } else {
                    err = e.isNetwork ? L(R.string.connect_failed) : e.detail
                }
            } catch {
                saving = false
                err = ApiMessage.text(error)
            }
        }
    }
}
