import Foundation
import Observation
import KeloCore
import KeloShared

/// One-off things the screen reacts to (a haptic tick), not kept in the state.
public enum ChatEvent: Sendable { case sent, replyDone, error, paymentAuto }

/// Sends an end-to-end card for a payment after the owner's check (Face ID), decrypting it on this phone. The wallet provides it;
/// without one the chat answers `.notOnPhone` (the card cannot be sent from this phone).
public protocol ChatCardSender: Sendable {
    func deliver(api: KeloApi, approvalId: String, cardId: String) async -> CardSendError?
}

/// History, sending and following a reply, approvals, the effort level and attachments (Android chat/ChatViewModel.kt and its parts ChatHistory, ChatTurns, ChatCardActions, ChatUploads). The unsent
/// text is kept in the encrypted screen cache, so it survives the app being closed; sending or signing out clears it.
@MainActor @Observable
public final class ChatModel {
    public internal(set) var items: [ChatItem] = [] { didSet { rows = Self.rowsOf(items, secrets) } }
    /// The rows the chat draws, made once per change of `items` / `secrets` (never per look: the list reads it often): the items plus
    /// the open secure entries no row shows yet (`ChatReducer.withPending`), with tool steps of one kind in a row folded into one chip
    /// (`ChatReducer.foldTools`).
    public private(set) var rows: [ChatItem] = []
    public internal(set) var loaded = false
    public internal(set) var busy = false
    public internal(set) var phase: Phase?
    public internal(set) var input = ""
    public private(set) var providers: [ModelProvider] = []
    /// The current provider's model list: the chip's readable name and the chooser's first section.
    public private(set) var modelList: ModelList?
    public internal(set) var hasOlder = false
    public internal(set) var loadingOlder = false
    /// A search hit's window is on screen: the messages after it are not loaded yet (`loadNewer` as the end comes; `leaveWindow`
    /// brings the newest page back). False for the normal history.
    public internal(set) var hasNewer = false
    public internal(set) var loadingNewer = false
    /// The message a search hit opened: lit for a moment (`openSearchHit`), then it fades (Android HIGHLIGHT_HOLD_MS / FADE_MS).
    public internal(set) var markedMessage: Int64?
    /// The message the list scrolls to; `focusRequest` counts the asks, so the same message can be asked for again.
    public internal(set) var focusTarget: Int64?
    public internal(set) var focusRequest = 0
    @ObservationIgnored var markTask: Task<Void, Never>?
    /// The last refresh did not reach the server: what shows may be old (kept from last time)
    public internal(set) var offline = false
    public internal(set) var attachments: [Attachment] = []
    /// The follow-up each agent message carries (by its message_id): its buttons go under that message
    public internal(set) var followups: [Int64: Followup] = [:]
    public internal(set) var acting: Set<String> = []
    public internal(set) var outcomes: [String: ActionOutcome] = [:]
    /// Questions waiting on the user that no followed reply shows: a background job's approval, a scheduled run's (`ChatAsks`)
    public internal(set) var asks: [ChatAsk] = []
    /// A message the server held back because it holds a key or password (`SecretGuard`): the sheet asks what to do with it
    public internal(set) var secretAsk: SecretAsk?
    /// A message held back until someone agrees that chats go to the AI service (`AiConsent`): the question is on screen
    public internal(set) var consentAsk: ConsentAsk?
    /// The Agree call is on its way; its error, if it failed
    public internal(set) var consentBusy = false
    public internal(set) var consentError: String?
    /// Secure entries the agent asked for (GET /api/secret-requests, open ones; plus the ones answered on this phone, folded until
    /// the next load). The chat shows each under the agent message it belongs to, or at the end (`rows`).
    /// The short message after a login or key was added from its card or sheet (the chat shows it for a moment and clears it).
    public var entryToast: String?
    public internal(set) var secrets: [SecretRequest] = [] { didSet { rows = Self.rowsOf(items, secrets) } }

    /// Payment steps that need the user (approve in another app, a code, the page's PIN keypad), shown at the bottom (Android
    /// ChatState.paymentSteps): from the reply's events and GET /api/payment-steps (work the chat is not following, another phone).
    public internal(set) var paymentSteps: [PaymentStep] = []
    /// Payment PINs waiting for this phone to release them (pin_needed), with how sending went.
    public internal(set) var pinNeeds: [PinNeedRow] = []
    /// Approvals this phone allowed whose site has a saved PIN: their pin_needed row releases it at once (one more check).
    @ObservationIgnored var pinOnAllow: Set<String> = []
    /// Waits for the wallet's card form after "Save in Keychain" chose a card (`secretToCard`); memory only
    @ObservationIgnored var cardRef: SecretAsk?

    public static let page = 100
    static let draftDelayNanos: UInt64 = 400_000_000

    @ObservationIgnored let app: any ChatHost
    @ObservationIgnored var onEvent: (ChatEvent) -> Void = { _ in }
    @ObservationIgnored let uploader: ChatUploader
    @ObservationIgnored let cardSender: ChatCardSender?
    @ObservationIgnored let clock: () -> Double
    @ObservationIgnored var seq = 0
    @ObservationIgnored var fresh: Set<String> = []
    @ObservationIgnored var lastId: Int64 = 0
    /// The server's views (the browser tabs and the phone shown in the chat): their version, the list, and the server's clock
    @ObservationIgnored var viewsVersion = -1
    @ObservationIgnored var views: [ChatViewItem]? = nil   // nil: nothing fetched yet, or an older server without the route
    @ObservationIgnored var viewsAt: Double = 0
    @ObservationIgnored var turn: Task<Void, Never>?
    @ObservationIgnored var turnId = ""          // the followed reply's id (Live Activity, backlog 64b)
    @ObservationIgnored var liveStarted = false
    @ObservationIgnored var saving: Task<Void, Never>?
    @ObservationIgnored var uploads: [String: Task<Void, Never>] = [:]
    @ObservationIgnored var repliesCame = false
    @ObservationIgnored var started = false
    /// The provider whose model list was last asked for, and the one already fetched fresh in this run (once per provider)
    @ObservationIgnored private var modelListWanted = ""
    @ObservationIgnored private var modelListFetched = ""

    public init(app: any ChatHost, uploader: ChatUploader = URLSessionUploader(), cardSender: ChatCardSender? = nil,
                clock: @escaping () -> Double = { Date().timeIntervalSince1970 }) {
        self.app = app
        self.uploader = uploader
        self.cardSender = cardSender
        self.clock = clock
        let kept: [ChatMessage]? = app.cache.get("messages")
        items = (kept ?? []).map(ChatReducer.item)
        loaded = kept != nil
        hasOlder = (kept?.count ?? 0) >= Self.page
        lastId = kept?.last?.id ?? 0
        providers = ModelCatalog.keptModelProviders(app.cache)
        input = app.cache.get("draft") ?? ""
    }

    // ---- small helpers -----------------------------------------------------------------------------------------------------

    func key() -> String {
        seq += 1
        let k = "k\(seq)"
        fresh.insert(k)
        return k
    }

    /// True once for a row made in this session: the screen lets it rise in, rows from history just appear.
    public func takeFresh(_ key: String) -> Bool { fresh.remove(key) != nil }

    func api() -> KeloApi? { app.api }

    /// Runs on first show: the history (kept rows show at once), the follow-ups, the open secure entries and the providers.
    public func start() {
        if !started {
            started = true
            if loaded { loadFollowups(); refreshSecrets() } else { reload() }
        } else if loaded {
            loadFollowups()
            refreshSecrets()
        }
        loadProviders()
    }

    static func rowsOf(_ items: [ChatItem], _ secrets: [SecretRequest]) -> [ChatItem] {
        ChatReducer.foldTools(ChatReducer.withPending(items, secrets))
    }

    public func setInput(_ v: String) {
        guard v != input else { return }
        input = v
        saveDraftSoon()
        app.activity.touch()   // the owner is typing: the character wakes up
    }

    /// Written a moment after typing stops, and right away when the screen goes.
    private func saveDraftSoon() {
        saving?.cancel()
        saving = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.draftDelayNanos)
            guard let self, !Task.isCancelled else { return }
            self.app.cache.put("draft", self.input)
        }
    }

    public func saveDraftNow() {
        saving?.cancel()
        guard app.api != nil else { return }   // signed out: never write it back
        app.cache.put("draft", input)
    }

    public func loadProviders() {
        Task { [weak self] in
            guard let self, let api = self.api() else { return }
            if let p = try? await ModelCatalog.fetchModelProviders(api, self.app.cache) { self.providers = p }
        }
    }

    /// The current provider's model list (the server caches it). The last good one (kept on the phone, `ModelCatalog`) shows at
    /// once, so the chip never changes its wording when the fresh list arrives; the fresh one then replaces it in place, and a
    /// failed fetch leaves the kept one (marked stale).
    public func loadModelList(provider: String) {
        modelListWanted = provider
        guard !provider.isEmpty else { modelList = nil; return }
        let kept = ModelCatalog.keptList(app.cache, provider, as: ModelList.self)
        if modelList?.provider != provider { modelList = kept }   // that provider's own last list, never the previous one's
        guard modelListFetched != provider else { return }
        modelListFetched = provider
        Task { [weak self] in
            guard let self, let api = self.api() else { return }
            let fresh: ModelList?
            do { fresh = try await ModelCatalog.fetch(api, self.app.cache, provider: provider, as: ModelList.self) }
            catch { fresh = nil; if self.modelListFetched == provider { self.modelListFetched = "" } }   // tried again next time
            guard self.modelListWanted == provider else { return }
            let shown = ModelCatalog.shown(fresh, self.modelList?.provider == provider ? self.modelList : kept)
            if shown != self.modelList { self.modelList = shown }
        }
    }

    public func pickEffort(_ value: String) {
        guard let api = api() else { return }
        Task { [weak self] in
            do { self?.app.setProfile(try await api.updateProfile(["effort": value])) }
            catch let e as ApiError { self?.addError(.message(e.detail)) }
            catch {}
        }
    }

    /// Text shared from another app goes into the draft.
    public func receive(text: String) { setInput(DraftText.append(input, text)) }
}
