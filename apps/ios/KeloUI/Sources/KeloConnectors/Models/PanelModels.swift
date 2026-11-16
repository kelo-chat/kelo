import SwiftUI
import Observation
import KeloCore
import KeloShared

/// A browser sign-in the view runs (the system sheet): true when it could be opened.
typealias BrowserSignIn = @MainActor (String) async -> Bool

/// Sign in on the service's own page (hosted MCP servers), in the system browser (Android Mcp).
@MainActor @Observable
final class McpModel {
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let id: String
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, id: String) { self.api = api; self.id = id }

    func signIn(browser: @escaping BrowserSignIn, changed: @escaping () -> Void) {
        err = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                let url = try await api.mcpSignInURL(id)
                if !(await browser(url)) { err = L(R.string.connector_browser_failed) }
                changed()
            } catch { err = ApiMessage.text(error) }
        }
    }

    func disconnect(item: String, name: String, changed: @escaping () -> Void, report: ForgetReporter) {
        jobs.run { [self] in
            if let ask = await ForgetRules.disconnect(api, item: item, name: name) { report.show(ask) }
            changed()
        }
    }
}

/// A mailbox with an app password, or the Google sign-in for Gmail (Android Mail).
@MainActor @Observable
final class MailModel {
    private(set) var data: MailProviders?
    private(set) var provider = ""
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let connectorId: String
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, connectorId: String) { self.api = api; self.connectorId = connectorId }

    func choose(_ p: String) { provider = p }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let d = try? await api.mailProviders(), !Task.isCancelled else { return }
            data = d
            if provider.isEmpty { provider = MailPanelForm.defaultProvider(connectorId: connectorId, providers: d.providers) }
        }
        loading = task
        await task.value
    }

    func returned() async { await load(); busy = false }

    func googleSignIn(browser: @escaping BrowserSignIn, changed: @escaping () -> Void) {
        err = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                let url = try await api.googleWebStart()
                if !(await browser(url)) { err = L(R.string.connector_browser_failed) }
                await load(); changed()
            } catch { err = ApiMessage.text(error) }
        }
    }

    /// `added` runs when the mailbox was saved (the form empties).
    func add(address: String, password: String, added: @escaping () -> Void, changed: @escaping () -> Void) {
        err = ""; busy = true
        let provider = provider
        jobs.run { [self] in
            defer { busy = false }
            do {
                try await api.addMail(provider: provider, address: address.trimmingCharacters(in: .whitespacesAndNewlines), password: password)
                added()
                await load(); changed()
            } catch { err = ApiMessage.text(error) }
        }
    }

    func remove(_ a: MailAccount, changed: @escaping () -> Void, report: ForgetReporter) {
        jobs.run { [self] in
            if let ask = await ForgetRules.disconnect(api, item: a.name, name: a.label) { report.show(ask) }
            await load(); changed()
        }
    }
}

/// A calendar from any CalDAV provider: checked once by the server before anything is saved (Android CalDav).
@MainActor @Observable
final class CalDavModel {
    private(set) var accounts: [CalDavAccount]?
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let got = try? await api.get("/api/caldav", as: CalDavAccounts.self), !Task.isCancelled else { return }
            accounts = got.accounts
        }
        loading = task
        await task.value
    }

    func returned() async { await load(); busy = false }

    func add(url: String, user: String, password: String, added: @escaping () -> Void, changed: @escaping () -> Void) {
        err = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                _ = try await api.request("POST", "/api/caldav/accounts", body: [
                    "url": .string(url.trimmingCharacters(in: .whitespaces)), "username": .string(user.trimmingCharacters(in: .whitespaces)),
                    "password": .string(password)])
                added()
                await load(); changed()
            } catch { err = ApiMessage.text(error) }
        }
    }

    func remove(_ a: CalDavAccount, changed: @escaping () -> Void, report: ForgetReporter) {
        jobs.run { [self] in
            if let ask = await ForgetRules.disconnect(api, item: a.name, name: a.address) { report.show(ask) }
            await load(); changed()
        }
    }
}

/// A key or token made on the service; the server tries it once before saving it (Android Key).
@MainActor @Observable
final class KeyModel {
    private(set) var form: KeyForm?
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let via: String
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, via: String) { self.api = api; self.via = via }

    func setError(_ text: String) { err = text }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let f = try? await api.keyForm(via), !Task.isCancelled else { return }
            form = f
        }
        loading = task
        await task.value
    }

    /// `saved` runs when the server accepted the key (the typed values go).
    func save(_ f: KeyForm, values: [String: String], saved: @escaping () -> Void, changed: @escaping () -> Void) {
        err = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                try await api.saveKey(f.id, values: values)
                saved()
                await load(); changed()
            } catch { err = ApiMessage.text(error) }
        }
    }

    func disconnect(_ f: KeyForm, changed: @escaping () -> Void, report: ForgetReporter) {
        jobs.run { [self] in
            if let ask = await ForgetRules.disconnect(api, item: f.item, name: f.name) { report.show(ask) }
            await load(); changed()
        }
    }
}

/// A site the agent uses with its own browser: its login kept in the keychain (Android Browser).
@MainActor @Observable
final class BrowserConnectModel {
    /// nil: loading; "": nothing kept.
    private(set) var saved: String?
    /// The keychain item made for this site's login, while its form is open.
    private(set) var asking: String?
    private(set) var err = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let site: String
    @ObservationIgnored private let name: String
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, site: String, name: String) { self.api = api; self.site = site; self.name = name }

    func setError(_ text: String) { err = text }

    func load(host: String) async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                let items = try await api.vault().items
                if Task.isCancelled { return }
                saved = ConnectorLogic.savedLogin(host: host, items: items, defaultText: L(R.string.browser_saved_default)) ?? ""
                if saved?.isEmpty == false { asking = nil }
            } catch {
                if !Task.isCancelled { saved = "" }
            }
        }
        loading = task
        await task.value
    }

    func ask() {
        err = ""
        jobs.run { [self] in
            do { asking = try await api.addLogin(site: site, label: name) } catch { err = ApiMessage.text(error) }
        }
    }

    func cancelAsk() {
        guard let draft = asking else { return }
        asking = nil
        jobs.run { [self] in try? await api.cancelLoginDraft(draft) }
    }
}

/// The secure form for a site's login (Android Browser's SiteLogin).
@MainActor @Observable
final class SiteLoginModel {
    private(set) var msg = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let name: String
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, name: String) { self.api = api; self.name = name }

    func save(user: String, password: String, mode: SecretLoginMode, saved: @escaping () -> Void) {
        busy = true; msg = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                try await api.putLogin(name, username: user.trimmingCharacters(in: .whitespacesAndNewlines), password: password, mode: mode)
                saved()
            } catch { msg = ApiMessage.text(error) }
        }
    }
}

/// Google Calendar: the Google sign-in, each account with its own disconnect (Android Google).
@MainActor @Observable
final class GoogleConnectModel {
    private(set) var state: GoogleCalendarState?
    private(set) var failed = false
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                let s = try await api.googleCalendar()
                if !Task.isCancelled { state = s; failed = false }
            } catch is CancellationError {
            } catch {
                if !Task.isCancelled { failed = true }
            }
        }
        loading = task
        await task.value
    }

    func returned() async { await load(); busy = false }

    func start(browser: @escaping BrowserSignIn, changed: @escaping () -> Void) {
        err = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do {
                let url = try await api.googleWebStart(product: "calendar")
                if !(await browser(url)) { err = L(R.string.connector_browser_failed) }
                await load(); changed()
            } catch { err = ApiMessage.text(error) }
        }
    }

    func remove(_ a: GoogleCalendarAccount, changed: @escaping () -> Void, report: ForgetReporter) {
        jobs.run { [self] in
            if let ask = await ForgetRules.disconnect(api, item: a.name, name: a.address) { report.show(ask) }
            await load(); changed()
        }
    }
}

/// The operator's own OAuth app (GitHub or Google) behind a code-connected service; a paired phone (403) sees nothing.
@MainActor @Observable
final class OperatorSettingsModel {
    private(set) var appInfo: OperatorApp?
    private(set) var cid = ""
    private(set) var msg = ""
    /// nil: not known yet; false: not the owner (403): nothing shown.
    private(set) var owner: Bool?
    private(set) var busy = false
    private(set) var switchErr = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let provider: String
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, provider: String) { self.api = api; self.provider = provider }

    func setClientId(_ v: String) { cid = v }

    func load() async {
        do {
            let a = try await api.operatorApp(provider)
            appInfo = a; cid = a.clientId; owner = true
        } catch let e as ApiError {
            owner = e.status == 403 ? false : nil
        } catch {}
    }

    /// `saved` runs after the secret field was emptied by the server's answer (the page reloads its own state).
    func save(secret: String, saved: @escaping () -> Void, cleared: @escaping () -> Void) {
        msg = ""
        jobs.run { [self] in
            do {
                let out = try await api.saveOperatorApp(provider, clientId: cid.trimmingCharacters(in: .whitespacesAndNewlines),
                                                        secret: secret.trimmingCharacters(in: .whitespacesAndNewlines))
                cleared()
                appInfo = out; cid = out.clientId
                msg = L(R.string.operator_saved)
                saved()
            } catch { msg = ApiMessage.text(error) }
        }
    }

    func setOpen(_ v: Bool) {
        busy = true; switchErr = ""
        jobs.run { [self] in
            defer { busy = false }
            do { appInfo = try await api.setGoogleOpen(v) } catch { switchErr = ApiMessage.text(error) }
        }
    }
}

/// "Ask to be let in early" under the Google review note (Android GoogleBeta).
@MainActor @Observable
final class BetaRequestModel {
    private(set) var state: GoogleBetaState?
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func load() async { state = try? await api.googleBeta() }

    /// `sent` runs when the server took the request.
    func send(email: String, product: String, appVersion: String, sent: @escaping () -> Void) {
        busy = true; err = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                state = try await api.requestGoogleBeta(email: email, product: product, appVersion: appVersion)
                sent()
            } catch let e as ApiError {
                err = e.detail
            } catch {
                err = L(R.string.connect_failed)
            }
        }
    }
}

/// Connect a service by typing a short code on its own page: the OAuth device flow, or the web sign-in (Android DeviceConnect).
@MainActor @Observable
final class DeviceConnectModel {
    private(set) var p: OAuthProvider?
    private(set) var flow: OAuthFlow?
    private(set) var deadlineMs: Int64 = 0
    private(set) var nowMs: Int64 = 0
    private(set) var scopeId = ""
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let provider: String
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, provider: String) { self.api = api; self.provider = provider }

    private static func now() -> Int64 { Int64(Date().timeIntervalSince1970 * 1000) }

    func setScope(_ id: String) { scopeId = id }
    func setError(_ text: String) { err = text }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let x = try? await api.oauthProviders().first(where: { $0.id == provider }), !Task.isCancelled else { return }
            // a connection shows the choice it was made with; otherwise the first (the default) until the user picks one
            if scopeId.isEmpty || (x.connected && !x.grantedScope.isEmpty && p?.grantedScope != x.grantedScope) {
                scopeId = x.grantedScope.isEmpty ? (x.scopes.first?.id ?? "") : x.grantedScope
            }
            p = x
        }
        loading = task
        await task.value
    }

    func returned() async { await load() }

    /// Follows the attempt with short requests while the page shows (its `.task(id:)`); it goes on at the server even if this
    /// screen is left. `connected` runs when the service got connected.
    func follow(connected: @escaping () -> Void) async {
        guard let id = flow?.id, flow?.pending == true else { return }
        var clock = DeviceFlowClock()
        while !Task.isCancelled {
            try? await Task.sleep(nanoseconds: DeviceFlowClock.tickSeconds * 1_000_000_000)
            if Task.isCancelled { return }
            nowMs = Self.now()
            guard clock.tick() else { continue }
            do {
                let f = try await api.deviceFlow(id)
                if Task.isCancelled { return }
                flow = f
                switch DeviceFlowClock.after(f) {
                case .keepPolling: continue
                case .connected: await load(); connected(); return
                case .ended: await load(); return
                }
            } catch let e as ApiError {
                if e.status == 404 { flow = nil; return }
            } catch { return }
        }
    }

    private func act(_ block: @escaping @MainActor () async throws -> Void) {
        err = ""; busy = true
        jobs.run { [self] in
            defer { busy = false }
            do { try await block() } catch { err = ApiMessage.text(error) }
        }
    }

    func webSignIn(browser: @escaping BrowserSignIn, changed: @escaping () -> Void) {
        act { [self] in
            let url = try await api.githubWebStart(scope: scopeId)
            if !(await browser(url)) { err = L(R.string.connector_browser_failed) }
            await load(); changed()
        }
    }

    func saveClientId(_ id: String, saved: @escaping () -> Void) {
        act { [self] in
            p = try await api.saveClientId(provider, clientId: id.trimmingCharacters(in: .whitespacesAndNewlines))
            saved()
        }
    }

    func startCode() {
        act { [self] in
            let started = try await api.startDeviceFlow(provider, scope: scopeId)
            let now = Self.now()
            deadlineMs = DeviceFlowClock.deadline(nowMs: now, expiresIn: started.expiresIn)
            nowMs = now
            flow = started
        }
    }

    func cancelFlow(_ f: OAuthFlow) {
        flow = nil
        jobs.run { [self] in try? await api.cancelDeviceFlow(f.id) }
    }

    func disconnect(item: String, name: String, changed: @escaping () -> Void, report: ForgetReporter) {
        jobs.run { [self] in
            if let ask = await ForgetRules.disconnect(api, item: item, name: name) { report.show(ask) }
            flow = nil
            await load(); changed()
        }
    }
}
