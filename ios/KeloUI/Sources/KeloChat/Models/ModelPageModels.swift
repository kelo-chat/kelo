import SwiftUI
import Observation
import KeloCore
import KeloShared

/// The model chooser sheet's data (Android ModelChooser): each provider's list (kept ones at once, fetched once per open, an old
/// answer never replaces a newer one) and a pick (PUT /api/profile, the unofficial-provider question first when needed).
@MainActor @Observable
final class ModelChooserModel {
    private(set) var lists: [String: ModelList] = [:]
    private(set) var switching: ModelChoice?
    private(set) var err = ""
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private unowned let host: any ProfileHost
    @ObservationIgnored private var fetched: Set<String> = []
    @ObservationIgnored var loads: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var kept = false
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, cache: ScreenCache, host: any ProfileHost) { self.api = api; self.cache = cache; self.host = host }

    /// The last good lists kept on the phone, and the chat's own current list; shown at once.
    func keepLists(current: ModelList?) {
        if !kept {
            kept = true
            for (k, v) in ModelCatalog.keptLists(cache, as: ModelList.self) where lists[k] == nil { lists[k] = v }
        }
        if let l = current, !l.provider.isEmpty { lists[l.provider] = l }
    }

    /// Fetches a provider's list once per open; the kept one shows meanwhile and is replaced in place (a failure keeps it, marked
    /// stale; with nothing kept the reason shows).
    func load(_ provider: String) {
        guard !fetched.contains(provider) else { return }
        fetched.insert(provider)
        loads[provider]?.cancel()
        loads[provider] = Task { @MainActor [self] in
            let fresh: ModelList
            do { fresh = try await ModelCatalog.fetch(api, cache, provider: provider, as: ModelList.self) }
            catch { fresh = .catalogFailure(provider: provider, error: L(R.string.model_picker_list_failed)) }
            if Task.isCancelled { return }
            guard let shown = ModelCatalog.shown(fresh, lists[provider]), shown != lists[provider] else { return }
            lists[provider] = shown
        }
    }

    /// One tap on a model. `already`: it is the one in use. `after` runs once the profile was saved; `needConsent` gets the provider
    /// the server says needs the user's agreement (then `retry` saves again).
    func switchTo(_ choice: ModelChoice, after: @escaping () -> Void, needConsent: @escaping (String, @escaping () -> Void) -> Void) {
        guard switching == nil else { return }
        let profile = host.profile ?? Profile()
        if choice.provider == profile.provider && choice.model == profile.model { after(); return }
        err = ""
        switching = choice
        jobs.run { [self] in
            defer { if switching == choice { switching = nil } }
            do {
                // the server empties a fallback on the same provider
                host.setProfile(try await api.updateProfile(["provider": choice.provider, "model": choice.model]))
                after()
            } catch let e as ApiError {
                if let n = ProviderConsentRules.requiredBy(e) {
                    switching = nil
                    needConsent(n) { [self] in switchTo(choice, after: after, needConsent: needConsent) }
                } else {
                    err = ApiMessage.text(e)
                }
            } catch {
                err = ApiMessage.text(error)
            }
        }
    }
}
