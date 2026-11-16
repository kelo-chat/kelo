import Foundation
import SwiftUI
import KeloCore

/// The tasks a page model started: they end with the model, and a test waits for them with `settled()`.
@MainActor public final class Jobs {
    public init() {}
    private var live: [UUID: Task<Void, Never>] = [:]

    @discardableResult
    public func run(_ body: @escaping @MainActor () async -> Void) -> Task<Void, Never> {
        let id = UUID()
        let t = Task { @MainActor [weak self] in
            await body()
            self?.live[id] = nil
        }
        live[id] = t
        return t
    }

    public func settled() async {
        while !live.isEmpty { for t in Array(live.values) { await t.value } }
    }

    deinit { live.values.forEach { $0.cancel() } }
}

/// Answers that may come back in any order: only the newest request's answer is kept (a settings page that saves on every tap).
public struct Latest {
    public init() {}
    private var issued = 0
    private var applied = 0
    public mutating func begin() -> Int { issued += 1; return issued }
    /// true when `n` is newer than the last answer kept (and now is the last one kept).
    public mutating func accept(_ n: Int) -> Bool {
        guard n > applied else { return false }
        applied = n
        return true
    }
}

private struct KeloApiKey: EnvironmentKey {
    static let defaultValue: KeloApi? = nil
}

public extension EnvironmentValues {
    /// The signed-in API, for leaf views that hand it to a small model (`model.load(url, api:)`); nil while signed out.
    var keloApi: KeloApi? {
        get { self[KeloApiKey.self] }
        set { self[KeloApiKey.self] = newValue }
    }
}

/// The profile the app shows (the AppModel's); a page that saves one hands it back.
@MainActor public protocol ProfileHost: AnyObject {
    var profile: Profile? { get }
    func setProfile(_ p: Profile)
    /// The agent's picture changed on this phone (nil: back to Kelo's character).
    func avatarChanged(_ image: PlatformImage?, version: Int64, data: Data?)
}

extension AppModel: ProfileHost {
    public func avatarChanged(_ image: PlatformImage?, version: Int64, data: Data?) { avatar.set(image, version: version, data: data, cache: cache) }
}


/// What the chat needs from the signed-in app (a small protocol instead of the whole AppModel): the API, the kept data, what the agent
/// is doing, the wallet's hook, the router, and the profile it hands a saved choice to.
@MainActor public protocol ChatHost: ProfileHost {
    var api: KeloApi? { get }
    var cache: ScreenCache { get }
    var activity: AgentActivityModel { get }
    var wallet: WalletHook? { get }
    var router: Router { get }
}

extension AppModel: ChatHost {}
