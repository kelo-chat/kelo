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

/// What an attachment's bytes come from: a photo made smaller in memory, or a file read while it goes up.
public enum UploadSource: Sendable {
    case data(Data)
    case file(URL)
}

/// PUT /api/uploads/{name} with progress. The real one uses a URLSession upload task; tests use a fake.
public protocol ChatUploader: Sendable {
    func upload(api: KeloApi, name: String, source: UploadSource, type: String?,
                progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String
}

/// History, sending and following a reply, approvals, the effort level and attachments (Android chat/ChatViewModel.kt). The unsent
/// text is kept in the encrypted screen cache, so it survives the app being closed; sending or signing out clears it.
@MainActor @Observable
public final class ChatModel {
    public private(set) var items: [ChatItem] = [] { didSet { rows = Self.rowsOf(items, secrets) } }
    /// The rows the chat draws, made once per change of `items` / `secrets` (never per look: the list reads it often): the items plus
    /// the open secure entries no row shows yet (`ChatReducer.withPending`), with tool steps of one kind in a row folded into one chip
    /// (`ChatReducer.foldTools`).
    public private(set) var rows: [ChatItem] = []
    public private(set) var loaded = false
    public private(set) var busy = false
    public private(set) var phase: Phase?
    public private(set) var input = ""
    public private(set) var providers: [ChatProvider] = []
    /// The current provider's model list: the chip's readable name and the chooser's first section.
    public private(set) var modelList: ChatModelList?
    public private(set) var hasOlder = false
    public private(set) var loadingOlder = false
    /// The last refresh did not reach the server: what shows may be old (kept from last time)
    public private(set) var offline = false
    public private(set) var attachments: [Attachment] = []
    /// The follow-up each agent message carries (by its message_id): its buttons go under that message
    public private(set) var followups: [Int64: Followup] = [:]
    public private(set) var acting: Set<String> = []
    public private(set) var outcomes: [String: ActionOutcome] = [:]
    /// Questions waiting on the user that no followed reply shows: a background job's approval, a scheduled run's (`ChatAsks`)
    public private(set) var asks: [ChatAsk] = []
    /// A message the server held back because it holds a key or password (`SecretGuard`): the sheet asks what to do with it
    public private(set) var secretAsk: SecretAsk?
    /// A message held back until someone agrees that chats go to the AI service (`AiConsent`): the question is on screen
    public private(set) var consentAsk: ConsentAsk?
    /// The Agree call is on its way; its error, if it failed
    public private(set) var consentBusy = false
    public private(set) var consentError: String?
    /// Secure entries the agent asked for (GET /api/secret-requests, open ones; plus the ones answered on this phone, folded until
    /// the next load). The chat shows each under the agent message it belongs to, or at the end (`rows`).
    public private(set) var secrets: [SecretRequest] = [] { didSet { rows = Self.rowsOf(items, secrets) } }

    /// Payment steps that need the user (approve in another app, a code, the page's PIN keypad), shown at the bottom (Android
    /// ChatState.paymentSteps): from the reply's events and GET /api/payment-steps (work the chat is not following, another phone).
    public private(set) var paymentSteps: [PaymentStep] = []
    /// Payment PINs waiting for this phone to release them (pin_needed), with how sending went.
    public private(set) var pinNeeds: [PinNeedRow] = []
    /// Approvals this phone allowed whose site has a saved PIN: their pin_needed row releases it at once (one more check).
    @ObservationIgnored private var pinOnAllow: Set<String> = []

    public static let page = 100
    static let draftDelayNanos: UInt64 = 400_000_000

    @ObservationIgnored let app: AppModel
    @ObservationIgnored var onEvent: (ChatEvent) -> Void = { _ in }
    @ObservationIgnored let uploader: ChatUploader
    @ObservationIgnored let cardSender: ChatCardSender?
    @ObservationIgnored let clock: () -> Double
    @ObservationIgnored private var seq = 0
    @ObservationIgnored private var fresh: Set<String> = []
    @ObservationIgnored private var lastId: Int64 = 0
    @ObservationIgnored private var turn: Task<Void, Never>?
    @ObservationIgnored private var saving: Task<Void, Never>?
    @ObservationIgnored private var uploads: [String: Task<Void, Never>] = [:]
    @ObservationIgnored private var repliesCame = false
    @ObservationIgnored private var started = false
    /// The provider whose model list was last asked for, and the one already fetched fresh in this run (once per provider)
    @ObservationIgnored private var modelListWanted = ""
    @ObservationIgnored private var modelListFetched = ""

    public init(app: AppModel, uploader: ChatUploader = URLSessionUploader(), cardSender: ChatCardSender? = nil,
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
        providers = ModelCatalog.keptChatProviders(app.cache)
        input = app.cache.get("draft") ?? ""
    }

    // ---- small helpers -----------------------------------------------------------------------------------------------------

    private func key() -> String {
        seq += 1
        let k = "k\(seq)"
        fresh.insert(k)
        return k
    }

    /// True once for a row made in this session: the screen lets it rise in, rows from history just appear.
    public func takeFresh(_ key: String) -> Bool { fresh.remove(key) != nil }

    private func api() -> KeloApi? { app.api }

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
            if let p = try? await ModelCatalog.fetchChatProviders(api, self.app.cache) { self.providers = p }
        }
    }

    /// The current provider's model list (the server caches it). The last good one (kept on the phone, `ModelCatalog`) shows at
    /// once, so the chip never changes its wording when the fresh list arrives; the fresh one then replaces it in place, and a
    /// failed fetch leaves the kept one (marked stale).
    public func loadModelList(provider: String) {
        modelListWanted = provider
        guard !provider.isEmpty else { modelList = nil; return }
        let kept = ModelCatalog.keptList(app.cache, provider, as: ChatModelList.self)
        if modelList?.provider != provider { modelList = kept }   // that provider's own last list, never the previous one's
        guard modelListFetched != provider else { return }
        modelListFetched = provider
        Task { [weak self] in
            guard let self, let api = self.api() else { return }
            let fresh: ChatModelList?
            do { fresh = try await ModelCatalog.fetch(api, self.app.cache, provider: provider, as: ChatModelList.self) }
            catch { fresh = nil; if self.modelListFetched == provider { self.modelListFetched = "" } }   // tried again next time
            guard self.modelListWanted == provider else { return }
            let shown = ModelCatalog.shown(fresh, self.modelList?.provider == provider ? self.modelList : kept)
            if shown != self.modelList { self.modelList = shown }
        }
    }

    // ---- history -----------------------------------------------------------------------------------------------------------

    public func reload() {
        Task { [weak self] in await self?.refreshing { try await self?.reloadNow() } }
    }

    private func fetch(_ path: String) async throws -> [ChatMessage] {
        guard let api = api() else { throw ApiError(status: 401, detail: "signed out") }
        return ChatMessage.parseList(try await api.request("GET", path))
    }

    func reloadNow() async throws {
        let ms = try await fetch("/api/messages?limit=\(Self.page)")
        app.cache.put("messages", ChatHistory.trim(ms))
        if busy { return }   // a reply started meanwhile: its own items are on screen
        lastId = ms.last?.id ?? 0
        items = ms.map(ChatReducer.item)
        secrets = SecretRules.settled(secrets)
        refreshAsks(force: true)   // a location card still waiting (it is not a stored message) comes back at the end
        loaded = true
        hasOlder = ms.count >= Self.page
        offline = false
        await loadFollowupsNow()
        await refreshSecretsNow()
    }

    public func loadFollowups() {
        Task { [weak self] in await self?.loadFollowupsNow() }
    }

    private func loadFollowupsNow() async {
        guard let api = api(), let list = try? await FollowupCalls(api: api).list() else { return }
        followups = FollowupRules.byMessage(list.items)
    }

    /// A server that cannot be reached leaves what is shown and only marks it as possibly old.
    private func refreshing(_ block: () async throws -> Void) async {
        do {
            try await block()
            if offline { offline = false }
        } catch is CancellationError {
        } catch let e as ApiError {
            if !e.isUnauthorized { offline = true }
        } catch {
            offline = true
        }
    }

    /// Older messages when the list is scrolled to its top (before_id).
    public func loadOlder() {
        guard hasOlder, !loadingOlder, let first = items.lazy.compactMap({ $0.asMsg?.id }).first else { return }
        loadingOlder = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.loadingOlder = false }
            guard let ms = try? await self.fetch("/api/messages?limit=\(Self.page)&before_id=\(first)") else { return }
            let known = Set(self.items.map(\.key))
            self.items = ms.map(ChatReducer.item).filter { !known.contains($0.key) } + self.items
            self.hasOlder = ms.count >= Self.page
        }
    }

    /// The chat history was cleared on the server (마이 → 대화 기록 삭제): the rows on screen go, then the (empty) history is asked again.
    public func forgetHistory() {
        lastId = 0
        items = []
        hasOlder = false
        followups = [:]
        asks = []
        secrets = []
        reload()
    }

    /// Another phone may have talked to the same assistant: while idle and in view, reload when the newest message changed.
    public func checkForNews() async {
        if busy { return }
        await refreshing {
            let ms = try await fetch("/api/messages?limit=1")
            if (ms.last?.id ?? 0) != lastId && !busy {
                app.activity.touch()   // a new message came in: the character wakes up
                try await reloadNow()
            }
        }
    }

    /// After a reply: note the newest id, and keep the newest page for the next open.
    private func remember() {
        Task { [weak self] in
            guard let self, let ms = try? await self.fetch("/api/messages?limit=\(Self.page)") else { return }
            self.lastId = ms.last?.id ?? 0
            self.app.cache.put("messages", ChatHistory.trim(ms))
        }
    }

    // ---- a reply -----------------------------------------------------------------------------------------------------------

    func apply(_ ev: TurnEvent) {
        if case .paymentStep(let step) = ev { paymentSteps = PaymentStepRules.upsert(paymentSteps, step) }
        if case .pinNeeded(let need) = ev {
            pinNeeds = PinNeedRow.upsert(pinNeeds, need)
            if pinOnAllow.remove(need.slotId) != nil { releasePin(need.slotId) }
        }
        if phase != nil { phase = ChatReducer.phase(phase, ev, now: clock()) }
        items = ChatReducer.apply(items, ev, newKey: key, now: Int64(Date().timeIntervalSince1970 * 1000))
        switch ev {
        case .error, .stepLimit: onEvent(.error)
        case .paymentAuto: onEvent(.paymentAuto)
        case .followup: loadFollowups()
        case .humanCheck: refreshAsks(force: true)   // the hand-over card (or its folded line) at the bottom, at once
        default: break
        }
        if ChatReducer.isReplies(ev) { repliesCame = true }
        app.activity.follow(ev)
    }

    /// The ready attachments' stored names.
    public var uploading: Bool { attachments.contains { $0.state == .uploading } }
    public var ready: [Attachment] { attachments.filter { $0.state == .done } }

    /// Whether 보내기 (or 중지) can be pressed.
    public var canSend: Bool {
        busy || (!uploading && (!input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !ready.isEmpty))
    }

    /// Sends `given` (a card's "에이전트에게 알리기", 다시 시도, a quick reply) or the typed text with the uploaded attachments.
    public func send(_ given: String? = nil, words: AttachmentWords? = nil, answers: [ReplyAnswer] = []) {
        if busy || secretAsk != nil || consentAsk != nil || (given == nil && uploading) { return }
        let took = given == nil && words != nil
        let names = took ? ready.compactMap(\.stored) : []
        let text: String
        if let given { text = given.trimmingCharacters(in: .whitespacesAndNewlines) }
        else if let words { text = AttachmentText.compose(input, names: names, words: words) }
        else { text = input.trimmingCharacters(in: .whitespacesAndNewlines) }
        guard !text.isEmpty else { return }
        // the files go with the message (the server keeps them with it, chat_attachments.py); nothing typed = the app's own sentence
        let files = trayFiles(took)
        start(text, fromComposer: given == nil, tookAttachments: took, answers: answers, secrets: nil, files: files,
              autoText: !files.isEmpty && input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    /// The uploaded files waiting in the tray, as a message's files (a held-back message sent again takes them along).
    func trayFiles(_ took: Bool) -> [MessageFile] {
        guard took else { return [] }
        return ready.compactMap { a in
            a.stored.map { MessageFile(name: a.name, path: $0.hasPrefix("uploads/") ? $0 : "uploads/" + $0, kind: a.image ? "image" : "document") }
        }
    }

    /// Starts the reply to `text`. `secrets` = `SecretGuard.allow` only when the owner chose "Send as is" on the secret sheet. When
    /// the server holds the message back for a secret (409), its bubble goes again, the composer and the attachments come back as
    /// they were, and `secretAsk` opens the sheet (no error row, no failed reply).
    private func start(_ text: String, fromComposer: Bool, tookAttachments: Bool, answers: [ReplyAnswer], secrets: String?,
                       files: [MessageFile] = [], autoText: Bool = false) {
        guard let api = api() else { return }
        let beforeInput = input
        let beforeAttachments = attachments
        if fromComposer {
            input = ""
            if tookAttachments { attachments = [] }
            saving?.cancel()
            app.cache.put("draft", "")
        }
        busy = true
        phase = Phase(.waiting, since: clock())
        let bubble = key()
        items.append(.msg(.init(key: bubble, mine: true, text: text, at: Int64(Date().timeIntervalSince1970 * 1000), files: files, autoText: autoText)))
        onEvent(.sent)
        app.activity.startFollowing()
        turn = Task { [weak self] in
            var finished = false
            do {
                try await TurnFollower(api: KeloTurnApi(api: api)).talk(text: text, timezone: TimeZone.current.identifier, answers: answers,
                                                                        secrets: secrets, attachments: files.map(\.path), autoText: autoText) { ev in
                    await MainActor.run { self?.apply(ev) }
                }
                finished = true
            } catch is CancellationError {
            } catch let e as ApiError {
                if secrets == nil, let held = SecretGuard.of(e), let self {
                    // nothing was sent to the model or stored: no bubble left behind, the text back where it was
                    self.fresh.remove(bubble)
                    self.items.removeAll { $0.key == bubble }
                    if fromComposer { self.input = beforeInput }
                    if tookAttachments { self.attachments = beforeAttachments + self.attachments }
                    self.secretAsk = SecretAsk(found: held, text: text, answers: answers, fromComposer: fromComposer,
                                               tookAttachments: tookAttachments, composer: fromComposer ? beforeInput : "",
                                               composerSpans: fromComposer ? SecretGuard.composerSpans(beforeInput, held.spans) : [])
                } else if let need = AiConsent.of(e), let self {
                    // nobody agreed yet: nothing was stored or sent; the text goes back and the question opens
                    self.fresh.remove(bubble)
                    self.items.removeAll { $0.key == bubble }
                    if fromComposer { self.input = beforeInput }
                    if tookAttachments { self.attachments = beforeAttachments + self.attachments }
                    self.consentError = nil
                    self.consentAsk = ConsentAsk(need: need, text: text, answers: answers, fromComposer: fromComposer,
                                                 tookAttachments: tookAttachments, secrets: secrets)
                } else {
                    self?.addError(e.isNetwork ? .network : (ChatRules.modelRequired(e) ? .pickModel(e.detail) : .message(e.detail)))
                }
            } catch {
                self?.addError(.message(String(describing: error)))
            }
            self?.endTurn(finished: finished)
        }
    }

    private func endTurn(finished: Bool) {
        turn = nil
        app.activity.stopFollowing(finished: finished)
        // questions nobody answered are denied by the server once the reply is over
        busy = false
        phase = nil
        items = ChatReducer.endTurn(items)
        secrets = SecretRules.settled(secrets)   // answered entries go with the reply's end; open ones stay (spec 5.16)
        if finished { onEvent(.replyDone) }
        if repliesCame {   // the answer buttons ride on the stored message: load the messages again
            repliesCame = false
            Task { [weak self] in try? await self?.reloadNow() }
        } else {
            remember()
        }
        loadFollowups()
        refreshSecrets()
    }

    // ---- the location card (location_request): the screen asks the phone (permission, place) and hands the answer here -------
    /// The card shows "getting your location" while the screen reads the place.
    public func locationReading(_ approvalId: String) {
        items = ChatReducer.locationState(items, approvalId, ChatItem.LocationAsk.sending)
    }

    /// POST /api/location-requests/{id}: `place` (lat, lon, name?, country_code?, accuracy?, sublocality?) when shared, nil for
    /// "no" (the system prompt refused, Not now, or no place could be read). The reply that waits on it goes on by itself. 404/409:
    /// the question is gone (expired); a network failure leaves the card open to try again.
    public func answerLocation(_ approvalId: String, place: [String: JSON]?) {
        guard let api = api() else { return }
        items = ChatReducer.locationState(items, approvalId, ChatItem.LocationAsk.sending)
        Task { [weak self] in
            var body: [String: JSON] = ["allowed": .bool(place != nil)]
            if let place { body["place"] = .object(place) }
            let end: String
            do {
                _ = try await api.request("POST", "/api/location-requests/\(KeloApi.segment(approvalId))", body: .object(body))
                end = place != nil ? ChatItem.LocationAsk.allowed : ChatItem.LocationAsk.refused
            } catch let e as ApiError {
                end = e.status == 404 || e.status == 409 ? ChatItem.LocationAsk.expired : ChatItem.LocationAsk.waiting
            } catch {
                end = ChatItem.LocationAsk.waiting
            }
            guard let self else { return }
            self.items = self.items.map { item in
                guard case .locationAsk(var l) = item, l.approvalId == approvalId, l.state == ChatItem.LocationAsk.sending else { return item }
                l.state = end
                return .locationAsk(l)
            }
        }
    }

    // ---- secure entries the agent asked for (a key, a login, a card; server secret_requests.py) -----------------------------

    /// GET /api/secret-requests into `secrets` (on load, after a reply, and when the status poll's count changes).
    public func refreshSecrets() {
        Task { [weak self] in await self?.refreshSecretsNow() }
        refreshPaymentSteps()
    }

    // ---- payment steps (server payment_steps.py): approve in another app, a one-time code, the page's PIN keypad in the card

    public func refreshPaymentSteps() {
        guard let api = api() else { return }
        Task { [weak self] in
            guard let fresh = try? await PaymentStepCalls(api: api).list() else { return }
            guard let self else { return }
            let next = PaymentStepRules.refresh(self.paymentSteps, fresh)
            if next != self.paymentSteps { self.paymentSteps = next }
        }
    }

    /// `answer`: approved | cancel | new_code | code | done. A step that is gone is folded by the next load.
    public func answerPaymentStep(_ stepId: String, _ answer: String, code: String? = nil) {
        guard let api = api() else { return }
        Task { [weak self] in
            do {
                try await PaymentStepCalls(api: api).answer(stepId, answer, code: code)
                if answer == "new_code", let self {
                    self.paymentSteps = self.paymentSteps.map { var s = $0; if s.stepId == stepId { s.state = "renewing" }; return s }
                }
            } catch {
                self?.refreshPaymentSteps()
            }
        }
    }

    /// The page's keypad region now (JPEG bytes), or nil.
    public func paymentPad(_ stepId: String) async -> Data? {
        guard let api = api() else { return nil }
        return try? await PaymentStepCalls(api: api).pad(stepId)
    }

    /// A tap at fractions of the keypad picture; the taps counted, or nil when it did not go through.
    public func paymentPadTap(_ stepId: String, x: Double, y: Double) async -> Int? {
        guard let api = api() else { return nil }
        return try? await PaymentStepCalls(api: api).tap(stepId, x: x, y: y)
    }

    public func paymentPadClear(_ stepId: String) async -> Int? {
        guard let api = api() else { return nil }
        return try? await PaymentStepCalls(api: api).clear(stepId)
    }

    /// Allow on a payment whose site has a saved PIN: its pin_needed row then asks for the owner's check at once.
    public func pinAllowed(_ approvalId: String) {
        if let row = pinNeeds.first(where: { $0.need.slotId == approvalId }), row.send == .idle { releasePin(approvalId) }
        else { pinOnAllow.insert(approvalId) }
    }

    /// "결제 비밀번호 보내기": the owner's check, then the PIN goes once to the payment's slot.
    public func releasePin(_ slotId: String) {
        guard let row = pinNeeds.first(where: { $0.need.slotId == slotId }), row.send != .working else { return }
        pinNeeds = PinNeedRow.set(pinNeeds, slotId, .working)
        Task { [weak self] in
            guard let self else { return }
            let err: CardSendError?
            if let wallet = self.app.wallet {
                err = (await wallet.releasePin(slotId, row.need.pinId)).map(CardSendError.init)
            } else {
                err = .notOnPhone
            }
            self.pinNeeds = PinNeedRow.set(self.pinNeeds, slotId, err.map { .failed($0) } ?? .sent)
            if err == nil { Haptics.success() } else { Haptics.warning() }
        }
    }

    /// The briefing cards on screen, asked for again when GET /api/agent/status "briefing_rev" changes (its hours line answered on
    /// another device, or here): the card follows without a reload (Android ChatViewModel.refreshBriefings).
    public func refreshBriefings() {
        guard items.contains(where: { $0.asMsg?.briefing != nil }) else { return }
        Task { [weak self] in
            guard let self, let ms = try? await self.fetch("/api/messages?limit=\(Self.page)") else { return }
            var fresh: [Int64: BriefingCard] = [:]
            for m in ms { if let b = m.briefing { fresh[m.id] = b } }
            self.items = ChatReducer.withBriefings(self.items, fresh)
        }
    }

    func refreshSecretsNow() async {
        guard let api = api(), let fresh = try? await SecretCalls(api: api).list() else { return }
        secrets = SecretRules.refresh(secrets, fresh)
        // the server lists only open entries: answered lines leave the rows unless a reply runs here (its end clears them)
        if !busy { items = ChatReducer.dropSettled(items) }
    }

    /// The entry `req` was saved here (the value went straight to the vault or the wallet): its card folds. `answer`'s turn is a
    /// reply the server started to carry on, followed here like this phone's own; without one the reply that asked goes on by itself.
    public func secretSaved(_ req: SecretRequest, answer: SecretSaveAnswer, result: SecretResult?) {
        settleSecret(req, state: SecretRequest.stateSaved, result: result, answer: answer)
        if busy { phase = Phase(.waiting, since: clock()) }
        if let t = answer.turnToFollow { followTurn(t) }
    }

    /// Cancel on an entry: the agent is told (it does not ask again); the card folds at once, or goes when no reply runs here. A
    /// reply the server starts to carry on (the answer's turn) is followed like this phone's own.
    public func secretCancelled(_ req: SecretRequest) {
        settleSecret(req, state: SecretRequest.stateCancelled, result: nil, answer: nil)
        guard let api = api() else { return }
        Task { [weak self] in
            do {
                let answer = try await SecretCalls(api: api).cancel(id: req.id)
                if let t = answer.turnToFollow { self?.followTurn(t) }
            }
            catch let e as ApiError { if e.status != 404 && !e.isUnauthorized { self?.addError(e.isNetwork ? .network : .message(e.detail)) } }
            catch {}
        }
    }

    /// The entry is no longer open on the server (answered on another phone, or it ran out).
    public func secretGone(_ req: SecretRequest) { settleSecret(req, state: SecretRequest.stateExpired, result: nil, answer: nil) }

    private func settleSecret(_ req: SecretRequest, state: String, result: SecretResult?, answer: SecretSaveAnswer?) {
        let next = SecretEntries.settle(items: items, secrets: secrets, req, state: state, result: result,
                                        followsHere: SecretRules.followsHere(answer, busy: busy))
        items = next.items
        secrets = next.secrets
    }

    /// Follows a reply the server started by itself for this phone (a secure entry saved after the reply that asked had ended): the
    /// same status line, events and end as a reply sent from here, without a bubble of the user's own.
    public func followTurn(_ turnId: String) {
        guard !busy, let api = api() else { return }
        busy = true
        phase = Phase(.waiting, since: clock())
        app.activity.startFollowing()
        turn = Task { [weak self] in
            var finished = false
            do {
                try await TurnFollower(api: KeloTurnApi(api: api)).follow(id: turnId) { ev in
                    await MainActor.run { self?.apply(ev) }
                }
                finished = true
            } catch is CancellationError {
            } catch let e as ApiError {
                if e.status != 404 { self?.addError(e.isNetwork ? .network : .message(e.detail)) }
            } catch {
                self?.addError(.message(String(describing: error)))
            }
            self?.endTurn(finished: finished)
        }
    }

    /// Waits for the reply in progress (tests).
    func waitForTurn() async { await turn?.value }

    // ---- the secret sheet (a message held back for a key or password; SecretGuard) -------------------------------------------
    /// Waits for the wallet's card form after "Save in Keychain" chose a card (`secretToCard`); memory only
    @ObservationIgnored private var cardRef: SecretAsk?

    /// The save step kept `value` in the Keychain (straight from the app to the vault): in the composer, every span holding that
    /// value becomes `reference` ("(saved to Keychain: GitHub)"), so the rest of the message can still be sent; the sheet closes.
    public func secretSaved(value: String, reference: String) {
        guard let a = secretAsk else { return }
        secretAsk = nil
        input = referenced(input, a, reference: reference, value: value)
    }

    /// A card number goes on to the wallet's card form, filled in there from `handoff` (memory only); the sheet closes.
    public func secretToCard(_ handoff: SecretHandoff) {
        guard let a = secretAsk else { return }
        handoff.offer(Array(a.values.prefix(1)))
        cardRef = a
        secretAsk = nil
    }

    /// The card form kept the card: the composer gets `reference` in place of the number.
    public func secretCardSaved(reference: String) {
        guard let a = cardRef else { return }
        cardRef = nil
        input = referenced(input, a, reference: reference, value: nil)
    }

    /// The composer is changed only while it still holds the text that was put back (the owner may have rewritten it since).
    private func referenced(_ input: String, _ a: SecretAsk, reference: String, value: String?) -> String {
        guard a.fromComposer, input == a.composer else { return input }
        return SecretGuard.replace(input, a.composerSpans, with: reference, value: value)
    }

    /// "Send as is": the very same text again, with `"secrets": "allow"` (only ever after this tap).
    public func secretSendAsIs() {
        guard let a = secretAsk else { return }
        secretAsk = nil
        if busy { return }
        let files = trayFiles(a.tookAttachments)
        start(a.text, fromComposer: a.fromComposer, tookAttachments: a.tookAttachments, answers: a.answers, secrets: SecretGuard.allow,
              files: files, autoText: !files.isEmpty && a.composer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    /// "Edit" (or the sheet dismissed): back to the composer with the text intact; a button's text is put there to be changed.
    public func secretEdit() {
        guard let a = secretAsk else { return }
        secretAsk = nil
        if !a.fromComposer && input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { input = a.text }
    }

    // ---- asking once before chats go to the AI service (AiConsent) ----------------------------------------------------------
    /// "Agree": the server keeps it (once, for every device), then the very same message goes.
    public func consentAgree() {
        guard let a = consentAsk, !consentBusy, let api = api() else { return }
        consentBusy = true
        consentError = nil
        Task { [weak self] in
            do {
                _ = try await api.request("PUT", "/api/ai-consent", body: ["accepted": .bool(true)])
                guard let self else { return }
                self.consentBusy = false
                self.consentAsk = nil
                if !self.busy {
                    let files = self.trayFiles(a.tookAttachments)
                    self.start(a.text, fromComposer: a.fromComposer, tookAttachments: a.tookAttachments, answers: a.answers, secrets: a.secrets,
                               files: files, autoText: !files.isEmpty && self.input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            } catch {
                self?.consentBusy = false
                self?.consentError = (error as? ApiError)?.detail ?? String(describing: error)
            }
        }
    }

    /// "Not now" (or the sheet dismissed): nothing is sent; the text stays in the composer (a button's text is put there).
    public func consentNotNow() {
        guard let a = consentAsk else { return }
        consentAsk = nil
        consentError = nil
        if !a.fromComposer && input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { input = a.text }
    }

    /// 다시 시도 on a reply or an error: the user's message before it goes again.
    public func retry(_ key: String) {
        guard !busy, let text = MessageActions.retryText(items, key: key) else { return }
        send(text)
    }

    /// The Stop button: cancelling the reply tells the server to stop (DELETE /api/turns/{id}).
    public func stop() { turn?.cancel() }

    public func showError(_ e: ChatError) { addError(e) }

    private func addError(_ e: ChatError) {
        items.append(.error(key: key(), e))
        onEvent(.error)
    }

    /// Answers an approval; `rememberSchedule`: keep allowing this step in the scheduled job it came from.
    public func approve(_ id: String, ok: Bool, rememberSchedule: Bool = false) {
        guard items.contains(where: { $0.asApproval?.id == id && $0.asApproval?.open == true }) else { return }
        items = ChatReducer.answer(items, id, ok)
        if busy { phase = Phase(.waiting, since: clock()) }
        guard let api = api() else { return }
        Task {
            // expired: the reply reports the denial
            _ = try? await api.answerApproval(id, approve: ok, remember: ok && rememberSchedule ? "schedule" : nil)
            if ok { ApprovalHooks.afterAllow() }   // a calendar change or call may now wait for this phone
        }
    }

    /// Allow on a payment with an end-to-end card: only when the card got there is the payment allowed.
    public func approveWithCard(_ id: String, cardId: String) {
        guard let a = items.lazy.compactMap(\.asApproval).first(where: { $0.id == id }), a.open, a.send != .working else { return }
        runCardSend(id, cardId: cardId) { [weak self] in self?.approve(id, ok: true) }
    }

    /// "카드 보내기" on a card_needed row (the payment was allowed already).
    public func sendCard(_ approvalId: String, cardId: String) {
        let row = items.lazy.compactMap { item -> ChatItem.CardNeeded? in
            if case .cardNeeded(let c) = item, c.approvalId == approvalId { return c }
            return nil
        }.first
        guard let row, !row.ended, row.send != .working, row.send != .sent else { return }
        runCardSend(approvalId, cardId: cardId) {}
    }

    private func runCardSend(_ id: String, cardId: String, then: @escaping () -> Void) {
        items = ChatReducer.cardSend(items, id, .working)
        Task { [weak self] in
            guard let self else { return }
            let err: CardSendError?
            if let sender = self.cardSender, let api = self.api() {
                err = await sender.deliver(api: api, approvalId: id, cardId: cardId)
            } else {
                err = .notOnPhone
            }
            if let err {
                var next = ChatReducer.cardSend(self.items, id, .failed(err))
                if err == .notWaiting {
                    next = next.map { item in
                        guard case .approval(var a) = item, a.id == id, a.open else { return item }
                        a.expired = true
                        return .approval(a)
                    }
                }
                self.items = next
                if err != .cancelled { self.onEvent(.error) }
            } else {
                self.items = ChatReducer.cardSend(self.items, id, .sent)
                then()
            }
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

    /// The user finished or closed a secure / connect card: it folds into one line.
    public func settleCard(_ key: String, _ state: CardState) { items = ChatReducer.settleCard(items, key, state) }

    // ---- the in-chat address form (shipping_form) ----------------------------------------------------------------------------

    private func shippingItem(_ key: String) -> ChatItem.Shipping? {
        for item in items { if case .shipping(let s) = item, s.key == key { return s } }
        return nil
    }

    /// "Use this address": POST /api/shipping-forms/{form_id}. A 200 folds the card into one line (and with `payment` "card" opens
    /// the wallet's card form); a 422 keeps the form open with the server's words under it; a 404 (the form is gone) shows it as
    /// expired.
    public func answerShipping(_ key: String, address: [String: JSON], save: Bool, label: String, payment: String?) {
        guard let s = shippingItem(key), s.state == .open, !s.sending, let api = api() else { return }
        items = ChatReducer.shipping(items, key) { $0.sending = true; $0.error = nil }
        let body = ShippingFormRules.body(address: address, save: save, label: label, payment: payment)
        Task { [weak self] in
            do {
                let a = try await api.answerShippingForm(s.formId, body)
                guard let self else { return }
                let one = a.profile.map(\.oneLine).flatMap { $0.isEmpty ? nil : $0 } ?? AddressRules.oneLine(a.formatted)
                let line = ShippingFormRules.doneLine(label: a.profile?.label ?? (save ? label : nil), oneLine: one)
                self.items = ChatReducer.shipping(self.items, key) { $0.state = .done; $0.sending = false; $0.doneLine = line }
                if self.busy { self.phase = Phase(.waiting, since: self.clock()) }
                if payment == ShippingFormRules.payCard { self.app.router.openMyPage(.walletAdd) }   // the existing secure card entry
            } catch let e as ApiError where e.status == 404 {
                self?.items = ChatReducer.shipping(self?.items ?? [], key) { $0.state = .expired; $0.sending = false }
            } catch let e as ApiError where !e.isNetwork {
                self?.items = ChatReducer.shipping(self?.items ?? [], key) { $0.sending = false; $0.error = e.detail.isEmpty ? nil : e.detail }
                self?.onEvent(.error)
            } catch {
                guard let self else { return }
                let message = self.app.message(for: error)
                self.items = ChatReducer.shipping(self.items, key) { $0.sending = false; $0.error = message }
                self.onEvent(.error)
            }
        }
    }

    /// "Cancel": the agent is told there is no address (POST {"cancel": true}); the card folds at once.
    public func cancelShipping(_ key: String) {
        guard let s = shippingItem(key), s.state == .open, !s.sending else { return }
        items = ChatReducer.shipping(items, key) { $0.state = .cancelled }
        guard let api = api() else { return }
        Task { try? await api.cancelShippingForm(s.formId) }
    }

    /// The form's time ran out (`expires_at`): no more answers.
    public func expireShipping(_ key: String) {
        items = ChatReducer.shipping(items, key) { if $0.state == .open { $0.state = .expired; $0.sending = false } }
    }

    /// Text shared from another app goes into the draft.
    public func receive(text: String) { setInput(DraftText.append(input, text)) }

    // ---- questions from work the chat is not following -------------------------------------------------------------------

    /// GET /api/approvals/pending joined into the chat's waiting cards; nothing is asked when the poll says nothing waits.
    public func refreshAsks(force: Bool = false) {
        if !force && ChatAsks.skipFetch(app.activity.status, asks) {
            // not fetched: the location cards stay as they are (only a fetched list says one has ended)
            asks = ChatAsks.merge(asks, pending: [], newestKey: items.last?.key)
            return
        }
        Task { [weak self] in
            guard let self, let api = self.api() else { return }
            // not reachable now: the cards stay as they are until the next poll
            guard let (pending, resolved, locations) = try? await api.pendingApprovalsFull() else { return }
            self.asks = ChatAsks.merge(self.asks, pending: pending, newestKey: self.items.last?.key, resolved: resolved)
            self.items = ChatReducer.withPendingLocations(self.items, locations, busy: self.busy)
        }
    }

    /// Answers a waiting card the same way the 작업 page does (`rememberSite`: allow this kind of step on that site from now on;
    /// `rememberSchedule`: in that scheduled job). A payment with an end-to-end card sends the card first on Allow; when that
    /// fails the question stays open and says why.
    public func answerAsk(_ id: String, approve: Bool, rememberSite: Bool = false, rememberSchedule: Bool = false, cardId: String? = nil) {
        guard let ask = asks.first(where: { $0.id == id }), ask.open, !ask.working, let api = api() else { return }
        let a = ask.approval
        asks = ChatAsks.working(asks, id, on: true)
        Task { [weak self] in
            guard let self else { return }
            if approve, let cardId {
                let err: CardSendError? = if let sender = self.cardSender { await sender.deliver(api: api, approvalId: id, cardId: cardId) } else { .notOnPhone }
                if let err {
                    self.asks = err == .notWaiting ? ChatAsks.ended(self.asks, id, .gone, newestKey: self.items.last?.key)
                        : ChatAsks.working(self.asks, id, on: false, error: err == .cancelled ? nil : err)
                    if err != .cancelled { self.onEvent(.error) }
                    return
                }
            }
            let mode: String? = approve && rememberSite && a.canRemember ? "site" : (approve && rememberSchedule && a.canRememberSchedule ? "schedule" : nil)
            do {
                _ = try await api.answerApproval(id, approve: approve, remember: mode)
                if approve { ApprovalHooks.afterAllow() }
                let end = ChatAsks.answerEnd(a, approve: approve, rememberSite: rememberSite, rememberSchedule: rememberSchedule)
                self.asks = ChatAsks.ended(self.asks, id, end, newestKey: self.items.last?.key)
            } catch let e as ApiError {
                if e.status == 404 { self.asks = ChatAsks.ended(self.asks, id, .gone, newestKey: self.items.last?.key) }
                else {
                    self.asks = ChatAsks.working(self.asks, id, on: false)
                    if !e.isUnauthorized { self.addError(e.isNetwork ? .network : .message(e.detail)) }
                }
            } catch {
                self.asks = ChatAsks.working(self.asks, id, on: false)
            }
        }
    }

    // ---- attachments -------------------------------------------------------------------------------------------------------

    /// Puts a chip in the tray at once and uploads in the background. `prepare` reads the pick (and makes a photo smaller) off the
    /// main thread; anything over 100 MB is refused before a byte is sent.
    public func attach(image: Bool, label: String,
                       prepare: @escaping @Sendable () async throws -> PreparedUpload) {
        guard let api = api() else { return }
        seq += 1
        let id = "a\(seq)"
        attachments.append(Attachment(id: id, name: label, image: image))
        let uploader = self.uploader
        uploads[id] = Task { [weak self] in
            var prepared: PreparedUpload?
            do {
                let p = try await prepare()
                prepared = p
                if UploadRules.tooLarge(p.length) { throw UploadTooLarge() }   // checked before anything is sent
                self?.change(id) { $0.name = p.name; $0.total = max(p.length, 0); $0.thumbnail = p.thumbnail }
                let path = try await uploader.upload(api: api, name: p.name, source: p.source, type: p.type) { sent, total in
                    Task { @MainActor in self?.change(id) { $0.sent = sent; $0.total = total } }
                }
                let stored = UploadRules.stored(path, fallback: p.name)
                self?.change(id) { $0.state = .done; $0.stored = stored; $0.sent = $0.total }
            } catch is CancellationError {
            } catch is UploadTooLarge {
                self?.fail(id, .tooLarge)
            } catch let e as ApiError {
                self?.fail(id, UploadRules.error(e))
            } catch {
                self?.fail(id, .unreadable)
            }
            self?.uploads[id] = nil
            prepared?.cleanup()
        }
    }

    private func change(_ id: String, _ f: (inout Attachment) -> Void) {
        guard let i = attachments.firstIndex(where: { $0.id == id }) else { return }
        f(&attachments[i])
    }

    private func fail(_ id: String, _ e: AttachError) {
        change(id) { $0.state = .failed; $0.error = e }
        onEvent(.error)
    }

    /// The chip's ×: a running upload is cancelled; one already up is taken off the server again.
    public func removeAttachment(_ id: String) {
        guard let a = attachments.first(where: { $0.id == id }) else { return }
        uploads.removeValue(forKey: id)?.cancel()
        attachments.removeAll { $0.id == id }
        if a.state == .done, let stored = a.stored, let api = api() {
            Task { _ = try? await api.request("DELETE", "/api/uploads/\(KeloApi.segment(stored))") }
        }
    }

    // ---- follow-up buttons -------------------------------------------------------------------------------------------------

    public func runAction(_ item: Followup, step: FollowupStep) {
        if acting.contains(item.id) || outcomes[item.id] != nil { return }
        if case .openLogin = step { return }   // the screen opens the sign-in sheet
        guard let api = api() else { return }
        acting.insert(item.id)
        Task { [weak self] in
            let (result, detail): (StepResult, String)
            do { (result, detail) = try await FollowupCalls(api: api).run(step) } catch { (result, detail) = (.failed, "") }
            guard let self else { return }
            self.acting.remove(item.id)
            if let o = ChatReducer.outcome(step, result) { self.outcomes[item.id] = o }
            if result == .failed { self.addError(detail.isEmpty ? .network : .message(detail)) }
            else { await self.loadFollowupsNow() }
        }
    }

    public func actionSignedIn(_ item: Followup) { outcomes[item.id] = .signedIn }

    // ---- scrolling to a message --------------------------------------------------------------------------------------------

    /// The row key of the stored message `id`, if it is in the list.
    public func rowKey(forMessage id: Int64) -> String? { items.first { $0.asMsg?.id == id }?.key }
}

struct UploadTooLarge: Error {}

/// What gets uploaded for one pick.
public struct PreparedUpload: Sendable {
    public var name: String
    public var source: UploadSource
    public var type: String?
    public var length: Int64
    public var thumbnail: Data?
    /// A temporary copy to delete once the upload ended either way.
    public var temporary: URL?

    public init(name: String, source: UploadSource, type: String?, length: Int64, thumbnail: Data? = nil, temporary: URL? = nil) {
        self.name = name; self.source = source; self.type = type; self.length = length; self.thumbnail = thumbnail; self.temporary = temporary
    }

    func cleanup() { if let temporary { try? FileManager.default.removeItem(at: temporary) } }
}

/// PUT /api/uploads/{name} through the API's transport (an upload task with progress; through KE2E a streamed 0x02 request), reporting
/// the bytes sent; a file goes up from disk (never held whole in memory). Errors as KeloApi's: the server's detail, or "server not
/// reached".
public struct URLSessionUploader: ChatUploader {
    public init() {}

    public func upload(api: KeloApi, name: String, source: UploadSource, type: String?,
                       progress: @escaping @Sendable (Int64, Int64) -> Void) async throws -> String {
        let body: HTTPUploadBody
        switch source {
        case .data(let d): body = .data(d)
        case .file(let u): body = .file(u)
        }
        return try await api.upload(name: name, body: body, contentType: type, progress: progress)
    }
}

/// A message the server refused with 409 `secret_in_message`: `text` is exactly what was sent (the spans point into it), with the
/// `answers` it carried. `fromComposer`: it was the typed text (put back in the composer, with the attachments when
/// `tookAttachments`). Memory only; never printed (the description leaves the text out).
public final class SecretAsk: Identifiable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let id = UUID()
    public let found: SecretFound
    let text: String
    let answers: [ReplyAnswer]
    public let fromComposer: Bool
    let tookAttachments: Bool
    /// The composer's text as it was put back, and the spans moved onto it (empty for a button's text)
    let composer: String
    let composerSpans: [SecretSpan]

    init(found: SecretFound, text: String, answers: [ReplyAnswer], fromComposer: Bool, tookAttachments: Bool,
         composer: String = "", composerSpans: [SecretSpan] = []) {
        self.found = found; self.text = text; self.answers = answers; self.fromComposer = fromComposer
        self.tookAttachments = tookAttachments; self.composer = composer; self.composerSpans = composerSpans
    }

    /// The detected values, for the save step's field (cut from `text`; memory only)
    public var values: [String] { SecretGuard.values(text, found.spans) }
    public var description: String { "SecretAsk(\(found.target.type), \(found.spans.count) spans)" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["target": found.target.type, "spans": found.spans.count]) }
}

/// A message held back until someone agrees that chats go to the AI service (`AiConsent`). Memory only; never printed.
public final class ConsentAsk: Identifiable, CustomStringConvertible, CustomDebugStringConvertible, CustomReflectable {
    public let id = UUID()
    public let need: AiConsentNeed
    let text: String
    let answers: [ReplyAnswer]
    public let fromComposer: Bool
    let tookAttachments: Bool
    let secrets: String?

    init(need: AiConsentNeed, text: String, answers: [ReplyAnswer], fromComposer: Bool, tookAttachments: Bool, secrets: String?) {
        self.need = need; self.text = text; self.answers = answers; self.fromComposer = fromComposer
        self.tookAttachments = tookAttachments; self.secrets = secrets
    }

    public var description: String { "ConsentAsk(\(need.company))" }
    public var debugDescription: String { description }
    public var customMirror: Mirror { Mirror(self, children: ["company": need.company]) }
}
