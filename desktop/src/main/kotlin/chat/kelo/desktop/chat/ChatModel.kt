package chat.kelo.desktop.chat

import chat.kelo.app.chat.AgentActivityRules
import chat.kelo.app.chat.AskEnd
import chat.kelo.app.chat.ChatAsks
import chat.kelo.app.agent.PendingApproval
import chat.kelo.app.followup.Followup
import chat.kelo.app.followup.FollowupCalls
import chat.kelo.app.followup.FollowupRules
import chat.kelo.app.followup.FollowupStep
import chat.kelo.app.followup.StepResult
import kotlinx.coroutines.flow.drop
import chat.kelo.app.chat.ChatError
import chat.kelo.app.chat.ChatItem
import chat.kelo.app.chat.ChatReducer
import chat.kelo.app.chat.KeloTurnApi
import chat.kelo.app.chat.MessageActions
import chat.kelo.app.chat.Phase
import chat.kelo.app.chat.PhaseKind
import chat.kelo.app.chat.ProviderInfo
import chat.kelo.app.chat.StoredMessage
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.chat.TurnFollower
import chat.kelo.app.chat.TurnKind
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Profile
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.DesktopCaches
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.util.TimeZone
import kotlin.coroutines.cancellation.CancellationException

/** A file dropped on the chat or picked with "+", going up (PUT /api/uploads/{name}) and then sent with the next message. */
data class Attachment(
    val id: String,
    val name: String,
    val state: AttachState = AttachState.UPLOADING,
    val sent: Long = 0,
    val total: Long = 0,
    val stored: String? = null,
    val error: AttachError? = null,
) {
    val progress: Float? get() = if (total > 0) (sent.toFloat() / total).coerceIn(0f, 1f) else null
}

enum class AttachState { UPLOADING, DONE, FAILED }

sealed interface AttachError {
    data class Server(val text: String) : AttachError
    data object TooLarge : AttachError
    data object Network : AttachError
    data object Unreadable : AttachError
}

/** The words a message uses for its attachments (android/kelo .../chat/Attachments.kt AttachmentText, same rules). */
class AttachmentWords(val line: (String) -> String, val ask: (String) -> String, val separator: String)

object AttachmentText {
    fun compose(text: String, names: List<String>, words: AttachmentWords): String {
        val typed = text.trim()
        if (names.isEmpty()) return typed
        if (typed.isEmpty()) return words.ask(names.joinToString(words.separator))
        return typed + "\n\n" + names.joinToString("\n") { words.line(it) }
    }
}

/**
 * A message the server refused with 409 `secret_in_message` (as on the phone, android .../chat/ChatViewModel.kt): [text] is exactly
 * what was sent, [composer] the composer's text put back and [composerSpans] the spans moved onto it. Memory only; never printed.
 */
class SecretAsk(
    val found: chat.kelo.app.chat.SecretFound, val text: String, val answers: List<chat.kelo.app.chat.ReplyAnswer>,
    val fromComposer: Boolean, val tookAttachments: Boolean,
    val composer: String = "", val composerSpans: List<chat.kelo.app.chat.SecretSpan> = emptyList(),
) {
    val values: List<String> get() = chat.kelo.app.chat.SecretGuard.values(text, found.spans)
    override fun toString(): String = "SecretAsk(${found.target.type}, ${found.spans.size} spans)"
}

data class ChatState(
    val items: List<ChatItem> = emptyList(),
    val loaded: Boolean = false,
    val busy: Boolean = false,
    val phase: Phase? = null,
    val input: String = "",
    val providers: List<ProviderInfo> = emptyList(),
    val hasOlder: Boolean = false,
    val loadingOlder: Boolean = false,
    val offline: Boolean = false,
    val attachments: List<Attachment> = emptyList(),
    /** The site and tab of each human check this chat has shown (approval id -> where) */
    val checks: Map<String, CheckPlace> = emptyMap(),
    /** The follow-up each agent message carries (GET /api/followups, by its message_id): its buttons go under that message */
    val followups: Map<Long, chat.kelo.app.followup.Followup> = emptyMap(),
    /** Follow-ups with a button being done right now (by follow-up id) */
    val acting: Set<String> = emptySet(),
    /** What a button pressed here did (by follow-up id), shown once its item is no longer open */
    val outcomes: Map<String, chat.kelo.app.chat.ActionOutcome> = emptyMap(),
    /** Questions waiting on the user that no followed reply shows: a background job's approval, a scheduled run's (ChatAsks) */
    val asks: List<chat.kelo.app.chat.ChatAsk> = emptyList(),
    /** A message the server held back because it holds a key or password (chat.kelo.app.chat.SecretGuard): the dialog asks */
    val secretAsk: SecretAsk? = null,
    /** A message held back until someone agrees that chats go to the AI service (chat.kelo.app.chat.AiConsent) */
    val consentAsk: chat.kelo.app.chat.ConsentAsk? = null,
    /** The Agree call is on its way; its error, if it failed */
    val consentBusy: Boolean = false,
    val consentError: String? = null,
    /** In-chat address forms answered here with "add a card" as the way to pay (form ids): the folded line says where cards are added */
    val shippingCard: Set<String> = emptySet(),
    /** Secure entries the agent asked for (GET /api/secret-requests; the ones answered here stay folded until the next load) */
    val secrets: List<chat.kelo.app.chat.vault.SecretRequest> = emptyList(),
    /** Payment steps that need the user (server payment_steps.py): approve in another app, a one-time code, the page's keypad */
    val paymentSteps: List<chat.kelo.app.chat.PaymentStep> = emptyList(),
    /** Payment PINs waiting for the phone that saved them (only a phone can release one; this computer says so) */
    val pinNeeds: List<chat.kelo.app.chat.PinNeed> = emptyList(),
) {
    val uploading: Boolean get() = attachments.any { it.state == AttachState.UPLOADING }
    val ready: List<Attachment> get() = attachments.filter { it.state == AttachState.DONE }
}

/**
 * The desktop chat: history, sending and following a reply (chat.kelo.app.chat.TurnFollower: short requests to
 * GET /api/turns/{id}, so the reply appears as it is written, through any tunnel), approvals and uploads. The list itself is
 * built by the shared chat.kelo.app.chat.ChatReducer, exactly as on the phone. The unsent text is kept encrypted
 * (chat.kelo.app.core.SecureStore) between launches.
 */
class ChatModel(private val app: AppContainer, private val dispatcher: kotlinx.coroutines.CoroutineDispatcher = Dispatchers.Main) {
    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val sessions = app.sessions
    private val activity = app.activity

    private val kept: List<StoredMessage>? = runCatching { sessions.cached(DesktopCaches.MESSAGES) }.getOrNull()

    private val _state = MutableStateFlow(
        ChatState(
            input = runCatching { app.store.loadDraft() }.getOrDefault(""),
            items = kept.orEmpty().map(::toItem),
            loaded = kept != null,
            hasOlder = (kept?.size ?: 0) >= PAGE,
            providers = ModelCatalog.keptProviderInfos(sessions),   // the last list at once (kept on this computer), refreshed by loadProviders
        ),
    )
    val state: StateFlow<ChatState> = _state.asStateFlow()

    /** "reply done" / "failed": the window may be hidden, then the tray says so */
    val finished = MutableSharedFlow<Boolean>(extraBufferCapacity = 4)

    private var seq = 0
    private fun key(): String = "k${++seq}"
    private var lastId = kept?.lastOrNull()?.id ?: 0L
    private var turn: Job? = null
    private var saving: Job? = null

    private fun api(): KeloApi = sessions.requireApi()

    init {
        // 마이 → 대화 기록 삭제: the rows on screen are gone on the server; ask again (the server now has none)
        scope.launch {
            sessions.historyCleared.drop(1).collect {
                lastId = 0
                _state.update { it.copy(items = emptyList(), hasOlder = false, followups = emptyMap(), asks = emptyList()) }
                reload()
            }
        }
        // the status poll says when the waiting approvals changed: ask for the list then
        scope.launch {
            var before: Pair<Int, Int>? = null
            activity.status.collect { s ->
                val k = ChatAsks.pollKey(s) ?: return@collect
                if (k != before) { before = k; refreshAsks() }
            }
        }
        // a payment step opened by work the chat is not following (a job, a scheduled run, a phone): looked for while the agent
        // works, and while one is open here
        scope.launch {
            activity.status.collect { s ->
                if (s?.busy == true || _state.value.paymentSteps.any { it.open }) refreshPaymentSteps()
            }
        }
        // a secure entry asked for or answered elsewhere (a phone, the Keychain page): the chat's list follows
        scope.launch {
            var before: Int? = null
            activity.status.collect { s ->
                val n = s?.secretRequests ?: return@collect
                if (n != before) { val first = before == null; before = n; if (!first || n > 0) refreshSecrets() }
            }
        }
        // a briefing card on screen changed elsewhere (its hours line answered on a phone, or here): the card follows (as the phone)
        scope.launch {
            var before: String? = null
            activity.status.collect { s ->
                val rev = s?.briefingRev ?: return@collect
                if (rev != before) { before = rev; refreshBriefings() }
            }
        }
    }

    /** The briefing cards on screen, asked for again when GET /api/agent/status "briefing_rev" changes ([ChatReducer.withBriefings]). */
    fun refreshBriefings() {
        if (_state.value.items.none { it is chat.kelo.app.chat.ChatItem.Msg && it.briefing != null }) return
        scope.launch {
            runCatching {
                val ms = api().get<List<chat.kelo.app.chat.StoredMessage>>("/api/messages?limit=$PAGE")
                val fresh = ms.associate { it.id to it.briefing }
                _state.update { it.copy(items = chat.kelo.app.chat.ChatReducer.withBriefings(it.items, fresh)) }
            }
        }
    }

    // ---- secure entries the agent asked for (a key, a login, a card): same rules as the phone (ChatReducer.withPending: open ones only; placeAnswered; SecretRules.settled at reply ends and reload)
    fun refreshSecrets() {
        scope.launch { runCatching { refreshSecretsNow() } }
        refreshPaymentSteps()
    }

    // ---- payment steps (server payment_steps.py), as android/kelo .../chat/ChatViewModel.kt
    fun refreshPaymentSteps() {
        scope.launch {
            val fresh = try { chat.kelo.app.chat.PaymentStepCalls(api()).list() } catch (e: Exception) { if (e is CancellationException) throw e; return@launch }
            _state.update { it.copy(paymentSteps = chat.kelo.app.chat.PaymentStepRules.refresh(it.paymentSteps, fresh)) }
        }
    }

    /** [answer]: approved | cancel | new_code | code | done. A step that is gone is folded by the next load. */
    fun answerPaymentStep(stepId: String, answer: String, code: String? = null) {
        scope.launch {
            try {
                chat.kelo.app.chat.PaymentStepCalls(api()).answer(stepId, answer, code)
                _state.update { s -> s.copy(paymentSteps = s.paymentSteps.map { if (it.stepId == stepId && answer == "new_code") it.copy(state = "renewing") else it }) }
            } catch (e: ApiError) {
                refreshPaymentSteps()
            }
        }
    }

    private suspend fun refreshSecretsNow() {
        val fresh = try { chat.kelo.app.chat.vault.SecretCalls.list(api()) } catch (e: ApiError) { return }
        // the server lists only open entries: answered ones leave the kept list, and their lines leave the rows unless a reply runs
        // here (its end clears them)
        _state.update { it.copy(secrets = chat.kelo.app.chat.vault.SecretRules.refresh(it.secrets, fresh),
            items = if (it.busy) it.items else ChatReducer.dropSettled(it.items)) }
    }

    /** Saved here: the card folds; a reply the server started to carry on is followed like this app's own. */
    fun secretSaved(req: chat.kelo.app.chat.vault.SecretRequest, answer: chat.kelo.app.chat.vault.SaveAnswer, result: chat.kelo.app.chat.vault.SecretResult?) {
        settleSecret(req, chat.kelo.app.chat.vault.SecretRequest.STATE_SAVED, result, answer)
        answer.turnId?.takeIf { answer.reply != chat.kelo.app.chat.vault.SaveAnswer.REPLY_NONE }?.let { followTurn(it) }
    }

    /** [Cancel] on an entry: the agent is told; the card folds at once (or goes, when no reply runs here); a reply it starts is followed. */
    fun secretCancelled(req: chat.kelo.app.chat.vault.SecretRequest) {
        settleSecret(req, chat.kelo.app.chat.vault.SecretRequest.STATE_CANCELLED, null, null)
        scope.launch {
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

    fun secretGone(req: chat.kelo.app.chat.vault.SecretRequest) = settleSecret(req, chat.kelo.app.chat.vault.SecretRequest.STATE_EXPIRED, null, null)

    /** As the phone (spec 5.16): the line folds when a reply here will end later (SecretRules.followsHere), else it goes at once. */
    private fun settleSecret(
        req: chat.kelo.app.chat.vault.SecretRequest, state: String, result: chat.kelo.app.chat.vault.SecretResult?,
        answer: chat.kelo.app.chat.vault.SaveAnswer?,
    ) {
        _state.update { s ->
            val done = req.copy(state = state, result = result ?: req.result)
            s.copy(
                items = ChatReducer.afterAnswer(s.items, done, chat.kelo.app.chat.vault.SecretRules.followsHere(answer, s.busy)),
                secrets = s.secrets.filterNot { it.id == req.id },   // the server lists only open entries
            )
        }
    }

    /** Follows a reply the server started by itself (a secure entry saved after the asking reply had ended). */
    fun followTurn(turnId: String) {
        val api = runCatching { api() }.getOrNull() ?: return
        if (_state.value.busy) return
        _state.update { it.copy(busy = true, phase = Phase(PhaseKind.WAITING, since = now())) }
        activity.following.value = true
        stir()
        turn = scope.launch {
            var done = false
            try {
                TurnFollower(KeloTurnApi(api)).follow(turnId) { ev -> apply(ev) }
                done = true
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status != 404) addError(if (e.isNetwork) ChatError.Network else ChatError.Message(e.detail))
            } catch (e: Exception) {
                addError(ChatError.Message(e.message ?: e.javaClass.simpleName))
            } finally {
                turn = null
                activity.turnLine.value = null
                activity.following.value = false
                stir()
                _state.update { it.copy(busy = false, phase = null, items = ChatReducer.endTurn(it.items), secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
                if (done) finished.tryEmit(true)
                remember()
                refreshSecrets()
            }
        }
    }

    // ---- questions from work the chat is not following (a background job, a scheduled run)
    /** GET /api/approvals/pending joined into the chat's waiting cards; nothing is asked when the poll says nothing waits. */
    fun refreshAsks(force: Boolean = false) {
        if (!force && ChatAsks.skipFetch(activity.status.value, _state.value.asks)) {
            _state.update { it.copy(asks = ChatAsks.merge(it.asks, emptyList(), it.items.lastOrNull()?.key),
                items = ChatReducer.withPendingLocations(it.items, emptyList(), it.busy)) }
            return
        }
        scope.launch {
            var locations = emptyList<TurnEvent.LocationRequest>()
            val pending = try {
                api().request("GET", "/api/approvals/pending").let {
                    // location cards are not approvals: listed so a reloaded chat shows the waiting one again (as on the phone)
                    locations = ((it as? kotlinx.serialization.json.JsonObject)?.get("items") as? kotlinx.serialization.json.JsonArray).orEmpty()
                        .mapNotNull { e -> TurnEvent.parse(e) as? TurnEvent.LocationRequest }
                    PendingApproval.parseList(it) to PendingApproval.parseResolved(it)
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
     * [rememberSchedule]: in that scheduled job). A payment with a card kept on a phone can only be refused here.
     */
    fun answerAsk(id: String, approve: Boolean, rememberSite: Boolean = false, rememberSchedule: Boolean = false) {
        val ask = _state.value.asks.firstOrNull { it.id == id } ?: return
        if (!ask.open || ask.working) return
        val a = ask.approval
        _state.update { it.copy(asks = ChatAsks.working(it.asks, id, true)) }
        scope.launch {
            try {
                api().post<JsonElement>("/api/approvals/${KeloApi.segment(id)}", buildJsonObject {
                    put("approve", approve)
                    if (approve && rememberSite && a.canRemember) put("remember", "site")
                    else if (approve && rememberSchedule && a.canRememberSchedule) put("remember", "schedule")
                })
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

    // ---- follow-up buttons under the agent's messages
    /** The follow-ups and the messages they belong to (also after a reply, and when a follow-up notification is opened). */
    fun loadFollowups() {
        scope.launch { runCatching { loadFollowupsNow() } }
    }

    private suspend fun loadFollowupsNow() {
        val list = try { FollowupCalls(api()).list() } catch (e: ApiError) { return }
        _state.update { it.copy(followups = FollowupRules.byMessage(list.items)) }
    }

    /** Does one button of the follow-up [item]: done or settled elsewhere, the buttons give way to one line; failed, they stay. */
    fun runAction(item: Followup, step: FollowupStep) {
        val s = _state.value
        if (item.id in s.acting || item.id in s.outcomes) return
        if (step is FollowupStep.OpenLogin) return   // the screen opens the sign-in form
        val api = runCatching { api() }.getOrNull() ?: return
        _state.update { it.copy(acting = it.acting + item.id) }
        scope.launch {
            val (result, detail) = FollowupCalls(api).run(step)
            val outcome = ChatReducer.outcome(step, result)
            _state.update { st -> st.copy(acting = st.acting - item.id, outcomes = if (outcome != null) st.outcomes + (item.id to outcome) else st.outcomes) }
            if (result == StepResult.FAILED) addError(if (detail.isBlank()) ChatError.Network else ChatError.Message(detail))
            else runCatching { loadFollowupsNow() }
        }
    }

    /** The sign-in form opened from a button was filled in: its buttons give way to one line. */
    fun actionSignedIn(item: Followup) =
        _state.update { it.copy(outcomes = it.outcomes + (item.id to chat.kelo.app.chat.ActionOutcome.SIGNED_IN)) }

    fun close() {
        saveDraftNow()
        scope.cancel()
    }

    fun setInput(v: String) {
        _state.update { it.copy(input = v) }
        stir()   // the owner is typing: the character wakes up
        saving?.cancel()
        saving = scope.launch {
            delay(DRAFT_DELAY_MS)
            val text = _state.value.input
            withContext(Dispatchers.IO) { runCatching { app.store.saveDraft(text) } }
        }
    }

    fun saveDraftNow() {
        saving?.cancel()
        if (sessions.api.value == null) return
        runCatching { app.store.saveDraft(_state.value.input) }
    }

    fun loadProviders() {
        scope.launch {
            runCatching { ModelCatalog.fetchProviderInfos(sessions) }.onSuccess { p -> _state.update { it.copy(providers = p) } }
        }
    }

    /** A fresh provider list from the model settings (after a key, an address or a sign-in). */
    fun setProviders(p: List<ProviderInfo>) {
        _state.update { it.copy(providers = p) }
        runCatching { sessions.keep(DesktopCaches.PROVIDERS, p) }
    }

    fun reload() {
        scope.launch { refreshing { reloadNow() } }
    }

    private suspend fun reloadNow() {
        val api = api()
        val ms = api.get<List<StoredMessage>>("/api/messages?limit=$PAGE")
        sessions.keep(api, DesktopCaches.MESSAGES, ms)
        if (_state.value.busy) return
        lastId = ms.lastOrNull()?.id ?: 0
        _state.update { it.copy(items = ms.map(::toItem), loaded = true, hasOlder = ms.size >= PAGE, offline = false,
            secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
        loadFollowupsNow()
        refreshSecretsNow()
        refreshPaymentSteps()   // a payment step left open (a reload, another device) shows again at the bottom
    }

    private suspend fun refreshing(block: suspend () -> Unit) {
        try {
            block()
            if (_state.value.offline) _state.update { it.copy(offline = false) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiError) {
            if (!e.isUnauthorized) _state.update { it.copy(offline = true, loaded = true) }
        } catch (e: Exception) {
            _state.update { it.copy(offline = true, loaded = true) }
        }
    }

    fun loadOlder() {
        val s = _state.value
        if (!s.hasOlder || s.loadingOlder) return
        val first = s.items.firstNotNullOfOrNull { (it as? ChatItem.Msg)?.id } ?: return
        _state.update { it.copy(loadingOlder = true) }
        scope.launch {
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

    /** Another device may have talked to the same agent: while idle, reload when the newest message changed. */
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

    /** Something happened in the chat (typing, a message sent or come in, a reply ending): the character stays awake a while. */
    private fun stir() {
        activity.touch(System.nanoTime() / 1_000_000)
    }

    private var repliesCame = false

    /** A turn event as if a reply brought it (tests). */
    internal fun applyForTest(ev: TurnEvent) = apply(ev)

    private fun apply(ev: TurnEvent) {
        _state.update {
            it.copy(
                phase = if (it.phase == null) null else ChatReducer.phase(it.phase, ev, now()),
                items = ChatReducer.apply(it.items, ev, System.currentTimeMillis(), ::key).let { items -> if (ev is TurnEvent.HumanCheck) Handover.settle(items, ev) else items },
                checks = Handover.places(it.checks, ev),
            )
        }
        if (ev is TurnEvent.Replies) repliesCame = true
        if (ev is TurnEvent.PaymentStepChanged) _state.update { it.copy(paymentSteps = chat.kelo.app.chat.PaymentStepRules.upsert(it.paymentSteps, ev.step)) }
        if (ev is TurnEvent.PinNeeded) _state.update { it.copy(pinNeeds = chat.kelo.app.chat.PaymentStepRules.pinUpsert(it.pinNeeds, ev.need)) }
        activity.turnLine.value = AgentActivityRules.next(activity.turnLine.value, ev)
        activity.turnKind.value = AgentActivityRules.turnKind(activity.turnKind.value, ev)
    }

    /**
     * "Not now" on a location request (POST /api/location-requests/{id} {"allowed": false}): this computer has no place of its own to
     * give (a phone shares it); the reply that waits on it goes on by itself.
     */
    fun refuseLocation(approvalId: String) {
        val api = runCatching { api() }.getOrNull() ?: return
        _state.update { it.copy(items = ChatReducer.locationState(it.items, approvalId, ChatItem.LocationAsk.SENDING)) }
        scope.launch {
            val end = try {
                api.post<JsonElement>("/api/location-requests/${KeloApi.segment(approvalId)}", buildJsonObject { put("allowed", false) })
                ChatItem.LocationAsk.REFUSED
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

    /** Sends [given] (a quick reply, 다시 시도) or the typed text with the uploaded attachments; [answers]: the quick-reply fields
     *  [given] answers. */
    fun send(given: String? = null, words: AttachmentWords? = null, answers: List<chat.kelo.app.chat.ReplyAnswer> = emptyList()) {
        val s = _state.value
        if (s.busy || s.secretAsk != null || s.consentAsk != null || (given == null && s.uploading)) return
        val names = if (given == null && words != null) s.ready.mapNotNull { it.stored } else emptyList()
        val text = if (given != null) given.trim() else if (words != null) AttachmentText.compose(s.input, names, words) else s.input.trim()
        if (text.isEmpty()) return
        // the files go with the message (the server keeps them with it); nothing typed = the app's own sentence (auto_text)
        val took = given == null && words != null
        start(text, fromComposer = given == null, tookAttachments = took, answers = answers, secrets = null,
            files = trayFiles(took), autoText = took && names.isNotEmpty() && s.input.isBlank())
    }

    /** The uploaded files waiting in the tray, as a message's files (a held-back message sent again takes them along). */
    private fun trayFiles(took: Boolean): List<chat.kelo.app.chat.MessageFile> = if (!took) emptyList() else
        _state.value.ready.mapNotNull { a -> a.stored?.let { chat.kelo.app.chat.MessageFile(name = a.name, path = it, kind = MessageFileRules.kindOf(a.name)) } }

    /**
     * Starts the reply to [text]; [secrets] = "allow" only after "Send as is" on the secret dialog. A 409 secret_in_message takes the
     * bubble away again, puts the composer and attachments back and opens the dialog ([ChatState.secretAsk]).
     */
    private fun start(
        text: String, fromComposer: Boolean, tookAttachments: Boolean, answers: List<chat.kelo.app.chat.ReplyAnswer>, secrets: String?,
        files: List<chat.kelo.app.chat.MessageFile> = trayFiles(tookAttachments), autoText: Boolean = false,
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
                items = it.items + ChatItem.Msg(bubble, mine = true, text = text, at = System.currentTimeMillis(), files = files, autoText = autoText),
            )
        }
        if (fromComposer) {
            saving?.cancel()
            scope.launch(Dispatchers.IO) { runCatching { app.store.saveDraft("") } }
        }
        activity.following.value = true
        stir()
        activity.turnLine.value = null
        activity.turnKind.value = TurnKind.Thinking
        var done = false
        var failed = false
        turn = scope.launch {
            try {
                TurnFollower(KeloTurnApi(api)).talk(text, TimeZone.getDefault().id, answers, secrets, files.map { it.path }, autoText) { ev -> apply(ev) }
                done = true
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                val held = if (secrets == null) chat.kelo.app.chat.SecretGuard.of(e) else null
                if (held != null) {   // nothing was sent to the model or stored: no bubble left behind, the text back where it was
                    _state.update { st ->
                        st.copy(
                            items = st.items.filterNot { it.key == bubble },
                            input = if (fromComposer) before.input else st.input,
                            attachments = if (tookAttachments) before.attachments + st.attachments else st.attachments,
                            secretAsk = SecretAsk(
                                held, text, answers, fromComposer, tookAttachments,
                                composer = if (fromComposer) before.input else "",
                                composerSpans = if (fromComposer) chat.kelo.app.chat.SecretGuard.composerSpans(before.input, held.spans) else emptyList(),
                            ),
                        )
                    }
                    return@launch
                }
                val consent = chat.kelo.app.chat.AiConsent.of(e)
                if (consent != null) {   // nobody agreed yet: nothing was stored or sent; the text goes back and the question opens
                    _state.update { st ->
                        st.copy(
                            items = st.items.filterNot { it.key == bubble },
                            input = if (fromComposer) before.input else st.input,
                            attachments = if (tookAttachments) before.attachments + st.attachments else st.attachments,
                            consentAsk = chat.kelo.app.chat.ConsentAsk(consent, text, answers, fromComposer, tookAttachments, secrets),
                            consentError = null,
                        )
                    }
                    return@launch
                }
                failed = true
                addError(
                    when {
                        e.isNetwork -> ChatError.Network
                        e.status == 400 -> ChatError.PickModel(e.detail)
                        else -> ChatError.Message(e.detail)
                    },
                )
            } catch (e: Exception) {
                failed = true
                addError(ChatError.Message(e.message ?: e.javaClass.simpleName))
            } finally {
                turn = null
                activity.turnLine.value = null
                activity.turnKind.value = TurnKind.Thinking
                activity.following.value = false
                stir()   // the reply's end starts the quiet time before the character may sleep
                if (done) activity.doneAt.value = System.nanoTime() / 1_000_000
                _state.update { it.copy(busy = false, phase = null, items = ChatReducer.endTurn(it.items), secrets = chat.kelo.app.chat.vault.SecretRules.settled(it.secrets)) }
                if (done || failed) finished.tryEmit(done)
                if (repliesCame) {
                    repliesCame = false
                    scope.launch { runCatching { reloadNow() } }
                } else {
                    remember()
                }
                refreshSecrets()
            }
        }
    }

    private fun remember() {
        scope.launch {
            runCatching {
                val api = api()
                val ms = api.get<List<StoredMessage>>("/api/messages?limit=$PAGE")
                lastId = ms.lastOrNull()?.id ?: 0
                sessions.keep(api, DesktopCaches.MESSAGES, ms)
            }
        }
    }

    // ---- the secret dialog (a message held back for a key or password; chat.kelo.app.chat.SecretGuard)
    /** The save step kept [value] in the Keychain: every composer span holding it becomes [reference]; the dialog closes. */
    fun secretSaved(value: String, reference: String) {
        val a = _state.value.secretAsk ?: return
        _state.update {
            val input = if (!a.fromComposer || it.input != a.composer) it.input
                else chat.kelo.app.chat.SecretGuard.replace(it.input, a.composerSpans, reference, value)
            it.copy(secretAsk = null, input = input)
        }
    }

    /** "Send as is": the very same text again, with "secrets": "allow" (only ever after this click). */
    fun secretSendAsIs() {
        val a = _state.value.secretAsk ?: return
        _state.update { it.copy(secretAsk = null) }
        if (_state.value.busy) return
        start(a.text, a.fromComposer, a.tookAttachments, a.answers, chat.kelo.app.chat.SecretGuard.ALLOW)
    }

    /** "Edit" (or the dialog closed): back to the composer with the text intact; a button's text is put there to be changed. */
    fun secretEdit() {
        val a = _state.value.secretAsk ?: return
        _state.update { it.copy(secretAsk = null, input = if (!a.fromComposer && it.input.isBlank()) a.text else it.input) }
    }

    // ---- asking once before chats go to the AI service (chat.kelo.app.chat.AiConsent)
    /** "Agree": the server keeps it (once, for every device), then the very same message goes. */
    fun consentAgree() {
        val a = _state.value.consentAsk ?: return
        if (_state.value.consentBusy) return
        _state.update { it.copy(consentBusy = true, consentError = null) }
        scope.launch {
            try {
                api().put<kotlinx.serialization.json.JsonElement>("/api/ai-consent", kotlinx.serialization.json.buildJsonObject {
                    put("accepted", kotlinx.serialization.json.JsonPrimitive(true))
                })
                _state.update { it.copy(consentAsk = null, consentBusy = false) }
                if (!_state.value.busy) start(a.text, a.fromComposer, a.tookAttachments, a.answers, a.secrets)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _state.update { it.copy(consentBusy = false, consentError = (e as? ApiError)?.detail ?: e.message ?: e.javaClass.simpleName) }
            }
        }
    }

    /** "Not now" (or the dialog closed): nothing is sent; the text stays in the composer (a button's text is put there). */
    fun consentNotNow() {
        val a = _state.value.consentAsk ?: return
        _state.update { it.copy(consentAsk = null, consentError = null, input = if (!a.fromComposer && it.input.isBlank()) a.text else it.input) }
    }

    fun retry(key: String) {
        if (_state.value.busy) return
        val text = MessageActions.retryText(_state.value.items, key) ?: return
        send(text)
    }

    fun stop() {
        turn?.cancel()
    }

    private fun addError(e: ChatError) {
        _state.update { it.copy(items = it.items + ChatItem.Error(key(), e)) }
    }

    fun showError(e: ChatError) = addError(e)

    /** Answers an approval card (POST /api/approvals/{id}); [rememberSchedule]: keep allowing it in that scheduled job. */
    fun approve(id: String, ok: Boolean, rememberSchedule: Boolean = false) {
        val open = _state.value.items.any { it is ChatItem.Approval && it.id == id && it.open }
        if (!open) return
        _state.update { s ->
            s.copy(items = ChatReducer.answer(s.items, id, ok), phase = if (s.busy) Phase(PhaseKind.WAITING, since = now()) else s.phase)
        }
        scope.launch {
            try {
                api().post<JsonElement>("/api/approvals/${KeloApi.segment(id)}", buildJsonObject {
                    put("approve", ok)
                    if (ok && rememberSchedule) put("remember", "schedule")
                })
            } catch (_: ApiError) {
            }
        }
    }

    fun pickEffort(value: String, onProfile: (Profile) -> Unit) {
        scope.launch {
            try {
                onProfile(api().put<Profile>("/api/profile", buildJsonObject { put("effort", value) }))
            } catch (e: ApiError) {
                addError(ChatError.Message(e.detail))
            }
        }
    }

    fun settleCard(key: String, state: chat.kelo.app.chat.CardState) = _state.update { it.copy(items = ChatReducer.settleCard(it.items, key, state)) }

    // ---- the in-chat address form (the agent's ask_shipping waits for this answer; android .../chat/ChatViewModel.kt does the same)
    /**
     * Sends the address of the form [formId] (POST /api/shipping-forms/{form_id}). 200: the form folds into one line (the nickname
     * it was saved under and the address on one line) and [onDone] gets the way to pay; 422: [onError] gets the server's words and
     * the form stays open; 404: it is gone (timed out, or the reply stopped) and shows as expired.
     */
    fun answerShipping(
        formId: String, form: chat.kelo.app.wallet.AddressForm, draft: chat.kelo.app.wallet.AddressDraft, save: Boolean, label: String,
        payment: String?, networkError: String, onError: (String) -> Unit, onDone: (String?) -> Unit,
    ) {
        val api = runCatching { api() }.getOrNull() ?: return onError(networkError)
        scope.launch {
            try {
                val answer = chat.kelo.app.wallet.ShippingCalls(api).answer(formId, chat.kelo.app.wallet.AddressRules.answerBody(form, draft, save, label, payment))
                val saved = answer.profile
                val line = saved?.oneLine?.ifBlank { null } ?: chat.kelo.app.wallet.AddressRules.oneLine(answer.formatted)
                val shownLabel = if (save) saved?.label?.ifBlank { null } ?: label.trim() else ""
                _state.update {
                    it.copy(
                        items = ChatReducer.settleShipping(it.items, formId, chat.kelo.app.chat.ShippingFormState.DONE, shownLabel, line),
                        shippingCard = if (payment == chat.kelo.app.wallet.ShippingAsk.PAY_CARD) it.shippingCard + formId else it.shippingCard,
                    )
                }
                onDone(payment)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                when {
                    e.status == 404 -> _state.update { it.copy(items = ChatReducer.settleShipping(it.items, formId, chat.kelo.app.chat.ShippingFormState.EXPIRED)) }
                    e.isNetwork -> onError(networkError)
                    else -> onError(e.detail)
                }
            }
        }
    }

    /** "Cancel" on the address form: the agent is told no address was given (a form already gone shows as expired). */
    fun cancelShipping(formId: String) {
        val api = runCatching { api() }.getOrNull() ?: return
        scope.launch {
            val state = try {
                chat.kelo.app.wallet.ShippingCalls(api).cancel(formId)
                chat.kelo.app.chat.ShippingFormState.CANCELLED
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status == 404) chat.kelo.app.chat.ShippingFormState.EXPIRED else return@launch addError(if (e.isNetwork) ChatError.Network else ChatError.Message(e.detail))
            }
            // (a CLOSED form, the tool's result having come back first, still takes CANCELLED: ChatReducer.settleShipping)
            _state.update { it.copy(items = ChatReducer.settleShipping(it.items, formId, state)) }
        }
    }

    // ---- attachments
    private val uploads = HashMap<String, Job>()

    /** Puts a chip in the tray at once and uploads the file in the background, read a chunk at a time (never all in memory). */
    fun attach(file: File) {
        val api = runCatching { api() }.getOrNull() ?: return
        val id = "a${++seq}"
        _state.update { it.copy(attachments = it.attachments + Attachment(id, file.name)) }
        uploads[id] = scope.launch {
            try {
                val length = withContext(Dispatchers.IO) { if (file.isFile && file.canRead()) file.length() else -1L }
                if (length < 0) throw java.io.IOException("unreadable")
                if (length > MAX_UPLOAD) { fail(id, AttachError.TooLarge); return@launch }
                change(id) { it.copy(total = length) }
                var shown = -1
                val type = runCatching { java.nio.file.Files.probeContentType(file.toPath()) }.getOrNull()
                val path = api.uploadStream(file.name, length, type, { file.inputStream() }) { sent, total ->
                    val pct = if (total > 0) (sent * 100 / total).toInt() else 0
                    if (pct != shown) { shown = pct; change(id) { it.copy(sent = sent, total = total) } }
                }
                val stored = path.removePrefix("uploads/").ifEmpty { file.name }
                change(id) { it.copy(state = AttachState.DONE, stored = stored, sent = it.total) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                fail(id, when {
                    e.isNetwork -> AttachError.Network
                    e.status == 413 -> AttachError.TooLarge
                    else -> AttachError.Server(e.detail)
                })
            } catch (e: Exception) {
                fail(id, AttachError.Unreadable)
            } finally {
                uploads.remove(id)
            }
        }
    }

    private fun change(id: String, f: (Attachment) -> Attachment) =
        _state.update { s -> s.copy(attachments = s.attachments.map { if (it.id == id) f(it) else it }) }

    private fun fail(id: String, e: AttachError) = change(id) { it.copy(state = AttachState.FAILED, error = e) }

    fun removeAttachment(id: String) {
        val a = _state.value.attachments.firstOrNull { it.id == id } ?: return
        uploads.remove(id)?.cancel()
        _state.update { s -> s.copy(attachments = s.attachments.filterNot { it.id == id }) }
        val stored = a.stored
        if (a.state == AttachState.DONE && stored != null) {
            scope.launch { runCatching { api().delete<JsonElement>("/api/uploads/${KeloApi.segment(stored)}") } }
        }
    }

    private fun now() = System.currentTimeMillis()

    private fun toItem(m: StoredMessage) =
        ChatItem.Msg("m${m.id}", mine = m.role == "user", text = m.content, id = m.id, replies = m.replies, at = (m.createdAt * 1000).toLong(),
            reaction = m.reaction, briefing = m.briefing, images = m.images, files = m.attachments, autoText = m.autoText)

    companion object {
        const val PAGE = 100
        const val DRAFT_DELAY_MS = 400L
        /** The server's limit: 100 MB a file (just under, as on the phone: Cloudflare's 100 MB body limit) */
        const val MAX_UPLOAD = 99_000_000L
    }
}
