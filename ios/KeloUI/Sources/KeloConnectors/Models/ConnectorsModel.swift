import SwiftUI
import Observation
import KeloCore
import KeloShared

/// The 커넥터 tab's data (Android ConnectorsViewModel): the list kept from last time shows at once and is refreshed in place. A newer
/// read cancels the older one, so an old answer never covers a newer one. Also what a connector's page does to the list: disconnect.
@MainActor @Observable
final class ConnectorsModel {
    var state = ConnectorsState()
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let cache: ScreenCache
    @ObservationIgnored private var loading: Task<Void, Never>?
    @ObservationIgnored private var warmed = false
    @ObservationIgnored let jobs = Jobs()

    init(api: KeloApi, cache: ScreenCache) { self.api = api; self.cache = cache }

    /// The kept list (screen cache) before the first refresh.
    func warm() {
        guard !warmed else { return }
        warmed = true
        if let kept: [ConnectorInfo] = cache.get("connectors") { state.list = kept; state.loaded = true }
    }

    /// GET /api/connectors (and the keychain's items) again: on start, on returning to the app, after a panel changed something.
    func reload() { jobs.run { [self] in await load() } }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            do {
                async let creds = try? api.credentials()
                let l = try await api.connectorList()
                let c = await creds
                guard !Task.isCancelled else { return }
                cache.put("connectors", l)
                state.loaded(l, creds: c)
            } catch {
                guard !Task.isCancelled else { return }
                state.failedToLoad()
            }
        }
        loading = task
        await task.value
    }

    /// A stored connection removed (the "reconnect" card's link); the list is read again.
    func disconnect(item: String) {
        jobs.run { [self] in
            try? await api.disconnect(item: item)
            await load()
        }
    }
}

/// One connected service's permission (GET /api/oauth/providers: its `granted_scope` among its `scopes`); nothing when not known.
@MainActor @Observable
final class DeviceScopesModel {
    private(set) var granted: OAuthScope?
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi) { self.api = api }

    func load(provider: String) async {
        guard let l = try? await api.oauthProviders(), !Task.isCancelled else { return }
        granted = ConnectorRules.grantedScope(l.first { $0.id == provider })
    }
}

/// The connect card in the chat and its icon: one connector's row by id, read again after a change and on returning to the app.
@MainActor @Observable
final class ConnectorInfoModel {
    private(set) var info: ConnectorInfo?
    @ObservationIgnored private let api: KeloApi
    @ObservationIgnored private let service: String
    @ObservationIgnored private var loading: Task<Void, Never>?

    init(api: KeloApi, service: String) { self.api = api; self.service = service }

    func load() async {
        loading?.cancel()
        let task = Task { @MainActor [self] in
            guard let l = try? await api.connectorList(), !Task.isCancelled else { return }
            info = l.first { $0.id == service }
        }
        loading = task
        await task.value
    }
}
