package chat.kelo.app.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.Caches
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.DraftStore
import chat.kelo.app.core.Shared
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Profile
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.TimeZone
import kotlin.coroutines.cancellation.CancellationException

data class ChatState(
    val items: List<ChatItem> = emptyList(),
    val loaded: Boolean = false,
    val busy: Boolean = false,
    val phase: Phase? = null,
    val input: String = "",
    val providers: List<ProviderInfo> = emptyList(),
    /** More stored messages before the first one shown */
    val hasOlder: Boolean = false,
    val loadingOlder: Boolean = false,
    /** The last refresh did not reach the server: what shows may be old (kept from last time) */
    val offline: Boolean = false,
    /** Photos and files picked with "+", going with the next message */
    val attachments: List<Attachment> = emptyList(),
    /** The follow-up each agent message carries (GET /api/followups, by its message_id): its buttons go under that message */
    val followups: Map<Long, chat.kelo.app.followup.Followup> = emptyMap(),
    /** Follow-ups with a button being done right now (by follow-up id) */
    val acting: Set<String> = emptySet(),
    /** What a button pressed on this phone did (by follow-up id), shown once its item is no longer open */
    val outcomes: Map<String, ActionOutcome> = emptyMap(),
    /** Questions waiting on the user that no followed reply shows: a background job's approval, a scheduled run's ([ChatAsks]) */
    val asks: List<ChatAsk> = emptyList(),
    /** A message the server held back because it holds a key or password ([SecretGuard]): the sheet asks what to do with it */
    val secretAsk: SecretAsk? = null,
    /** A message held back until someone agrees that chats go to the AI service ([AiConsent]): the question is on screen */
    val consentAsk: ConsentAsk? = null,
    /**
     * Secure entries the agent asked for (GET /api/secret-requests, open ones; plus the ones answered on this phone, folded until
     * the next load). The chat shows each under the agent message it belongs to, or at the end ([ChatReducer.withPending]).
     */
    val secrets: List<chat.kelo.app.chat.vault.SecretRequest> = emptyList(),
    /** Payment steps that need the user (GET /api/payment-steps and the payment_step events): shown at the bottom, newest last */
    val paymentSteps: List<PaymentStep> = emptyList(),
    /** Payment PINs waiting for this phone to release them (pin_needed events) */
    val pinNeeds: List<PinNeed> = emptyList(),
) {
    /** An attachment is still on its way up: sending waits for it */
    val uploading: Boolean get() = attachments.any { it.state == AttachState.UPLOADING }
    /** What the next message would take along */
    val ready: List<Attachment> get() = attachments.filter { it.state == AttachState.DONE }
}

/**
 * A message the server refused with 409 `secret_in_message`: [text] is exactly what was sent (the spans point into it), with the
 * [answers] it carried. [fromComposer]: it was the typed text (put back in the composer, with the attachments when [tookAttachments]).
 * Memory only; never printed (toString leaves the text out).
 */
class SecretAsk(
    val found: SecretFound, val text: String, val answers: List<ReplyAnswer>, val fromComposer: Boolean, val tookAttachments: Boolean,
    /** The composer's text as it was put back, and the spans moved onto it (empty for a button's text) */
    val composer: String = "", val composerSpans: List<SecretSpan> = emptyList(),
) {
    /** The detected values, for the save step's field (cut from [text]; memory only) */
    val values: List<String> get() = SecretGuard.values(text, found.spans)
    override fun toString(): String = "SecretAsk(${found.target.type}, ${found.spans.size} spans)"
}

/** Shared text joining what is already typed. */
object DraftText {
    fun append(draft: String, text: String): String = when {
        text.isBlank() -> draft
        draft.isBlank() -> text
        else -> draft.trimEnd() + "\n" + text
    }
}

/** One-off things the screen reacts to (a haptic tick), not kept in the state. */
enum class ChatEvent { SENT, REPLY_DONE, ERROR, PAYMENT_AUTO }

/**
 * History, sending and following a reply, approvals, the effort level and documents. The unsent text is kept in
 * [drafts] (encrypted on the phone), so it is still there after the app was closed or killed; sending or signing out clears it.
 */
class ChatViewModel(
    private val sessions: Sessions,
    private val drafts: DraftStore? = null,
    /** The line in the header's name pill: what the reply being followed is doing right now */
    private val activity: AgentActivity? = null,
    /** "reply done" / "failed" notifications while the app is away */
    private val watch: chat.kelo.app.notify.TurnWatch? = null,
) : ViewModel() {
    /** The newest messages kept from last time: shown at once (no placeholder), then refreshed in place by [reload] */
    private val kept: List<StoredMessage>? = runCatching { sessions.cached(Caches.MESSAGES) }.getOrNull()

    private val _state = MutableStateFlow(
        ChatState(
            input = drafts?.let { d -> runCatching { d.loadDraft() }.getOrNull() }.orEmpty(),
            items = kept.orEmpty().map(::toItem),
            loaded = kept != null,
            hasOlder = (kept?.size ?: 0) >= PAGE,
            providers = runCatching { sessions.cached(Caches.PROVIDERS) }.getOrNull().orEmpty(),
        ),
    )
    val state: StateFlow<ChatState> = _state.asStateFlow()

    private val _events = MutableSharedFlow<ChatEvent>(extraBufferCapacity = 8)
    val events: SharedFlow<ChatEvent> = _events.asSharedFlow()

    private var seq = 0
    /** Rows made in this session (not loaded from history) that have not risen into view yet */
    private val fresh = HashSet<String>()
    private fun key(): String = "k${++seq}".also { fresh += it }
    private var lastId = kept?.lastOrNull()?.id ?: 0L
    private var turn: Job? = null
    private var saving: Job? = null

    private fun api(): KeloApi = sessions.requireApi()

    init {
        // 마이 → 대화 기록 삭제: the rows on screen are gone on the server; ask again (the server now has none)
        viewModelScope.launch {
            sessions.historyCleared.drop(1).collect {
                lastId = 0
                _state.update { it.copy(items = emptyList(), hasOlder = false) }
                reload()
            }
        }
        // the status poll (every few seconds in the foreground) says when the waiting approvals changed: ask for the list then
        activity?.let { a ->
            viewModelScope.launch {
                var before: Pair<Int, Int>? = null
                a.status.collect { s ->
                    val k = ChatAsks.pollKey(s) ?: return@collect
                    if (k != before) { before = k; refreshAsks() }
                }
            }
            // a payment step opened by work the chat is not following (a job, a scheduled run, another phone): looked for while the
            // agent works, and while one is open here
            viewModelScope.launch {
                a.status.collect { s ->
                    if (s?.busy == true || _state.value.paymentSteps.any { it.open }) refreshPaymentSteps()
                }
            }
            // a secure entry asked for or answered elsewhere (another phone, the Keychain page): the chat's list follows
            viewModelScope.launch {
                var before: Int? = null
                a.status.collect { s ->
                    val n = s?.secretRequests ?: return@collect
                    if (n != before) { val first = before == null; before = n; if (!first || n > 0) refreshSecrets() }
                }
            }
            // a briefing card on screen changed elsewhere (its hours line answered on another device, or here): the card follows. The
            // first poll counts too: the card kept from last time may have changed while the app was closed.
            viewModelScope.launch {
                var before: String? = null
                a.status.collect { s ->
                    val rev = s?.briefingRev ?: return@collect
                    if (rev != before) { before = rev; refreshBriefings() }
                }
            }
        }
    }

    // ---- questions from work the chat is not following (a background job, a scheduled run)
    /** GET /api/approvals/pending joined into the chat's waiting cards; nothing is asked when the poll says nothing waits. */
    fun refreshAsks(force: Boolean = false) {
        if (!force && ChatAsks.skipFetch(activity?.status?.value, _state.value.asks)) {
            // not fetched: the location cards stay as they are (only a fetched list says one has ended)
            _state.update { it.copy(asks = ChatAsks.merge(it.asks, emptyList(), it.items.lastOrNull()?.key)) }
            return
        }
        viewModelScope.launch {
            var locations = emptyList<TurnEvent.LocationRequest>()
            val pending = try {
                api().request("GET", "/api/approvals/pending").let {
                    // location cards are not approvals: listed so a reloaded chat shows the waiting one again
                    locations = ((it as? kotlinx.serialization.json.JsonObject)?.get("items") as? kotlinx.serialization.json.JsonArray).orEmpty()
                        .mapNotNull { e -> TurnEvent.parse(e) as? TurnEvent.LocationRequest }
                    chat.kelo.app.agent.PendingApproval.parseList(it) to chat.kelo.app.agent.PendingApproval.parseResolved(it)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return@launch   // not reachable now: the cards stay as they are until the next poll
            }
            _state.update {
                it.copy(asks = ChatAsks.merge(it.asks, pending.first, it.items.lastOrNull()?.key, pending.second),
                    items = ChatReducer.withPendingLocations(it.items, locations, it.busy))
            }
        }
    }

    /**
     * Answers a waiting card the same way the 작업 page does ([rememberSite]: allow this kind of step on that site from now on;
     * [rememberSchedule]: in that scheduled job). [sendCard] runs first on Allow for a payment with an end-to-end card; when it
     * fails the question stays open and says why.
     */
    fun answerAsk(
        id: String, approve: Boolean, rememberSite: Boolean = false, rememberSchedule: Boolean = false,
        sendCard: (suspend () -> chat.kelo.app.wallet.CardSendError?)? = null,
    ) {
        val ask = _state.value.asks.firstOrNull { it.id == id } ?: return
        if (!ask.open || ask.working) return
        val a = ask.approval
        _state.update { it.copy(asks = ChatAsks.working(it.asks, id, true)) }
        viewModelScope.launch {
            try {
                if (approve && sendCard != null) {
                    val err = try { sendCard() } catch (e: CancellationException) { throw e } catch (e: Exception) { chat.kelo.app.wallet.CardSendError.OTHER }
                    if (err != null) {
                        _state.update { s ->
                            s.copy(asks = if (err == chat.kelo.app.wallet.CardSendError.NOT_WAITING) ChatAsks.ended(s.asks, id, AskEnd.GONE, s.items.lastOrNull()?.key)
                                else ChatAsks.working(s.asks, id, false, err.takeIf { it != chat.kelo.app.wallet.CardSendError.CANCELLED }))
                        }
                        if (err != chat.kelo.app.wallet.CardSendError.CANCELLED) _events.tryEmit(ChatEvent.ERROR)
                        return@launch
                    }
                }
                api().post<JsonElement>("/api/approvals/${KeloApi.segment(id)}", buildJsonObject {
                    put("approve", approve)
                    if (approve && rememberSite && a.canRemember) put("remember", "site")
                    else if (approve && rememberSchedule && a.canRememberSchedule) put("remember", "schedule")
                })
                if (approve) chat.kelo.app.device.DeviceTasks.afterApproval()   // a text, call or calendar change may now wait for this phone
                val end = ChatAsks.answerEnd(a, approve, rememberSite, rememberSchedule)
                _state.update { s -> s.copy(asks = ChatAsks.ended(s.asks, id, end, s.items.lastOrNull()?.key)) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status == 404) _state.update { s -> s.copy(asks = ChatAsks.ended(s.asks, id, AskEnd.GONE, s.items.lastOrNull()?.key)) }
                else {
                    _state.update { s -> s.copy(asks = ChatAsks.working(s.asks, id, false)) }
                    addError(if (e.isNetwork) ChatError.Network else ChatError.Message(e.detail))
                }
            } catch (e: Exception) {
                _state.update { s -> s.copy(asks = ChatAsks.working(s.asks, id, false)) }   // signed out meanwhile
            }
        }
    }

    /** True once for a row made in this session: the screen lets it rise in, rows from history just appear. */
    fun takeFresh(key: String): Boolean = fresh.remove(key)

    fun setInput(v: String) {
        _state.update { it.copy(input = v) }
        saveDraftSoon()
        stir()   // the owner is typing: the character wakes up
    }

    /** Something happened in the chat (typing, a message sent or come in, a reply ending): the character stays awake a while. */
    private fun stir() {
        activity?.touch(android.os.SystemClock.elapsedRealtime())
    }

    /** Written a moment after typing stops (each write is an encryption), and right away when the screen goes. */
    private fun saveDraftSoon() {
        val d = drafts ?: return
        saving?.cancel()
        saving = viewModelScope.launch {
            delay(DRAFT_DELAY_MS)
            val text = _state.value.input
            withContext(Dispatchers.IO) { runCatching { d.saveDraft(text) } }
        }
    }

    fun saveDraftNow() {
        val d = drafts ?: return
        saving?.cancel()
        if (sessions.api.value == null) return   // signed out: the draft was cleared with the session, do not write it back
        runCatching { d.saveDraft(_state.value.input) }
    }

    override fun onCleared() {
        saveDraftNow()
    }

    fun loadProviders() {
        viewModelScope.launch {
            runCatching {
                val api = api()
                api.get<List<ProviderInfo>>("/api/providers").also { sessions.keep(api, Caches.PROVIDERS, it) }
            }.onSuccess { p -> _state.update { it.copy(providers = p) } }
        }
    }

    // ---- history
    fun reload() {
        viewModelScope.launch { refreshing { reloadNow() } }
    }

    private suspend fun reloadNow() {
        val api = api()
        val ms = api.get<List<StoredMessage>>("/api/messages?limit=$PAGE")
        sessions.keep(api, Caches.MESSAGES, ms)
        if (_state.value.busy) return   // a reply started meanwhile: its own items are on screen
        lastId = ms.lastOrNull()?.id ?: 0
        // the same rows (same keys) as the kept ones: the list updates in place, nothing jumps
        _state.update { it.copy(items = ms.map(::toItem), loaded = true, hasOlder = ms.size >= PAGE, offline = false,
            secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
        loadFollowupsNow()
        refreshSecretsNow()
        refreshAsks(force = true)   // a location card still waiting (it is not a stored message) comes back at the end
    }

    /** The follow-ups and the messages they belong to (also after a reply, and when a follow-up notification is opened). */
    fun loadFollowups() {
        viewModelScope.launch { runCatching { loadFollowupsNow() } }
    }

    private suspend fun loadFollowupsNow() {
        val list = try { chat.kelo.app.followup.FollowupCalls(api()).list() } catch (e: ApiError) { return }
        _state.update { it.copy(followups = chat.kelo.app.followup.FollowupRules.byMessage(list.items)) }
    }

    /** Runs a refresh; a server that cannot be reached leaves what is shown and only marks it as possibly old. */
    private suspend fun refreshing(block: suspend () -> Unit) {
        try {
            block()
            if (_state.value.offline) _state.update { it.copy(offline = false) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiError) {
            if (!e.isUnauthorized) _state.update { it.copy(offline = true) }
        } catch (e: Exception) {
            _state.update { it.copy(offline = true) }
        }
    }

    /** Older messages when the list is scrolled to its top (before_id). */
    fun loadOlder() {
        val s = _state.value
        if (!s.hasOlder || s.loadingOlder) return
        val first = s.items.firstNotNullOfOrNull { (it as? ChatItem.Msg)?.id } ?: return
        _state.update { it.copy(loadingOlder = true) }
        viewModelScope.launch {
            try {
                val ms = api().get<List<StoredMessage>>("/api/messages?limit=$PAGE&before_id=$first")
                _state.update { st ->
                    val known = st.items.mapTo(HashSet()) { it.key }
                    st.copy(items = ms.map(::toItem).filter { it.key !in known } + st.items, hasOlder = ms.size >= PAGE)
                }
            } catch (_: ApiError) {
            } finally {
                _state.update { it.copy(loadingOlder = false) }
            }
        }
    }

    /** Another phone may have talked to the same assistant: while idle and in view, reload when the newest message changed. */
    suspend fun checkForNews() {
        if (_state.value.busy) return
        refreshing {
            val ms = api().get<List<StoredMessage>>("/api/messages?limit=1")
            if ((ms.lastOrNull()?.id ?: 0) != lastId && !_state.value.busy) {
                stir()   // a new message came in: the character wakes up
                reloadNow()
            }
        }
    }

    /** After a reply: note the newest id, and keep the newest page for the next open (the rows on screen stay as they are). */
    private fun remember() {
        viewModelScope.launch {
            runCatching {
                val api = api()
                val ms = api.get<List<StoredMessage>>("/api/messages?limit=$PAGE")
                lastId = ms.lastOrNull()?.id ?: 0
                sessions.keep(api, Caches.MESSAGES, ms)
            }
        }
    }

    // ---- a reply
    /** The reply being followed put answer buttons under its message */
    private var repliesCame = false

    private fun apply(ev: TurnEvent) {
        _state.update {
            it.copy(
                phase = if (it.phase == null) null else ChatReducer.phase(it.phase, ev, now()),
                items = ChatReducer.apply(it.items, ev, now(), ::key),
            )
        }
        if (ev is TurnEvent.Error || ev is TurnEvent.StepLimit) _events.tryEmit(ChatEvent.ERROR)
        if (ev is TurnEvent.PaymentAuto) _events.tryEmit(ChatEvent.PAYMENT_AUTO)
        if (ev is TurnEvent.Followup) loadFollowups()
        if (ev is TurnEvent.HumanCheck) refreshAsks(force = true)   // the hand-over card (or its folded line) at the bottom, at once
        if (ev is TurnEvent.Replies) repliesCame = true
        if (ev is TurnEvent.PaymentStepChanged) _state.update { it.copy(paymentSteps = PaymentStepRules.upsert(it.paymentSteps, ev.step)) }
        if (ev is TurnEvent.PinNeeded) _state.update { it.copy(pinNeeds = PaymentStepRules.pinUpsert(it.pinNeeds, ev.need)) }
        activity?.let {
            it.turnLine.value = AgentActivityRules.next(it.turnLine.value, ev)
            it.turnKind.value = AgentActivityRules.turnKind(it.turnKind.value, ev)
        }
        watch?.event(ev)
    }

    /**
     * Sends [given] (a card's "에이전트에게 알리기", or 다시 시도) or the typed text; the typed text takes the uploaded attachments
     * along ([words] names them in the message), and they leave the tray. [answers]: the quick-reply fields [given] answers.
     */
    fun send(given: String? = null, words: AttachmentWords? = null, answers: List<ReplyAnswer> = emptyList()) {
        val s = _state.value
        if (s.busy || s.secretAsk != null || s.consentAsk != null || (given == null && s.uploading)) return
        val sending = if (given == null && words != null) s.ready.filter { it.stored != null } else emptyList()
        val names = sending.mapNotNull { it.stored }
        val text = if (given != null) given.trim() else if (words != null) AttachmentText.compose(s.input, names, words) else s.input.trim()
        if (text.isEmpty()) return
        // the files go with the message (the server keeps them with it, chat_attachments.py); nothing typed = the app's own sentence
        val files = sending.map { a -> MessageFile(name = a.name, path = a.stored.orEmpty(), kind = if (a.image) "image" else "document") }
        start(text, fromComposer = given == null, tookAttachments = given == null && words != null, answers = answers, secrets = null,
            files = files, autoText = files.isNotEmpty() && s.input.isBlank())
    }

    // ---- the location card (location_request): the screen asks the phone (permission, place) and hands the answer here
    /** The card shows "getting your location" while the screen reads the place. */
    fun locationReading(approvalId: String) =
        _state.update { it.copy(items = ChatReducer.locationState(it.items, approvalId, ChatItem.LocationAsk.SENDING)) }

    /**
     * POST /api/location-requests/{id}: [place] (lat, lon, name?, country_code?, accuracy?, sublocality?) when shared, null for "no"
     * (the system prompt refused, Not now, or no place could be read). The reply that waits on it goes on by itself.
     */
    fun answerLocation(approvalId: String, place: kotlinx.serialization.json.JsonObject?) {
        val api = runCatching { api() }.getOrNull() ?: return
        _state.update { it.copy(items = ChatReducer.locationState(it.items, approvalId, ChatItem.LocationAsk.SENDING)) }
        viewModelScope.launch {
            val end = try {
                api.post<kotlinx.serialization.json.JsonElement>("/api/location-requests/${KeloApi.segment(approvalId)}", kotlinx.serialization.json.buildJsonObject {
                    put("allowed", kotlinx.serialization.json.JsonPrimitive(place != null))
                    if (place != null) put("place", place)
                })
                if (place != null) ChatItem.LocationAsk.ALLOWED else ChatItem.LocationAsk.REFUSED
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status == 404 || e.status == 409) ChatItem.LocationAsk.EXPIRED else ChatItem.LocationAsk.WAITING   // try again
            } catch (e: Exception) {
                ChatItem.LocationAsk.WAITING
            }
            _state.update { s ->
                s.copy(items = s.items.map { if (it is ChatItem.LocationAsk && it.approvalId == approvalId && it.state == ChatItem.LocationAsk.SENDING) it.copy(state = end) else it })
            }
        }
    }

    /** The uploaded files waiting in the tray, as a message's files (a held-back message sent again takes them along). */
    private fun trayFiles(took: Boolean): List<MessageFile> = if (!took) emptyList() else
        _state.value.ready.filter { it.stored != null }.map { a -> MessageFile(name = a.name, path = a.stored.orEmpty(), kind = if (a.image) "image" else "document") }

    /**
     * Starts the reply to [text]. [secrets] = [SecretGuard.ALLOW] only when the owner chose "Send as is" on the secret sheet. When
     * the server holds the message back for a secret (409), its bubble goes again, the composer and the attachments come back as
     * they were, and [ChatState.secretAsk] opens the sheet.
     */
    private fun start(
        text: String, fromComposer: Boolean, tookAttachments: Boolean, answers: List<ReplyAnswer>, secrets: String?,
        files: List<MessageFile> = emptyList(), autoText: Boolean = false,
    ) {
        val api = runCatching { api() }.getOrNull() ?: return
        val before = _state.value
        val bubble = key()
        _state.update {
            it.copy(
                input = if (fromComposer) "" else it.input,
                attachments = if (tookAttachments) emptyList() else it.attachments,
                busy = true,
                phase = Phase(PhaseKind.WAITING, since = now()),
                items = it.items + ChatItem.Msg(bubble, mine = true, text = text, at = now(), files = files, autoText = autoText),
            )
        }
        if (fromComposer) {
            saving?.cancel()
            drafts?.let { d -> viewModelScope.launch(Dispatchers.IO) { runCatching { d.saveDraft("") } } }
        }
        _events.tryEmit(ChatEvent.SENT)
        activity?.following?.value = true
        stir()
        activity?.turnLine?.value = null
        activity?.turnKind?.value = TurnKind.Thinking
        watch?.started()
        var finished = false
        var failed = false
        turn = viewModelScope.launch {
            try {
                TurnFollower(KeloTurnApi(api)).talk(text, TimeZone.getDefault().id, answers, secrets, files.map { it.path }, autoText) { ev -> apply(ev) }
                finished = true
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                val held = if (secrets == null) SecretGuard.of(e) else null
                if (held != null) {   // nothing was sent to the model or stored: no bubble left behind, the text back where it was
                    fresh -= bubble
                    _state.update { st ->
                        st.copy(
                            items = st.items.filterNot { it.key == bubble },
                            input = if (fromComposer) before.input else st.input,
                            attachments = if (tookAttachments) before.attachments + st.attachments else st.attachments,
                            secretAsk = SecretAsk(
                                held, text, answers, fromComposer, tookAttachments,
                                composer = if (fromComposer) before.input else "",
                                composerSpans = if (fromComposer) SecretGuard.composerSpans(before.input, held.spans) else emptyList(),
                            ),
                        )
                    }
                    return@launch
                }
                val consent = AiConsent.of(e)
                if (consent != null) {   // nobody agreed yet: nothing was stored or sent; the text goes back and the question opens
                    fresh -= bubble
                    _state.update { st ->
                        st.copy(
                            items = st.items.filterNot { it.key == bubble },
                            input = if (fromComposer) before.input else st.input,
                            attachments = if (tookAttachments) before.attachments + st.attachments else st.attachments,
                            consentAsk = ConsentAsk(consent, text, answers, fromComposer, tookAttachments, secrets),
                        )
                    }
                    return@launch
                }
                failed = true
                addError(
                    when {
                        e.isNetwork -> ChatError.Network
                        // only the server's "no model chosen" answer; any other 400 says its own words
                        e.status == 400 && (e.detailJson?.get("code") as? kotlinx.serialization.json.JsonPrimitive)?.content == "model_required" ->
                            ChatError.PickModel(e.detail)
                        else -> ChatError.Message(e.detail)
                    },
                )
            } catch (e: Exception) {
                failed = true
                addError(ChatError.Message(e.message ?: e.javaClass.simpleName))
            } finally {
                when {
                    finished -> watch?.finished(true)
                    failed -> watch?.finished(false)
                    else -> watch?.cancelled()
                }
                turn = null
                activity?.turnLine?.value = null
                activity?.turnKind?.value = TurnKind.Thinking
                activity?.following?.value = false
                stir()   // the reply's end starts the quiet time before the character may sleep
                // the reply came in: the character cheers for a moment
                if (finished) activity?.doneAt?.value = android.os.SystemClock.elapsedRealtime()
                // questions nobody answered are denied by the server once the reply is over
                _state.update { it.copy(busy = false, phase = null, items = ChatReducer.endTurn(it.items), secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
                if (finished) _events.tryEmit(ChatEvent.REPLY_DONE)
                if (repliesCame) {   // the answer buttons ride on the stored message: load the messages again to show them
                    repliesCame = false
                    viewModelScope.launch { runCatching { reloadNow() } }
                } else {
                    remember()   // this phone's own messages are already on screen: do not reload them
                }
                loadFollowups()
                refreshAsks()
                refreshSecrets()
            }
        }
    }

    // ---- the briefing cards on screen (server briefing.py): asked for again when GET /api/agent/status "briefing_rev" changes
    fun refreshBriefings() {
        if (_state.value.items.none { it is ChatItem.Msg && it.briefing != null }) return
        viewModelScope.launch {
            runCatching {
                val ms = api().get<List<StoredMessage>>("/api/messages?limit=$PAGE")
                val fresh = ms.associate { it.id to it.briefing }
                _state.update { it.copy(items = ChatReducer.withBriefings(it.items, fresh)) }
            }
        }
    }

    // ---- secure entries the agent asked for (a key, a login, a card; server secret_requests.py)
    fun refreshSecrets() {
        viewModelScope.launch { runCatching { refreshSecretsNow() } }
        refreshPaymentSteps()
    }

    // ---- payment steps (server payment_steps.py): approve in another app, a one-time code, the page's PIN keypad in the card
    fun refreshPaymentSteps() {
        viewModelScope.launch {
            val fresh = try { PaymentStepCalls(api()).list() } catch (e: Exception) { if (e is CancellationException) throw e; return@launch }
            _state.update { it.copy(paymentSteps = PaymentStepRules.refresh(it.paymentSteps, fresh)) }
        }
    }

    /** [answer]: approved | cancel | new_code | code | done. A step that is gone is folded by the next load. */
    fun answerPaymentStep(stepId: String, answer: String, code: String? = null) {
        viewModelScope.launch {
            try {
                PaymentStepCalls(api()).answer(stepId, answer, code)
                _state.update { s -> s.copy(paymentSteps = s.paymentSteps.map { if (it.stepId == stepId && answer == "new_code") it.copy(state = "renewing") else it }) }
            } catch (e: ApiError) {
                refreshPaymentSteps()
            }
        }
    }

    /** Approvals this phone allowed whose site has a saved PIN: their pin_needed row releases it at once (one more check). */
    private val pinOnAllow = mutableSetOf<String>()

    fun pinAllowed(approvalId: String) { pinOnAllow += approvalId }

    /** True once for a slot this phone allowed: the screen then starts the release by itself. */
    fun takePinOnAllow(slotId: String): Boolean = pinOnAllow.remove(slotId)

    /** "결제 비밀번호 보내기": the owner's check, then the PIN goes once to the payment's slot. */
    fun releasePin(slotId: String, send: suspend () -> chat.kelo.app.wallet.CardSendError?) {
        _state.update { s -> s.copy(pinNeeds = s.pinNeeds.map { if (it.slotId == slotId) it.copy(send = CardSend.Working) else it }) }
        viewModelScope.launch {
            val err = send()
            val result = if (err == null) CardSend.Sent else CardSend.Failed(err)
            _state.update { s -> s.copy(pinNeeds = s.pinNeeds.map { if (it.slotId == slotId) it.copy(send = result) else it }) }
        }
    }

    private suspend fun refreshSecretsNow() {
        val fresh = try { chat.kelo.app.chat.vault.SecretCalls.list(api()) } catch (e: ApiError) { return }
        // the server lists only open entries: answered ones leave the kept list, and their lines leave the rows unless a reply runs
        // here (its end clears them)
        _state.update { it.copy(secrets = chat.kelo.app.chat.vault.SecretRules.refresh(it.secrets, fresh),
            items = if (it.busy) it.items else ChatReducer.dropSettled(it.items)) }
    }

    /**
     * The entry [req] was saved here (the value went straight to the vault or the wallet). [answer]'s turn is a reply the server
     * started to carry on, followed here like this phone's own; its end clears the entry's line. Without a reply on this phone
     * (none follows, or the reply already ended or failed before this answer came) the line goes at once ([SecretRules.followsHere]).
     */
    fun secretSaved(req: chat.kelo.app.chat.vault.SecretRequest, answer: chat.kelo.app.chat.vault.SaveAnswer, result: chat.kelo.app.chat.vault.SecretResult?) {
        settleSecret(req, chat.kelo.app.chat.vault.SecretRequest.STATE_SAVED, result, answer)
        answer.turnId?.takeIf { answer.reply != chat.kelo.app.chat.vault.SaveAnswer.REPLY_NONE }?.let { followTurn(it) }
    }

    /** [Cancel] on an entry: the agent is told (it does not ask again); the card folds at once (or goes, when no reply runs here). */
    fun secretCancelled(req: chat.kelo.app.chat.vault.SecretRequest) {
        settleSecret(req, chat.kelo.app.chat.vault.SecretRequest.STATE_CANCELLED, null, null)
        viewModelScope.launch {
            try {
                val answer = chat.kelo.app.chat.vault.SecretCalls.cancel(api(), req.id)
                answer.turnId?.takeIf { answer.reply != chat.kelo.app.chat.vault.SaveAnswer.REPLY_NONE }?.let { followTurn(it) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status != 404) addError(if (e.isNetwork) ChatError.Network else ChatError.Message(e.detail))
            } catch (_: Exception) {
            }
        }
    }

    /** The entry is no longer open on the server (answered on another phone, or it ran out). */
    fun secretGone(req: chat.kelo.app.chat.vault.SecretRequest) = settleSecret(req, chat.kelo.app.chat.vault.SecretRequest.STATE_EXPIRED, null, null)

    private fun settleSecret(
        req: chat.kelo.app.chat.vault.SecretRequest, state: String, result: chat.kelo.app.chat.vault.SecretResult?,
        answer: chat.kelo.app.chat.vault.SaveAnswer?,
    ) {
        _state.update { s ->
            val done = req.copy(state = state, result = result ?: req.result)
            s.copy(
                items = ChatReducer.afterAnswer(s.items, done, chat.kelo.app.chat.vault.SecretRules.followsHere(answer, s.busy)),
                // the server lists only open entries: an answered one leaves the kept list
                secrets = s.secrets.filterNot { it.id == req.id },
            )
        }
    }

    /**
     * Follows a reply the server started by itself for this phone (a secure entry saved after the reply that asked had ended): the
     * same status line, events and end as a reply sent from here, without a bubble of the user's own.
     */
    fun followTurn(turnId: String) {
        val api = runCatching { api() }.getOrNull() ?: return
        if (_state.value.busy) return
        _state.update { it.copy(busy = true, phase = Phase(PhaseKind.WAITING, since = now())) }
        activity?.following?.value = true
        activity?.turnLine?.value = null
        activity?.turnKind?.value = TurnKind.Thinking
        stir()
        var finished = false
        turn = viewModelScope.launch {
            try {
                TurnFollower(KeloTurnApi(api)).follow(turnId) { ev -> apply(ev) }
                finished = true
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status != 404) addError(if (e.isNetwork) ChatError.Network else ChatError.Message(e.detail))
            } catch (e: Exception) {
                addError(ChatError.Message(e.message ?: e.javaClass.simpleName))
            } finally {
                turn = null
                activity?.turnLine?.value = null
                activity?.following?.value = false
                stir()
                if (finished) activity?.doneAt?.value = android.os.SystemClock.elapsedRealtime()
                _state.update { it.copy(busy = false, phase = null, items = ChatReducer.endTurn(it.items), secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
                if (finished) _events.tryEmit(ChatEvent.REPLY_DONE)
                remember()
                loadFollowups()
                refreshSecrets()
            }
        }
    }

    // ---- the secret sheet (a message held back for a key or password; SecretGuard)
    /** Waits for the wallet's card form after "Save in Keychain" chose a card ([secretToCard]); memory only */
    private var cardRef: SecretAsk? = null

    /**
     * The save step kept [value] in the Keychain (straight from the app to the vault): in the composer, every span holding that value
     * becomes [reference] ("(saved to Keychain: GitHub)"), so the rest of the message can still be sent; the sheet closes.
     */
    fun secretSaved(value: String, reference: String) {
        val a = _state.value.secretAsk ?: return
        _state.update { it.copy(secretAsk = null, input = referenced(it.input, a, reference, value)) }
    }

    /** A card number goes on to the wallet's card form, filled in there from [handoff] (memory only); the sheet closes. */
    fun secretToCard(handoff: SecretHandoff) {
        val a = _state.value.secretAsk ?: return
        handoff.offer(a.values.take(1))
        cardRef = a
        _state.update { it.copy(secretAsk = null) }
    }

    /** The card form kept the card: the composer gets [reference] in place of the number. */
    fun secretCardSaved(reference: String) {
        val a = cardRef ?: return
        cardRef = null
        _state.update { it.copy(input = referenced(it.input, a, reference, null)) }
    }

    /** The composer is changed only while it still holds the text that was put back (the owner may have rewritten it since). */
    private fun referenced(input: String, a: SecretAsk, reference: String, value: String?): String =
        if (!a.fromComposer || input != a.composer) input else SecretGuard.replace(input, a.composerSpans, reference, value)

    /** "Send as is": the very same text again, with `"secrets": "allow"` (only ever after this tap). */
    fun secretSendAsIs() {
        val a = _state.value.secretAsk ?: return
        _state.update { it.copy(secretAsk = null) }
        if (_state.value.busy) return
        start(a.text, a.fromComposer, a.tookAttachments, a.answers, SecretGuard.ALLOW, trayFiles(a.tookAttachments), a.tookAttachments && a.composer.isBlank())
    }

    /** "Edit" (or the sheet dismissed): back to the composer with the text intact; a button's text is put there to be changed. */
    fun secretEdit() {
        val a = _state.value.secretAsk ?: return
        _state.update { it.copy(secretAsk = null, input = if (!a.fromComposer && it.input.isBlank()) a.text else it.input) }
    }

    // ---- asking once before chats go to the AI service ([AiConsent])
    /** The consent call is on its way (the Agree button waits) */
    private val _consentBusy = MutableStateFlow(false)
    val consentBusy: StateFlow<Boolean> = _consentBusy.asStateFlow()
    private val _consentError = MutableStateFlow<String?>(null)
    val consentError: StateFlow<String?> = _consentError.asStateFlow()

    /** "Agree": the server keeps it (once, for every device), then the very same message goes. */
    fun consentAgree() {
        val a = _state.value.consentAsk ?: return
        if (_consentBusy.value) return
        _consentBusy.value = true
        _consentError.value = null
        viewModelScope.launch {
            try {
                api().put<kotlinx.serialization.json.JsonElement>("/api/ai-consent", kotlinx.serialization.json.buildJsonObject {
                    put("accepted", kotlinx.serialization.json.JsonPrimitive(true))
                })
                _state.update { it.copy(consentAsk = null) }
                if (!_state.value.busy) start(a.text, a.fromComposer, a.tookAttachments, a.answers, a.secrets, trayFiles(a.tookAttachments))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _consentError.value = (e as? ApiError)?.detail ?: e.message ?: e.javaClass.simpleName
            } finally {
                _consentBusy.value = false
            }
        }
    }

    /** "Not now" (or the question dismissed): nothing is sent; the text stays in the composer (a button's text is put there). */
    fun consentNotNow() {
        val a = _state.value.consentAsk ?: return
        _consentError.value = null
        _state.update { it.copy(consentAsk = null, input = if (!a.fromComposer && it.input.isBlank()) a.text else it.input) }
    }

    /** 다시 시도 on a reply or an error: the user's message before it goes again, the same way the composer sends it. */
    fun retry(key: String) {
        if (_state.value.busy) return
        val text = MessageActions.retryText(_state.value.items, key) ?: return
        send(text)
    }

    /** The Stop button: cancelling the reply tells the server to stop (DELETE /api/turns/{id}). */
    fun stop() {
        turn?.cancel()
    }

    /** Something the screen itself could not do (the camera), shown as an error in the chat. */
    fun showError(e: ChatError) = addError(e)

    private fun addError(e: ChatError) {
        _state.update { it.copy(items = it.items + ChatItem.Error(key(), e)) }
        _events.tryEmit(ChatEvent.ERROR)
    }

    /** Answers an approval; [rememberSchedule]: keep allowing this step in the scheduled job it came from. */
    fun approve(id: String, ok: Boolean, rememberSchedule: Boolean = false) {
        val open = _state.value.items.any { it is ChatItem.Approval && it.id == id && it.open }
        if (!open) return
        _state.update { s ->
            s.copy(
                items = ChatReducer.answer(s.items, id, ok),
                phase = if (s.busy) Phase(PhaseKind.WAITING, since = now()) else s.phase,
            )
        }
        viewModelScope.launch {
            try {
                api().post<JsonElement>("/api/approvals/${KeloApi.segment(id)}", buildJsonObject {
                    put("approve", ok)
                    if (ok && rememberSchedule) put("remember", "schedule")
                })
                if (ok) chat.kelo.app.device.DeviceTasks.afterApproval()   // a text, call or calendar change may now wait for this phone
            } catch (_: ApiError) {
                // expired: the reply reports the denial
            }
        }
    }

    /**
     * Allow on a payment with an end-to-end card: [send] asks for the owner's check, decrypts the card and sends it to the
     * approval's slot; only when it got there is the payment allowed. A failure leaves the question open (the user can try again
     * or say no) and says why; a slot that is gone means the question is gone too.
     */
    fun approveWithCard(id: String, send: suspend () -> chat.kelo.app.wallet.CardSendError?) {
        val item = _state.value.items.firstOrNull { it is ChatItem.Approval && it.id == id } as? ChatItem.Approval ?: return
        if (!item.open || item.send == CardSend.Working) return
        runCardSend(id, send) { approve(id, true) }
    }

    /** "카드 보내기" on a card_needed row (the payment was allowed already). */
    fun sendCard(approvalId: String, send: suspend () -> chat.kelo.app.wallet.CardSendError?) {
        val item = _state.value.items.firstOrNull { it is ChatItem.CardNeeded && it.approvalId == approvalId } as? ChatItem.CardNeeded ?: return
        if (item.ended || item.send == CardSend.Working || item.send == CardSend.Sent) return
        runCardSend(approvalId, send) {}
    }

    private fun runCardSend(id: String, send: suspend () -> chat.kelo.app.wallet.CardSendError?, then: () -> Unit) {
        _state.update { it.copy(items = ChatReducer.cardSend(it.items, id, CardSend.Working)) }
        viewModelScope.launch {
            val err = try { send() } catch (e: CancellationException) { throw e } catch (e: Exception) { chat.kelo.app.wallet.CardSendError.OTHER }
            if (err == null) {
                _state.update { it.copy(items = ChatReducer.cardSend(it.items, id, CardSend.Sent)) }
                then()
            } else {
                _state.update { s ->
                    var items = ChatReducer.cardSend(s.items, id, CardSend.Failed(err))
                    if (err == chat.kelo.app.wallet.CardSendError.NOT_WAITING) {
                        items = items.map { if (it is ChatItem.Approval && it.id == id && it.open) it.copy(expired = true) else it }
                    }
                    s.copy(items = items)
                }
                if (err != chat.kelo.app.wallet.CardSendError.CANCELLED) _events.tryEmit(ChatEvent.ERROR)
            }
        }
    }

    // ---- the chips under the list
    fun pickEffort(value: String, onProfile: (Profile) -> Unit) {
        viewModelScope.launch {
            try {
                onProfile(api().put<Profile>("/api/profile", buildJsonObject { put("effort", value) }))
            } catch (e: ApiError) {
                addError(ChatError.Message(e.detail))
            }
        }
    }

    /** The user finished or closed a secure / connect card in the chat: it folds into one line. */
    fun settleCard(key: String, state: CardState) = _state.update { it.copy(items = ChatReducer.settleCard(it.items, key, state)) }

    /** The in-chat address form [formId] was answered, cancelled or found gone here: its card folds into one line. */
    fun shippingAnswered(formId: String, outcome: ShippingOutcome) {
        _state.update {
            it.copy(
                items = when (outcome) {
                    is ShippingOutcome.Sent -> ChatReducer.settleShipping(it.items, formId, ShippingFormState.DONE, outcome.label, outcome.line)
                    ShippingOutcome.Cancelled -> ChatReducer.settleShipping(it.items, formId, ShippingFormState.CANCELLED)
                    ShippingOutcome.Gone -> ChatReducer.settleShipping(it.items, formId, ShippingFormState.EXPIRED)
                },
                phase = if (it.busy && outcome !is ShippingOutcome.Gone) Phase(PhaseKind.WAITING, since = now()) else it.phase,
            )
        }
    }

    // ---- something shared from another app
    /** Text goes into the draft (after what is already there); a document goes into the tray like one picked with "+". */
    fun receive(shared: Shared) {
        when (shared) {
            is Shared.Text -> setInput(DraftText.append(_state.value.input, shared.text))
            is Shared.File -> attach(image = false, label = shared.name) {
                val f = java.io.File(shared.path)
                if (f.length() > MAX_UPLOAD) { f.delete(); throw AttachmentReader.TooLarge() }
                Prepared(shared.name, null, shared.mime, length = f.length(), open = { f.inputStream() }, done = { f.delete() })
            }
            is Shared.Unsupported -> addError(ChatError.Unsupported(shared.name))
            is Shared.Failed -> addError(if (shared.tooLarge) ChatError.TooLarge else ChatError.ShareFailed)
        }
    }

    // ---- attachments ("+": a photo, the camera, a file)
    private val uploads = HashMap<String, Job>()

    /**
     * Puts a chip in the tray at once and uploads in the background (PUT /api/uploads/{name}): [prepare] reads the pick (and makes
     * a photo smaller) off the main thread. A failure stays on the chip with the reason (the server's own words when it said no).
     */
    fun attach(image: Boolean, label: String, prepare: () -> Prepared) {
        val api = runCatching { api() }.getOrNull() ?: return
        val id = "a${++seq}"
        _state.update { it.copy(attachments = it.attachments + Attachment(id, label, image)) }
        uploads[id] = viewModelScope.launch {
            var prepared: Prepared? = null
            try {
                val p = withContext(Dispatchers.IO) { prepare() }
                prepared = p
                if (p.length > MAX_UPLOAD) throw AttachmentReader.TooLarge()   // checked before anything is sent
                change(id) { it.copy(name = p.name, total = p.length.coerceAtLeast(0), thumbnail = p.thumbnail) }
                var shown = -1
                val progress: (Long, Long) -> Unit = { sent, total ->
                    if (sent > MAX_UPLOAD) throw AttachmentReader.TooLarge()
                    val pct = if (total > 0) (sent * 100 / total).toInt() else 0
                    if (pct != shown) {   // a few dozen updates for a whole file, not one per chunk
                        shown = pct
                        change(id) { it.copy(sent = sent, total = total) }
                    }
                }
                val bytes = p.bytes
                val path = if (bytes != null) api.upload(p.name, bytes, p.type, progress)
                else api.uploadStream(p.name, p.length, p.type, p.open ?: throw AttachmentReader.Unreadable(), progress)
                val stored = path.removePrefix("uploads/").ifEmpty { p.name }
                change(id) { it.copy(state = AttachState.DONE, stored = stored, sent = it.total) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: AttachmentReader.TooLarge) {
                fail(id, AttachError.TooLarge)
            } catch (e: ApiError) {
                fail(id, when {
                    e.cause is AttachmentReader.TooLarge -> AttachError.TooLarge
                    e.isNetwork -> AttachError.Network
                    e.status == 413 -> AttachError.TooLarge
                    else -> AttachError.Server(e.detail)
                })
            } catch (e: Exception) {
                android.util.Log.w("KeloAttach", "could not read the attachment", e)
                fail(id, AttachError.Unreadable)
            } finally {
                uploads.remove(id)
                prepared?.done?.invoke()
            }
        }
    }

    private fun change(id: String, f: (Attachment) -> Attachment) =
        _state.update { s -> s.copy(attachments = s.attachments.map { if (it.id == id) f(it) else it }) }

    private fun fail(id: String, e: AttachError) {
        change(id) { it.copy(state = AttachState.FAILED, error = e) }
        _events.tryEmit(ChatEvent.ERROR)
    }

    /**
     * The chip's ×: an upload still running is cancelled; one already up is taken off the server again (DELETE /api/uploads/{name}),
     * since nothing was sent with it.
     */
    fun removeAttachment(id: String) {
        val a = _state.value.attachments.firstOrNull { it.id == id } ?: return
        uploads.remove(id)?.cancel()
        _state.update { s -> s.copy(attachments = s.attachments.filterNot { it.id == id }) }
        val stored = a.stored
        if (a.state == AttachState.DONE && stored != null) {
            viewModelScope.launch {
                runCatching { api().delete<JsonElement>("/api/uploads/${KeloApi.segment(stored)}") }
            }
        }
    }


    private fun now() = System.currentTimeMillis()

    private fun toItem(m: StoredMessage) =
        ChatItem.Msg("m${m.id}", mine = m.role == "user", text = m.content, id = m.id, replies = m.replies, at = (m.createdAt * 1000).toLong(),
            reaction = m.reaction, briefing = m.briefing, images = m.images, files = m.attachments, autoText = m.autoText)

    // ---- follow-up buttons under the agent's messages
    /**
     * Does one button of the follow-up [item] ([step] from [chat.kelo.app.followup.FollowupRules.step]). Done or already settled
     * elsewhere: the buttons give way to one line and the list loads again. Failed: they stay, and the reason shows in the chat.
     */
    fun runAction(item: chat.kelo.app.followup.Followup, step: chat.kelo.app.followup.FollowupStep) {
        val s = _state.value
        if (item.id in s.acting || item.id in s.outcomes) return
        if (step is chat.kelo.app.followup.FollowupStep.OpenLogin) return   // the screen opens the sign-in sheet
        val api = runCatching { api() }.getOrNull() ?: return
        _state.update { it.copy(acting = it.acting + item.id) }
        viewModelScope.launch {
            val (result, detail) = chat.kelo.app.followup.FollowupCalls(api).run(step)
            val outcome = ChatReducer.outcome(step, result)
            _state.update { st -> st.copy(acting = st.acting - item.id, outcomes = if (outcome != null) st.outcomes + (item.id to outcome) else st.outcomes) }
            if (result == chat.kelo.app.followup.StepResult.FAILED) addError(if (detail.isBlank()) ChatError.Network else ChatError.Message(detail))
            else runCatching { loadFollowupsNow() }
        }
    }

    /** The sign-in sheet opened from a button was filled in: its buttons give way to one line. */
    fun actionSignedIn(item: chat.kelo.app.followup.Followup) =
        _state.update { it.copy(outcomes = it.outcomes + (item.id to ActionOutcome.SIGNED_IN)) }

    companion object {
        const val PAGE = 100
        const val DRAFT_DELAY_MS = 400L
        /** The server's MAX_UPLOAD ("up to 100 MB", under Cloudflare's 100 MB body limit) */
        const val MAX_UPLOAD = 99_000_000L
    }
}
