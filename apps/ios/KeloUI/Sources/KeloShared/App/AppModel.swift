import Foundation
import Observation
import SwiftUI
import KeloCore

/// The profile of the signed-in server (Android start/AppViewModel.kt ProfileState).
public enum ProfileState: Equatable, Sendable {
    case signedOut, loading, failed
    case ready(Profile)
}

/// Android ProfileRules.
public enum ProfileRules {
    /// A failed profile request: a 401 signs out; otherwise a profile already shown (kept from last time) stays.
    public static func afterFailure(_ current: ProfileState, unauthorized: Bool) -> ProfileState {
        if unauthorized { return .signedOut }
        if case .ready = current { return current }
        return .failed
    }

    /// A new server has no model yet: the chat leads to the model settings (Android ModelGate). Only when the server says so.
    public static func needsModel(_ p: Profile) -> Bool { p.modelReady == false }
}

/// The app's long-lived state (Android AppContainer + AppViewModel + Sessions): the language, the session and its API client,
/// the profile, the router, what the agent is doing, and each screen's kept data. One per app, in the SwiftUI environment.
@MainActor @Observable
public final class AppModel {
    public let strings: Strings
    public let prefs: Prefs
    public let router = Router()
    public let activity = AgentActivityModel()
    public let avatar = AgentAvatarModel()
    /// What Nest offers the app's surfaces (nil while signed out). Nothing shows until the app root gives a renderer.
    public private(set) var nest: NestModel?
    /// Which built-in tabs are hidden (none today: nothing sets it yet).
    public var tabLayout = TabLayout.default
    /// A card number from a message the chat held back (`SecretGuard`), on its way to the wallet's card form. Memory only.
    @ObservationIgnored public let secretHandoff = SecretHandoff()
    /// Goes up when the card form kept such a card, so the chat puts its reference in the composer (`secretCardKept`).
    public private(set) var secretCardKeptTick = 0
    /// A card entry the agent asked for, waiting for the wallet's card form (`SecretCardWait`). Memory only.
    public let secretCardWait = SecretCardWait()
    /// The wallet's forms and key for the chat and the Keychain (set by the shell; nil in previews and tests).
    @ObservationIgnored public var wallet: WalletHook?
    @ObservationIgnored public let cache: ScreenCache
    @ObservationIgnored private let store: SessionStore
    @ObservationIgnored private let transport: HTTPTransport
    @ObservationIgnored private var loading: Task<Void, Never>?
    /// Every call through KE2E, and a server without `X-Kelo-Server` counts as too old (spec 5.18). On for the real network; the
    /// tests' in-process fake servers speak plaintext unless the session carries a pin.
    @ObservationIgnored private let strictE2E: Bool
    /// The signed-in server's KE2E state (pin, clock), shared with its transport.
    @ObservationIgnored public private(set) var e2eState: KE2EState?

    /// The server needs a newer app (426 app_too_old, or `min_app_version.ios` above this build): the update screen covers the app.
    public private(set) var appTooOld = false
    /// The server is older than this app needs (`X-Kelo-Server` below the compiled-in min_server, or a pinned server that no
    /// longer speaks KE2E): the "update your server" screen. The text is the server's version ("" when unknown).
    public private(set) var serverTooOld: String?
    /// The server's KE2E key changed (409 e2e_key_changed): this phone must be paired again.
    public private(set) var pairAgain = false
    /// Keyguard: the server holds no key for the owner's passwords, keys and cards (423 {"code": "locked"}, /api/agent/status
    /// "locked"): the app shows "Kelo is locked" with the unlock prompt.
    public private(set) var locked = false
    /// Keyguard is on for this server (GET /api/keyguard "mode" phone); nil while not known (`refreshKeyguard`).
    public private(set) var keyguardOn: Bool?

    /// The pinned key as My → Devices shows it (fingerprint, verified or not); nil while none is pinned.
    public private(set) var e2ePin: KE2EPin?


    /// nil until a language is picked in 마이 → 언어 (or on the first screen of an earlier version): the phone's language is followed.
    public private(set) var languageChoice: String?
    public private(set) var session: Session?
    public private(set) var api: KeloApi?
    public private(set) var profileState: ProfileState = .signedOut
    /// Goes up each time the chat history was cleared on the server in this run (마이 → 대화 기록 삭제): screens that hold messages,
    /// tasks, jobs or approvals drop them when it changes (Android Sessions.historyCleared).
    public private(set) var historyCleared = 0

    public init(store: SessionStore = KeychainSessionStore(), prefs: Prefs = .shared, strings: Strings? = nil,
                transport: HTTPTransport = URLSessionTransport.shared, cache: ScreenCache = ScreenCache(), strictE2E: Bool? = nil) {
        self.store = store
        self.prefs = prefs
        self.strings = strings ?? Strings.shared
        self.transport = transport
        self.strictE2E = strictE2E ?? (transport is URLSessionTransport)
        self.cache = cache
        languageChoice = prefs.language.flatMap { Languages.isKnown($0) ? $0 : nil }
        self.strings.apply(choice: languageChoice)
        refreshAcceptLanguage()
        if let s = store.load() { open(s) }
        activity.onStatus = { [weak self] s in self?.statusPolled(s) }
    }

    public var profile: Profile? {
        if case .ready(let p) = profileState { return p }
        return nil
    }

    /// What this phone is called in 마이 → 내 기기 ("iPhone · Kelo", at most 40 characters).
    public var deviceName: String { String(L(R.string.device_name_format, DeviceInfo.model).prefix(40)) }

    // ---- language ---------------------------------------------------------------------------------------------------------------

    public func chooseLanguage(_ code: String) {
        languageChoice = code
        prefs.language = code
        strings.apply(choice: code)
        refreshAcceptLanguage()
        tellServerLanguage(code)
    }

    /// The server keeps the owner's language for what it says on its own (notifications, the briefing, scheduled runs) and changes
    /// it only from a pick (PUT /api/language), never from a request's language. Only on a pick, not at every start, so one phone in
    /// another language does not flip it. Signed in only; errors (an older server's 404 too) are ignored.
    private func tellServerLanguage(_ code: String) {
        guard let api else { return }
        let language = Languages.forServer(code, deviceTags: Locale.preferredLanguages)
        Task { _ = try? await api.request("PUT", "/api/language", body: ["language": .string(language)]) }
    }

    /// The language every request asks the server for: the app's pick, then the phone's languages.
    public func refreshAcceptLanguage() {
        AcceptLanguageValue.current = AcceptLanguage.build(appLanguage: Languages.override(languageChoice),
                                                           deviceTags: Locale.preferredLanguages)
    }

    // ---- session ----------------------------------------------------------------------------------------------------------------

    public func signIn(_ session: Session) {
        store.save(session)
        prefs.lastServerUrl = session.serverUrl
        open(session)
    }

    /// Called with the signed-in API just before it is dropped (the push token is taken back from the server).
    public var onSignOut: ((KeloApi) -> Void)?

    public func signOut() {
        if let api { onSignOut?(api) }
        loading?.cancel()
        store.save(nil)
        cache.clear()   // each screen's kept data (the chat, the model lists, ...) goes with the session (Android Sessions.signOut)
        session = nil
        api = nil
        e2eState = nil
        e2ePin = nil
        locked = false
        keyguardOn = nil
        pairAgain = false
        serverTooOld = nil
        appTooOld = false
        profileState = .signedOut
        nest = nil
        activity.reset()
        avatar.reset()
        router.reset()
        secretCardWait.stop()
    }

    private func open(_ s: Session) {
        session = s
        e2ePin = s.e2e
        let state = KE2EState(pin: s.e2e, onPin: { [weak self] pin in
            Task { @MainActor in self?.pinned(pin) }
        })
        e2eState = state
        // no plaintext mode (spec 5.18): a phone paired before KE2E takes the key on its first call (trust on first use, "not
        // verified" in My → Devices); a server that offers none shows "update your server"
        let wire: HTTPTransport = strictE2E || s.e2e != nil ? KE2ETransport(base: transport, baseUrl: s.serverUrl, state: state) : transport
        api = KeloApi(session: s, transport: wire, onUnauthorized: { [weak self] in
            Task { @MainActor in self?.signOut() }
        }, onFailure: { [weak self] e in
            Task { @MainActor in self?.failed(e) }
        }, onServerVersion: { [weak self] v in
            Task { @MainActor in self?.serverVersion(v) }
        })
        cache.use(server: s.serverUrl)
        if let api { nest = NestModel(api: api) }
        loadProfile()
    }

    /// A key was pinned (trust on first use) or verified (a scanned QR): kept with the session in the Keychain.
    private func pinned(_ pin: KE2EPin) {
        guard var s = session, e2eState?.pin == pin else { return }
        s.e2e = pin
        session = s
        e2ePin = pin
        store.save(s)
    }

    /// A scanned pairing link checked against the pinned key (My → Devices → verify). nil: the link carries no key.
    public func verifyKey(scanned link: PairingLink) -> Bool? {
        guard let key = link.serverKey else { return nil }
        return e2eState?.verify(scanned: key) ?? false
    }

    public func setLocked(_ on: Bool) { if locked != on { locked = on } }

    /// Asks GET /api/keyguard again (after turning it on or off, before an auto-pay step).
    @discardableResult
    public func refreshKeyguard() async -> Bool? {
        guard let api else { return nil }
        if let v = try? await KeyguardCalls(api: api).view() {
            keyguardOn = v.on
            setLocked(v.locked)
        }
        return keyguardOn
    }

    private func failed(_ e: ApiError) {
        if e.status == 426 || e.code == Versioning.appTooOldCode { appTooOld = true }
        if KeyguardRules.isLocked(e) { setLocked(true) }
        if e.isKeyChanged { pairAgain = true }
        if e.code == KE2ETransport.unsupportedCode, serverTooOld == nil { serverTooOld = "" }
    }

    private func serverVersion(_ v: String?) {
        // no header: a server older than 0.9.0 (spec 5.18)
        if v == nil && strictE2E {
            if serverTooOld == nil { serverTooOld = "" }
        } else if Versioning.serverTooOld(header: v) {
            if serverTooOld != v { serverTooOld = v }
        } else if serverTooOld != nil, v != nil {
            serverTooOld = nil   // updated meanwhile
        }
    }

    /// "Try again" on the server-update screen: the next answer decides again.
    public func recheckServer() {
        serverTooOld = nil
        retryProfile()
    }

    /// The server cleared the chat history and what came of it (DELETE /api/messages): the kept chat is emptied, so the next open
    /// does not show it, and the screens are told (`historyCleared`).
    public func historyWasCleared() {
        cache.put("messages", [StoredMessage]())
        historyCleared += 1
    }

    // ---- profile ----------------------------------------------------------------------------------------------------------------

    public func retryProfile() { loadProfile() }

    public func setProfile(_ p: Profile) {
        profileState = .ready(p)
        cache.put("profile", p)
    }

    /// A status the poll read: the tone changed in the chat ("반말로 해 줘") or on another device, so 마이 → 에이전트 shows it
    /// without a reload (`ToneRules.synced`; nothing changes when it is the same or the server is older).
    public func statusPolled(_ s: AgentStatus) {
        setLocked(s.locked)   // Keyguard: "Kelo is locked" comes and goes with the server's own word
        if let p = profile, let next = ToneRules.synced(p, polled: s.tone) { setProfile(next) }
        // the agent was renamed elsewhere (or by the agent itself): the header follows the poll
        if let p = profile, let n = s.agentName, !n.isEmpty, n != p.agentName { var q = p; q.agentName = n; setProfile(q) }
    }

    /// The profile kept from last time shows the main screens at once; the server's answer then replaces it in place.
    private func loadProfile() {
        guard let api else { profileState = .signedOut; return }
        loading?.cancel()
        if let kept: Profile = cache.get("profile"), !kept.agentName.isEmpty {
            profileState = .ready(kept)
        } else {
            profileState = .loading
        }
        loading = Task { [weak self] in
            do {
                let fresh = try await api.profile()
                guard !Task.isCancelled else { return }
                self?.setProfile(fresh)
            } catch let e as ApiError {
                guard let self, !Task.isCancelled else { return }
                self.profileState = ProfileRules.afterFailure(self.profileState, unauthorized: e.isUnauthorized)
            } catch {
                guard let self, !Task.isCancelled else { return }
                self.profileState = ProfileRules.afterFailure(self.profileState, unauthorized: false)
            }
        }
    }

    /// The text to show for a failed call: the server's own detail, or "couldn't connect" when nothing came back.
    /// The wallet's card form kept the card number the chat handed over: the value is dropped and the chat is told.
    public func secretCardKept() {
        secretHandoff.kept()
        secretCardKeptTick += 1
    }

}

/// An error in words for the person: the server's own detail, or "could not connect" when it was not reached or said nothing.
/// Page models use this instead of reaching into the AppModel.
public enum ApiMessage {
    @MainActor public static func text(_ error: Error) -> String {
        if let e = error as? ApiError { return e.isNetwork || e.detail.isEmpty ? L(R.string.connect_failed) : e.detail }
        return L(R.string.connect_failed)
    }
}
