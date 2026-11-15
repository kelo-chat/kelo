import Foundation
import KeloCore
import KeloShared

/// The chat's form of a model list (with each model's facts) in the kept catalog (`ModelCatalog`).
extension ChatModelList: CatalogModelList {
    public static func catalogList(provider: String, models: [String]) -> ChatModelList { ChatModelList(provider: provider, models: models) }
    public static func catalogFailure(provider: String, error: String) -> ChatModelList { ChatModelList(provider: provider, error: error) }
}

extension ModelCatalog {
    /// The chat's provider rows from the kept GET /api/providers answer, else from where the chat kept them before; empty when
    /// nothing is kept.
    public static func keptChatProviders(_ cache: ScreenCache) -> [ChatProvider] {
        if let raw = keptProviders(cache) { return ChatProvider.parseList(raw) }
        return cache.get(legacyChatProvidersSlot, as: [ChatProvider].self) ?? []
    }

    /// GET /api/providers in the chat's rows (and keeps the answer). Throws what the call throws.
    public static func fetchChatProviders(_ api: KeloApi, _ cache: ScreenCache) async throws -> [ChatProvider] {
        ChatProvider.parseList(try await fetchProviders(api, cache))
    }
}
