import Foundation
import KeloCore

/// A model list as GET /api/models?provider= gives it, in whichever form a screen reads it (the chat's `ModelList`, 마이's
/// `ModelList`): what `ModelCatalog` needs to tell a good list from a failed one and to show the last good one instead.
public protocol CatalogModelList: Equatable {
    var provider: String { get }
    var models: [String] { get }
    var error: String? { get }
    var stale: Bool { get set }
    /// A list of `models` alone (no labels, no error)
    static func catalogList(provider: String, models: [String]) -> Self
    /// What a failed fetch shows: no models, the reason
    static func catalogFailure(provider: String, error: String) -> Self
}

extension ModelList: CatalogModelList {
    public static func catalogList(provider: String, models: [String]) -> ModelList { ModelList(provider: provider, models: models) }
    public static func catalogFailure(provider: String, error: String) -> ModelList { ModelList(provider: provider, error: error) }
}

/// The last good provider list and model lists (their labels and facts too), kept per server on the phone (encrypted, in the
/// `ScreenCache`) so the chat's model chip, the model chooser, the model sheet and 마이 → 모델 show them at once on the next open, then
/// refresh them in the background and change in place (Android chat/ModelCatalog.kt). Only a list that worked is kept: a failed
/// fetch never replaces it. Before anything was ever fetched, names come from `ModelNames.humanize`, which writes them as the
/// server does.
///
/// Both are kept as the server sent them (JSON), so each screen reads its own form from the same copy and nothing one form does
/// not know (a model's facts, a provider's sign-in) is lost when the other writes it.
public enum ModelCatalog {
    /// A refresh this slow shows a small "loading" hint next to what is shown; a quicker one shows nothing
    public static let slowMs: UInt64 = 1000
    public static var slowNanos: UInt64 { slowMs * 1_000_000 }
    /// The most providers whose model lists are kept (the newest fetched first)
    public static let maxProviders = 16

    static let listsSlot = "model_lists"
    static let providersSlot = "providers"
    /// Where the chat kept its shorter provider rows before (read when nothing newer is kept)
    public static let legacyChatProvidersSlot = "chat.providers"

    // ---- what a screen shows

    /// A list worth keeping: it has models and no error.
    public static func keepable<L: CatalogModelList>(_ l: L) -> Bool { !l.provider.isEmpty && l.error == nil && !l.models.isEmpty }

    /// What a screen shows for one provider: a good fresh list; else, when the fetch failed or has not answered yet, the kept one
    /// (marked stale after a failure); else the fresh answer as it is (its error, or nothing yet).
    public static func shown<L: CatalogModelList>(_ fresh: L?, _ kept: L?) -> L? {
        if let fresh, keepable(fresh) { return fresh }
        if let kept, fresh == nil { return kept }
        if var kept, let fresh, fresh.error != nil { kept.stale = true; return kept }
        return fresh
    }

    /// While nothing is known yet for `provider`: the chosen model alone, so a field shows its name (never the raw id, never empty).
    public static func placeholder<L: CatalogModelList>(provider: String, model: String, as type: L.Type = L.self) -> L? {
        provider.isEmpty || model.isEmpty ? nil : L.catalogList(provider: provider, models: [model])
    }

    /// The kept lists (as the server sent them, newest first) once `list` arrived: a good list goes first and replaces that
    /// provider's; a failed one changes nothing. At most `maxProviders`.
    public static func merge(_ kept: [JSON], _ list: JSON) -> [JSON] {
        guard let l = try? JSON.decode(ModelList.self, from: list), keepable(l) else { return kept }
        var out = [list]
        for k in kept where out.count < maxProviders && (k["provider"]?.text ?? "") != l.provider { out.append(k) }
        return out
    }

    // ---- kept on the phone

    /// The kept lists as the server sent them, newest first.
    public static func keptRaw(_ cache: ScreenCache) -> [JSON] { cache.get(listsSlot, as: [JSON].self) ?? [] }

    public static func keptList<L: CatalogModelList & Decodable>(_ cache: ScreenCache, _ provider: String, as type: L.Type = L.self) -> L? {
        guard !provider.isEmpty, let raw = keptRaw(cache).first(where: { ($0["provider"]?.text ?? "") == provider }) else { return nil }
        return try? JSON.decode(L.self, from: raw)
    }

    public static func keptLists<L: CatalogModelList & Decodable>(_ cache: ScreenCache, as type: L.Type = L.self) -> [String: L] {
        var out: [String: L] = [:]
        for raw in keptRaw(cache) {
            if let l = try? JSON.decode(L.self, from: raw), !l.provider.isEmpty, out[l.provider] == nil { out[l.provider] = l }
        }
        return out
    }

    /// Keeps a GET /api/models answer when it is good, without the server's stale mark: shown on a later open, it is just the
    /// last list, refreshed right after.
    public static func keepList(_ cache: ScreenCache, _ list: JSON) {
        guard case .object(var o) = list else { return }
        o["stale"] = .bool(false)
        let kept = keptRaw(cache)
        let next = merge(kept, .object(o))
        if next != kept { cache.put(listsSlot, next) }
    }

    /// The kept GET /api/providers answer (each screen reads its own rows from it), or nil.
    public static func keptProviders(_ cache: ScreenCache) -> JSON? {
        guard let j = cache.get(providersSlot, as: JSON.self), j.arrayValue != nil || j["items"]?.arrayValue != nil else { return nil }
        return j
    }

    public static func keepProviders(_ cache: ScreenCache, _ json: JSON) {
        guard json.arrayValue != nil || json["items"]?.arrayValue != nil else { return }
        cache.put(providersSlot, json)
    }

    // ---- fetching (and keeping what worked)

    /// GET /api/models?provider= (and keeps a good answer). Throws what the call throws.
    public static func fetch<L: CatalogModelList & Decodable>(_ api: KeloApi, _ cache: ScreenCache, provider: String, refresh: Bool = false,
                                                              as type: L.Type = L.self) async throws -> L {
        let raw = try await api.request("GET", "/api/models?provider=\(KeloApi.query(provider))" + (refresh ? "&refresh=true" : ""))
        let l = try KeloApi.decode(L.self, raw)
        keepList(cache, raw)
        return l
    }

    /// GET /api/providers as the server sent it (and keeps it). Throws what the call throws.
    public static func fetchProviders(_ api: KeloApi, _ cache: ScreenCache) async throws -> JSON {
        let raw = try await api.request("GET", "/api/providers")
        keepProviders(cache, raw)
        return raw
    }
}

extension ModelCatalog {
    /// The provider rows from the kept GET /api/providers answer, else from where the chat kept them before (the old slot); empty when
    /// nothing is kept.
    public static func keptModelProviders(_ cache: ScreenCache) -> [ModelProvider] {
        if let raw = keptProviders(cache) { return ModelProvider.list(raw) }
        return cache.get(legacyChatProvidersSlot, as: [ModelProvider].self) ?? []
    }

    /// GET /api/providers as provider rows (and keeps the answer). Throws what the call throws.
    public static func fetchModelProviders(_ api: KeloApi, _ cache: ScreenCache) async throws -> [ModelProvider] {
        ModelProvider.list(try await fetchProviders(api, cache))
    }
}
