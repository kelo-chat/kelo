import SwiftUI
import Observation
import KeloCore
import KeloShared

/// A picture the server holds (a chat image, a message's attached photo and its thumbnail): downloaded once through the API, with
/// the app's own device token. A view's `.task(id: url)` runs `load`, so a view that goes away stops its download.
@MainActor @Observable
final class RemotePictureModel {
    private(set) var picture: PlatformImage?

    init() {}

    func load(_ url: String, api: KeloApi?) async {
        guard picture == nil, !url.isEmpty, let api, let bytes = try? await api.download(url), !Task.isCancelled else { return }
        picture = PlatformImage(data: bytes)
    }
}

/// The connect card in the chat while it is open: the connector's name, and whether it is connected yet (asked every 3 seconds while
/// the card shows; the view's `.task(id:)` ends it with the card).
@MainActor @Observable
final class ConnectCardModel {
    private(set) var connector: Connector?
    @ObservationIgnored private let service: String
    @ObservationIgnored private let api: KeloApi

    init(api: KeloApi, service: String) { self.api = api; self.service = service }

    func watch() async {
        while !Task.isCancelled {
            if let list = try? await api.connectors(), !Task.isCancelled { connector = list.first { $0.id == service } }
            if connector?.status == "connected" { return }
            try? await Task.sleep(nanoseconds: 3_000_000_000)
        }
    }
}

/// Looking up one keychain item the agent asked for (the sign-in sheet and the card): found, or gone. The vault is read once per
/// look; a view's `.task` runs it.
@MainActor @Observable
final class SecureLookupModel {
    private(set) var item: ChatVaultItem?
    /// 0 looking, 1 found, 2 gone
    private(set) var state = 0
    private(set) var closed = false

    init() {}

    func load(_ name: String, api: KeloApi?) async {
        guard let api, let info = try? await api.get("/api/vault", as: ChatVaultInfo.self) else { state = 2; return }
        if Task.isCancelled { return }
        item = info.find(name)
        state = item != nil ? 1 : 2
    }

    /// Nothing was entered: the request is forgotten, and nothing is left in the keychain.
    func discard(_ name: String, api: KeloApi?) {
        closed = true
        guard let api else { return }
        Task { _ = try? await api.request("DELETE", "/api/vault/\(KeloApi.segment(name))/draft") }
    }
}

/// The form where a login or a key is typed: the one call that saves it (the value is never shown again).
@MainActor @Observable
final class SecureEntrySaveModel {
    private(set) var busy = false
    private(set) var msg = ""
    @ObservationIgnored let jobs = Jobs()

    init() {}

    func save(_ item: ChatVaultItem, user: String, pass: String, mode: SecretLoginMode, api: KeloApi?, saved: @escaping () -> Void) {
        guard let api else { return }
        busy = true; msg = ""
        jobs.run { [self] in
            defer { busy = false }
            do {
                if item.isLogin {
                    _ = try await api.request("PUT", "/api/vault/\(KeloApi.segment(item.name))/login", body: [
                        "username": .string(user.trimmingCharacters(in: .whitespaces)), "password": .string(pass), "mode": .string(mode.rawValue),
                    ])
                } else {
                    _ = try await api.request("PUT", "/api/credentials/\(KeloApi.segment(item.name))/secret",
                                              body: ["secret": .string(pass.trimmingCharacters(in: .whitespaces))])
                }
                saved()
            } catch {
                msg = ApiMessage.text(error)
            }
        }
    }
}

/// The step that saves what the chat held back as a key or password (Android SecretSheet): the connector's form, and the one call
/// that saves it by use. The value goes with the sheet.
@MainActor @Observable
final class SecretSaveModel {
    private(set) var form: KeyForm?
    private(set) var err = ""
    private(set) var busy = false
    @ObservationIgnored let jobs = Jobs()

    init() {}

    func clearError() { err = "" }

    func loadForm(connector: String, api: KeloApi?) async {
        guard form == nil, let api else { return }
        let f = try? await SecretSave.connectorForm(api, connector: connector)
        if !Task.isCancelled { form = f }
    }

    /// `saved` runs when the server took it.
    func save(use: SecretUse, target: String, value: String, others: [String: String], site: String, label: String, user: String, host: String,
              api: KeloApi?, saved: @escaping () -> Void) {
        busy = true; err = ""
        let connector = form
        jobs.run { [self] in
            defer { busy = false }
            do {
                guard let api else { throw ApiError(status: 401, detail: "signed out") }
                switch use {
                case .aiKey: try await SecretSave.aiKey(api, provider: target, value: value)
                case .connectorKey:
                    guard let connector else { return }
                    try await SecretSave.connectorKey(api, form: connector, value: value, others: others)
                case .login: try await SecretSave.login(api, site: site, label: label, username: user, password: value)
                case .serviceKey: try await SecretSave.serviceKey(api, host: host, label: label, value: value)
                case .card: return
                }
                saved()
            } catch let e as ApiError {
                // as Android: a network failure reads "Can't reach the server", never the transport's own text
                err = e.isNetwork ? L(R.string.agent_offline) : (e.detail.isEmpty ? ApiMessage.text(e) : e.detail)
            } catch {
                err = ApiMessage.text(error)
            }
        }
    }
}
