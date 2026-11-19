import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 키체인: the vault's data, shared by its pages while it is open (the list, the AI keys, the log), and what the pages do to
/// it: lock, mail codes, one login or key (allow, revoke, remove, replace), a new login, an AI key. One per My stack, passed to the
/// vault's pages; every change reads the vault again. `message` is the last failure in words (the page clears it when it opens).
@MainActor @Observable final class VaultModel {
    private(set) var info: VaultState?
    private(set) var providers: [ModelProvider] = []
    private(set) var log: [VaultLogRow]?
    private(set) var message = ""
    /// The AI key page: a key was saved ("Saved") and the busy / removed marks
    private(set) var aiSaved = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi) { self.api = api }

    func clearMessage() { message = ""; aiSaved = false }

    /// A newer read cancels the older one, so an old answer never covers what a change just made.
    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            if let v = try? await api.vault(), !Task.isCancelled { info = v }
        }
        loading = task
        await task.value
    }

    func reload() { jobs.run { [self] in await load() } }

    func loadProviders() async {
        if let p = (try? await api.request("GET", "/api/providers")).map(ModelProvider.list) { providers = p }
    }

    func loadLog() async {
        log = (try? await api.get("/api/vault/log?limit=200", as: [VaultLogRow].self)) ?? []
    }

    /// An entry of the list (or one still waiting for its value).
    func item(_ name: String) -> VaultEntry? { info.flatMap { s in (s.items + s.pending).first { $0.name == name } } }

    // ---- the vault as a whole -------------------------------------------------------------------------------------------------------

    func setLocked(_ locked: Bool) {
        jobs.run { [self] in
            _ = try? await api.request("PUT", "/api/vault/lock", body: ["locked": .bool(locked)])
            await load()
        }
    }

    func setMailCodes(_ on: Bool) {
        jobs.run { [self] in
            _ = try? await api.request("PUT", "/api/vault/settings", body: ["mail_codes_auto": .bool(on)])
            await load()
        }
    }

    // ---- one login or key -----------------------------------------------------------------------------------------------------------

    func patch(_ name: String, _ fields: [String: JSON]) {
        jobs.run { [self] in
            do {
                try await api.request("PATCH", "/api/vault/\(KeloApi.segment(name))", body: .object(fields))
                await load()
            } catch { message = ApiMessage.text(error) }
        }
    }

    func revoke(_ g: ScheduleGrant) {
        jobs.run { [self] in
            do { try await api.revokeScheduleGrant(g); await load() } catch { message = ApiMessage.text(error) }
        }
    }

    /// The question "also forget its data?" after a login or key was removed that left some data (the vault's page shows it).
    var forget: ForgetAsk?

    /// Signs Kelo's browser out of `site` (cookies and site data go; a saved login stays), then reloads (brief B4).
    func signOutBrowser(_ site: String) {
        jobs.run { [self] in
            do {
                try await api.request("DELETE", "/api/browser/sign-ins/\(KeloApi.segment(site))")
                await load()
            } catch { message = ApiMessage.text(error) }
        }
    }

    /// Signs a named browser profile out of `site`, then reloads (brief C2d).
    func signOutProfileSite(_ profile: String, _ site: String) {
        jobs.run { [self] in
            do {
                try await api.request("DELETE", "/api/browser/profiles/\(KeloApi.segment(profile))/sites/\(KeloApi.segment(site))")
                await load()
            } catch { message = ApiMessage.text(error) }
        }
    }

    /// Deletes a named browser profile with every sign-in kept in it; `done` runs when it is gone.
    func deleteProfile(_ profile: String, done: @escaping () -> Void) {
        jobs.run { [self] in
            do {
                try await api.request("DELETE", "/api/browser/profiles/\(KeloApi.segment(profile))")
                await load()
                done()
            } catch { message = ApiMessage.text(error) }
        }
    }

    /// Removes the login or key; `done` runs when it is gone (the page closes). Data it left is asked about afterwards.
    func remove(_ name: String, done: @escaping () -> Void) {
        let label = item(name)?.label ?? name
        jobs.run { [self] in
            do {
                let data = try await api.disconnect(item: name)
                await load()
                if let ask = ForgetRules.ask(item: name, name: label, data: data) { forget = ask }
                done()
            } catch { message = ApiMessage.text(error) }
        }
    }

    /// Leaving a login or key that was started but never filled in: nothing stays behind in the vault.
    func dropUnfilled(_ name: String) {
        guard let item = item(name), !item.hasSecret else { return }
        jobs.run { [self] in
            _ = try? await api.request("DELETE", "/api/vault/\(KeloApi.segment(name))/draft")
            await load()
        }
    }

    /// A new login (its site and name): the vault item the next page fills in.
    func addLogin(site: String, label: String, created: @escaping (String) -> Void) {
        message = ""
        jobs.run { [self] in
            do {
                let r: NewLogin = try await api.post("/api/vault/logins",
                    body: ["site": .string(site.trimmingCharacters(in: .whitespaces)), "label": .string(label.trimmingCharacters(in: .whitespaces))])
                await load()
                created(r.name)
            } catch { message = ApiMessage.text(error) }
        }
    }

    /// The value typed for a login or a key (never shown again). Throws the server's answer for the form to show.
    func saveSecret(_ item: VaultEntry, user: String, pass: String, mode: SecretLoginMode) async throws {
        if item.isLogin {
            try await api.request("PUT", "/api/vault/\(KeloApi.segment(item.name))/login",
                                  body: ["username": .string(user.trimmingCharacters(in: .whitespaces)), "password": .string(pass), "mode": .string(mode.rawValue)])
        } else {
            try await api.request("PUT", "/api/credentials/\(KeloApi.segment(item.name))/secret",
                                  body: ["secret": .string(pass.trimmingCharacters(in: .whitespacesAndNewlines))])
        }
    }

    // ---- an AI service's key --------------------------------------------------------------------------------------------------------

    private func keyPath(_ name: String) -> String { "/api/providers/\(KeloApi.segment(name))/key" }

    func saveAiKey(_ name: String, key: String) {
        message = ""; aiSaved = false
        jobs.run { [self] in
            do {
                try await api.request("PUT", keyPath(name), body: ["api_key": .string(key.trimmingCharacters(in: .whitespacesAndNewlines))])
                aiSaved = true
                await loadProviders()
            } catch { message = ApiMessage.text(error) }
        }
    }

    func removeAiKey(_ name: String, done: @escaping () -> Void) {
        jobs.run { [self] in
            _ = try? await api.request("DELETE", keyPath(name))
            await loadProviders()
            done()
        }
    }
}

/// The form where a login or a key is typed: the save and its failure.
@MainActor @Observable final class SecureEntryModel {
    private(set) var message = ""
    private(set) var busy = false
    @ObservationIgnored private let vault: VaultModel
    @ObservationIgnored let jobs = Jobs()

    init(vault: VaultModel) { self.vault = vault }

    func save(_ item: VaultEntry, user: String, pass: String, mode: SecretLoginMode, saved: @escaping () -> Void) {
        busy = true; message = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                try await vault.saveSecret(item, user: user, pass: pass, mode: mode)
                saved()
            } catch { message = ApiMessage.text(error) }
        }
    }
}

/// "Waiting for input" on the keychain page: the secure entries the agent asked for that are still open.
@MainActor @Observable final class WaitingEntriesModel {
    private(set) var list: [SecretRequest] = []
    internal(set) var open: String?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let onChanged: () -> Void
    @ObservationIgnored let jobs = Jobs()

    /// `onChanged`: an entry was answered or dropped, so the keychain loads again (a key or a login may now be listed).
    init(api: KeloApi, onChanged: @escaping () -> Void) { self.api = api; self.onChanged = onChanged }

    func load() async {
        guard let fresh = try? await SecretCalls(api: api).list() else { return }
        list = fresh
    }

    func reload() { jobs.run { [self] in await load() } }
    func fill(_ id: String) { open = id }

    /// Answered (or gone): it leaves the list.
    func done(_ r: SecretRequest) {
        open = nil
        list.removeAll { $0.id == r.id }
        onChanged()
    }

    /// [Delete] / Cancel: the agent is told; an empty vault item made for it goes too.
    func drop(_ r: SecretRequest) {
        open = nil
        list.removeAll { $0.id == r.id }
        jobs.run { [self] in
            try? await SecretCalls(api: api).cancel(id: r.id)
            onChanged()
        }
    }
}
