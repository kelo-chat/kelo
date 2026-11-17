package chat.kelo.app.agent

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*


import chat.kelo.app.approval.ApprovalSignErrors
import chat.kelo.app.approval.ApprovalSignOutcome
import chat.kelo.app.approval.signPaymentAllow
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Approvals and permission rules: waiting approvals, the history, answering one, revoking a rule or a task's permission. */
class AgentPermissions(private val host: AgentHost) {
    private val _perms = MutableStateFlow(Loadable<Permissions>())
    val perms: StateFlow<Loadable<Permissions>> = _perms.asStateFlow()

    fun loadPerms() { host.refresh(_perms) {
        val api = host.api()
        val pending = PendingApproval.parseList(api.request("GET", "/api/approvals/pending"))
        val rules = api.get<RuleList>("/api/approvals/rules").items
        val history = api.get<ApprovalHistory>("/api/approvals/history?limit=${AgentViewModel.PAGE}")
        val grants = try { api.get<TaskPermissionList>("/api/task-permissions").items } catch (e: ApiError) { if (e.status == 404) emptyList() else throw e }
        val recipients = try { RecipientRules.ordered(api.get<RecipientAllowList>("/api/permissions/recipients").items) } catch (e: ApiError) { if (e.status == 404) emptyList() else throw e }
        Permissions(pending, rules, history.items, history.nextBefore, taskGrants = grants, recipients = recipients, cover = loadCover(api))
    } }

    /** The payment cover; a server without it (404) has none, and the section stays hidden. */
    private suspend fun loadCover(api: KeloApi): PaymentCover? = try {
        val orders = api.get<PreapprovalList>("/api/permissions/preapprovals")
        val allows = api.get<PayAllowList>("/api/permissions/pay-allows").items
        val switch = api.get<PaymentCoverSwitch>("/api/permissions/payment-cover").enabled
        PaymentCover(switch, PaymentCoverRules.ordered(orders.items), allows)
    } catch (e: ApiError) { if (e.status == 404) null else throw e }

    private val _past = MutableStateFlow(Loadable<PastApprovals>())
    /** The Past approvals page's list (GET /api/approvals/history, newest first, paged by the last row's asked_at) */
    val past: StateFlow<Loadable<PastApprovals>> = _past.asStateFlow()

    fun loadPast() { host.refresh(_past) {
        val h = host.api().get<ApprovalHistory>("/api/approvals/history?limit=${AgentViewModel.PAGE}")
        PastApprovals(h.items, h.nextBefore)
    } }

    fun loadMorePast() {
        val cur = _past.value.data ?: return
        val before = cur.next ?: return
        if (cur.loadingMore) return
        _past.update { l -> l.copy(data = l.data?.copy(loadingMore = true, moreFailed = false)) }
        host.scope.launch {
            runCatching { host.api().get<ApprovalHistory>("/api/approvals/history?limit=${AgentViewModel.PAGE}&before=$before") }
                .onSuccess { h -> _past.update { l -> l.copy(data = l.data?.let { PastApprovalsRules.append(it, h) }) } }
                .onFailure { _past.update { l -> l.copy(data = l.data?.copy(loadingMore = false, moreFailed = true)) } }
        }
    }

    fun loadMoreHistory() {
        val cur = _perms.value.data ?: return
        val before = cur.historyNext ?: return
        host.scope.launch {
            runCatching { host.api().get<ApprovalHistory>("/api/approvals/history?limit=${AgentViewModel.PAGE}&before=$before") }.onSuccess { h ->
                _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(history = d.history + h.items, historyNext = h.nextBefore) }) }
            }
        }
    }

    /**
     * Answers a waiting approval ([remember]: allow this kind of step on this site from now on). [beforeAllow] runs first on Allow
     * (a payment with an end-to-end card sends the card from this phone); when it fails the question stays open.
     */
    fun answer(
        a: PendingApproval, approve: Boolean, remember: Boolean = false, beforeAllow: (suspend () -> Boolean)? = null,
        rememberSchedule: Boolean = false, rememberRecipient: Boolean = false,
    ) {
        val cur = _perms.value.data ?: return
        if (a.id in cur.answering) return
        _perms.update { l -> l.copy(data = l.data?.copy(answering = l.data.answering + a.id)) }
        host.scope.launch {
            try {
                if (approve && beforeAllow != null && !beforeAllow()) return@launch
                // a signed payment Allow: the owner's check and this phone's signature first (a cancelled one sends nothing)
                val sig = if (approve && a.event.signable) when (val signed = signPaymentAllow(host.platform.approvalSigner, a.id, a.event.issuedAt)) {
                    is ApprovalSignOutcome.Ready -> signed.sig
                    ApprovalSignOutcome.Cancelled -> return@launch
                    is ApprovalSignOutcome.Failed -> { host.sayChatError(signed.error); return@launch }
                } else null
                host.api().post<JsonElement>("/api/approvals/${KeloApi.segment(a.id)}", buildJsonObject {
                    put("approve", approve)
                    sig?.let { put("sig", it) }
                    if (approve && rememberRecipient && RecipientRules.canAlways(a.event.offers)) put("remember", "recipient")
                    else if (approve && remember && a.canRemember) put("remember", "site")
                    else if (approve && rememberSchedule && a.canRememberSchedule) put("remember", "schedule")
                    if (approve) ApprovalDrafts.edited(a.id, a.event.editable)?.let { put("edited", it) }   // the message the owner fixed in the card
                })
                ApprovalDrafts.clear(a.id)
                host.platform.itemSettled(a.id)
                if (approve) host.platform.afterApproval()   // a text, call or calendar change may now wait for this phone
                host.say(when {
                    !approve -> Res.string.agent_approval_denied
                    rememberRecipient && RecipientRules.canAlways(a.event.offers) -> Res.string.recipient_approval_always
                    remember && a.canRemember -> Res.string.agent_approval_always
                    rememberSchedule && a.canRememberSchedule -> Res.string.approval_kept_for_schedule
                    else -> Res.string.agent_approval_allowed
                })
            } catch (e: ApiError) {
                when {
                    e.status == 404 -> host.say(Res.string.agent_approval_gone)
                    ApprovalSignErrors.isSignature(e) -> host.sayChatError(ApprovalSignErrors.error(e, host.platform.approvalSigner.available))
                    else -> host.sayError(e)
                }
            } finally {
                _perms.update { l -> l.copy(data = l.data?.copy(answering = l.data.answering - a.id)) }
                _perms.update { it.copy(loading = false) }
                loadPerms()
            }
        }
    }

    fun revokeRule(r: ApprovalRule) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(rules = d.rules.filter { it.id != r.id }) }) }
        host.scope.launch {
            try {
                host.api().delete<JsonElement>("/api/approvals/rules/${r.id}")
                host.say(Res.string.agent_rule_revoked, r.site)
            } catch (e: ApiError) {
                if (e.status != 404) { host.sayError(e); loadPerms() }
            }
        }
    }

    /** Takes back an "always for this recipient": the next such send asks again. */
    fun removeRecipient(r: RecipientAllow) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(recipients = RecipientRules.without(d.recipients, r.id)) }) }
        host.scope.launch {
            try {
                host.api().delete<JsonElement>("/api/permissions/recipients/${r.id}")
                host.say(Res.string.recipient_allow_removed)
            } catch (e: ApiError) {
                if (e.status != 404) { host.sayError(e); loadPerms() }
            }
        }
    }

    /** Takes back one order approved in advance: it asks when it is paid. 404: it is already gone, the list loads again. */
    fun removePreapproval(p: Preapproval) = removeCover(
        "/api/permissions/preapprovals/${KeloApi.segment(p.id)}", { PaymentCoverRules.withoutOrder(it, p.id) },
    )

    /** Takes back a schedule's allow for its payments (same shop, same payment method): the next one asks. */
    fun removePayAllow(a: PayAllow) = removeCover(
        "/api/permissions/pay-allows/${KeloApi.segment(a.id)}", { PaymentCoverRules.withoutAllow(it, a.id) },
    )

    private fun removeCover(path: String, without: (PaymentCover) -> PaymentCover) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(cover = d.cover?.let(without)) }) }
        host.scope.launch {
            try {
                host.api().delete<JsonElement>(path)
                host.say(Res.string.paycover_removed)
            } catch (e: ApiError) {
                if (e.status != 404) host.sayError(e)   // 404: it is already gone
                loadPerms()
            }
        }
    }

    /** The switch "Allow payments approved in advance": shown at once, put back if the server says no. */
    fun setPaymentCover(on: Boolean) {
        val before = _perms.value.data?.cover?.enabled ?: return
        setSwitch(on)
        host.scope.launch {
            try {
                val now = host.api().put<PaymentCoverSwitch>("/api/permissions/payment-cover", buildJsonObject { put("enabled", on) }).enabled
                setSwitch(now)
            } catch (e: ApiError) {
                setSwitch(before)
                host.sayError(e)
            }
        }
    }

    private fun setSwitch(on: Boolean) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(cover = d.cover?.copy(enabled = on)) }) }
    }

    /** Stops a task's permission at once: its next such step asks again. */
    fun revokeTaskPermission(g: TaskPermission) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(taskGrants = d.taskGrants.filter { it.id != g.id }) }) }
        host.scope.launch {
            try {
                host.api().delete<JsonElement>("/api/task-permissions/${KeloApi.segment(g.id)}")
                host.say(Res.string.work_grant_revoked)
            } catch (e: ApiError) {
                if (e.status != 404) { host.sayError(e); loadPerms() }
            }
        }
    }

    /** The chat history was cleared on the server: the approval history and waiting approvals go at once. */
    fun forgetHistory() {
        _perms.update { l -> l.copy(data = l.data?.copy(pending = emptyList(), history = emptyList(), historyNext = null, answering = emptySet())) }
    }
}
