import SwiftUI
import KeloCore
import KeloShared

/// 마이 → 사용량: the limits of the chosen model, the tokens by kind of work and API-key balances; "refresh" asks the server to
/// read the provider again. A newer load cancels the older one, so an old answer never replaces a newer one.
@MainActor @Observable final class UsageModel {
    private(set) var usage: MyUsage?
    private(set) var providers: [ModelProvider] = []
    private(set) var adminNote: StringKey?
    private(set) var adminBusy = false
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private var loading: Task<Void, Never>?

    init(api: KeloApi) { self.api = api }

    /// The provider's name as the person knows it.
    func label(_ name: String) -> String { providers.first { $0.name == name }?.displayName ?? name }

    func load() async {
        await read(refresh: false)
        await reloadProviders()
    }

    func refresh() { Task { @MainActor [self] in await read(refresh: true) } }

    func reloadProviders() async {
        let list = (try? await api.request("GET", "/api/providers")).map(ModelProvider.list)
        if !Task.isCancelled { providers = list ?? [] }
    }

    func saveAdminKey(provider: String, key: String, then: @escaping @MainActor () -> Void) async {
        adminBusy = true
        adminNote = nil
        do {
            _ = try await api.request("PUT", "/api/providers/\(KeloApi.segment(provider))/admin-key", body: ["api_key": .string(key.trimmingCharacters(in: .whitespaces))])
            adminNote = R.string.admin_key_saved
            await reloadProviders()
            then()
        } catch let e as ApiError {
            adminNote = switch e.code {
            case "key_refused": R.string.admin_key_refused
            case "keyring_off": R.string.admin_key_keyring_off
            default: R.string.admin_key_failed
            }
        } catch {
            adminNote = R.string.admin_key_failed
        }
        adminBusy = false
    }

    func removeAdminKey(provider: String, then: @escaping @MainActor () -> Void) async {
        _ = try? await api.request("DELETE", "/api/providers/\(KeloApi.segment(provider))/admin-key")
        await reloadProviders()
        then()
    }

    func clearAdminNote() { adminNote = nil }

    private func read(refresh: Bool) async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let u = try? await api.get("/api/usage" + (refresh ? "?refresh=true" : ""), as: MyUsage.self), !Task.isCancelled else { return }
            usage = u
        }
        loading = task
        await task.value
    }
}
