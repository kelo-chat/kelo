package chat.kelo.app.agent

import androidx.annotation.StringRes
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import chat.kelo.app.R
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.core.Sessions
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import java.time.YearMonth
import kotlin.coroutines.cancellation.CancellationException

/** A short message for the snackbar: a string resource with its arguments, or the server's own words. */
data class AgentMessage(@StringRes val text: Int, val args: List<Any> = emptyList(), val detail: String? = null)

/** One list's loading state. */
data class Loadable<T>(val data: T? = null, val loading: Boolean = false, val failed: Boolean = false) {
    val loaded: Boolean get() = data != null
}

data class TaskList(val items: List<AgentTask> = emptyList(), val nextBefore: Long? = null, val loadingMore: Boolean = false)

/** An approval waiting right now (GET /api/approvals/pending): the chat's approval event, with what the list adds. */
data class PendingApproval(
    val event: TurnEvent.ApprovalRequest,
    val site: String,
    val canRemember: Boolean,
    val askedAt: Double,
    val unattended: Boolean,
    val taskId: Long?,
    /** "keep allowing this in that scheduled job" may be offered */
    val canRememberSchedule: Boolean = false,
    /** What asked: the run's task source (chat, worker, schedule:<id>, background…); "" from an older server */
    val source: String = "",
    /** The background job the asking run is a step of */
    val workerId: String? = null,
    /** That run's title (a job's own title) */
    val title: String = "",
) {
    val id: String get() = event.approvalId
    val isPayment: Boolean get() = event.payment != null
    /** A site's check whether a person is there, handed to the user: "solve it" opens [tab] live with control, "skip" answers no */
    val isHumanCheck: Boolean get() = event.kind == HUMAN_CHECK
    /** A human check that is a sign-in's second step (approve on the phone, or type the code), not a person check */
    val isVerification: Boolean get() = isHumanCheck && event.check?.isVerification == true
    /** The browser tab a human check is on */
    val tab: String get() = event.tab.ifBlank { "owner" }

    companion object {
        fun parse(o: JsonObject): PendingApproval? {
            val ev = TurnEvent.parse(o) as? TurnEvent.ApprovalRequest ?: return null
            if (ev.approvalId.isEmpty()) return null
            fun p(k: String) = o[k] as? JsonPrimitive
            return PendingApproval(
                ev, p("site")?.takeIf { it.isString }?.content.orEmpty(), p("can_remember")?.booleanOrNull == true,
                p("asked_at")?.doubleOrNull ?: 0.0, p("unattended")?.booleanOrNull == true, p("task_id")?.longOrNull,
                ev.payment == null && p("can_remember_schedule")?.booleanOrNull == true,
                p("source")?.takeIf { it.isString }?.content.orEmpty(),
                p("worker_id")?.takeIf { it.isString }?.content?.ifBlank { null },
                p("title")?.takeIf { it.isString }?.content.orEmpty(),
            )
        }

        fun parseList(e: JsonElement): List<PendingApproval> =
            ((e as? JsonObject)?.get("items")?.jsonArray).orEmpty().mapNotNull { (it as? JsonObject)?.let(::parse) }

        /** Hand-overs that ended lately (the list's "resolved"): approval id -> solved | skipped | expired | cancelled. */
        fun parseResolved(e: JsonElement): Map<String, String> =
            ((e as? JsonObject)?.get("resolved") as? kotlinx.serialization.json.JsonArray).orEmpty().mapNotNull { item ->
                val o = item as? JsonObject ?: return@mapNotNull null
                val id = (o["approval_id"] as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
                val out = (o["outcome"] as? JsonPrimitive)?.takeIf { it.isString }?.content.orEmpty()
                if (id.isEmpty() || out.isEmpty()) null else id to out
            }.toMap()

        const val HUMAN_CHECK = "human_check"
    }
}

data class Permissions(
    val pending: List<PendingApproval> = emptyList(),
    val rules: List<ApprovalRule> = emptyList(),
    val history: List<ApprovalRecord> = emptyList(),
    val historyNext: Double? = null,
    /** What the user allowed for a running task as a whole (one card for many steps) */
    val taskGrants: List<TaskPermission> = emptyList(),
    /** Approvals being answered right now */
    val answering: Set<String> = emptySet(),
)

data class Journal(val month: YearMonth, val entries: List<JournalEntry> = emptyList(), val gaps: List<JournalGap> = emptyList())

/**
 * The agent's work (tasks, background jobs, questions waiting, schedules, browser tabs) and its records (permission rules and
 * history, the journal, the nightly look-back). Each part loads when a page first shows it and refreshes when shown again; a
 * failed refresh keeps what is shown.
 */
class AgentViewModel(private val sessions: Sessions) : ViewModel() {
    private val _tasks = MutableStateFlow(Loadable<TaskList>())
    val tasks: StateFlow<Loadable<TaskList>> = _tasks.asStateFlow()
    private val _schedules = MutableStateFlow(Loadable<ScheduleList>())
    val schedules: StateFlow<Loadable<ScheduleList>> = _schedules.asStateFlow()
    private val _perms = MutableStateFlow(Loadable<Permissions>())
    val perms: StateFlow<Loadable<Permissions>> = _perms.asStateFlow()
    private val _browser = MutableStateFlow(Loadable<BrowserTabs>())
    val browser: StateFlow<Loadable<BrowserTabs>> = _browser.asStateFlow()
    private val _journal = MutableStateFlow(Loadable<Journal>())
    val journal: StateFlow<Loadable<Journal>> = _journal.asStateFlow()
    private val _month = MutableStateFlow(YearMonth.now())
    /** The month the 일지 tab shows */
    val month: StateFlow<YearMonth> = _month.asStateFlow()
    private val journalMonth: YearMonth get() = _month.value
    private val _followups = MutableStateFlow(Loadable<chat.kelo.app.followup.FollowupList>())
    /** Things the agent asks about again (GET /api/followups); the ones on hold go in 답이 필요해요 */
    val followups: StateFlow<Loadable<chat.kelo.app.followup.FollowupList>> = _followups.asStateFlow()
    private val _followupBusy = MutableStateFlow(setOf<String>())
    /** Follow-ups with a button being done right now */
    val followupBusy: StateFlow<Set<String>> = _followupBusy.asStateFlow()
    private val _lookback = MutableStateFlow(Loadable<Lookback>())
    /** The nightly look-back (shown in 일지) */
    val lookback: StateFlow<Loadable<Lookback>> = _lookback.asStateFlow()
    private val _skills = MutableStateFlow(Loadable<SkillProposals>())
    /** Custom skills waiting for the owner's approval (shown on the Records page, next to the learned rules) */
    val skillProposals: StateFlow<Loadable<SkillProposals>> = _skills.asStateFlow()
    private val _memoryChanges = MutableStateFlow(Loadable<MemoryChanges>())
    /** What the last memory pass changed (opened from its line on the Records page) */
    val memoryChanges: StateFlow<Loadable<MemoryChanges>> = _memoryChanges.asStateFlow()
    private val _memoryUndoing = MutableStateFlow(false)
    val memoryUndoing: StateFlow<Boolean> = _memoryUndoing.asStateFlow()
    private val _rebuilding = MutableStateFlow<Set<String>>(emptySet())
    val rebuilding: StateFlow<Set<String>> = _rebuilding.asStateFlow()

    private val _messages = MutableSharedFlow<AgentMessage>(extraBufferCapacity = 4)
    val messages: SharedFlow<AgentMessage> = _messages.asSharedFlow()

    private fun api(): KeloApi = sessions.requireApi()

    init {
        // 마이 → 대화 기록 삭제: the server deleted the records this screen may be holding
        viewModelScope.launch { sessions.historyCleared.drop(1).collect { forgetHistory() } }
    }

    /**
     * The chat history was cleared on the server: the task log, background jobs, approval history, follow-ups, day summaries and
     * ended browser tabs this screen holds go at once, and the lists that were loaded are asked for again. Rules, schedules and
     * watchers stay as they are (the clear keeps them).
     */
    internal fun forgetHistory() {
        val shown = listOf(_tasks.value.loaded, _workers.value.loaded, _followups.value.loaded, _perms.value.loaded, _browser.value.loaded)
        _tasks.update { l -> l.copy(data = l.data?.let { TaskList() }) }
        _workers.update { l -> l.copy(data = l.data?.let { WorkerList(running = 0, limit = it.limit) }) }
        _followups.update { l -> l.copy(data = l.data?.copy(items = emptyList())) }
        _perms.update { l -> l.copy(data = l.data?.copy(pending = emptyList(), history = emptyList(), historyNext = null, answering = emptySet())) }
        _browser.update { l -> l.copy(data = l.data?.let { d -> d.copy(items = d.items.filter { it.status != "ended" && !it.id.startsWith("task:") && !it.id.startsWith("schedule:") }) }) }   // runs' tabs are closed
        _journal.update { l -> l.copy(data = l.data?.copy(entries = emptyList(), gaps = emptyList())) }
        _week.update { l -> l.copy(data = l.data?.copy(entries = emptyList(), gaps = emptyList())) }
        if (shown[0]) loadTasks()
        if (shown[1]) loadWorkers()
        if (shown[2]) loadFollowups()
        if (shown[3]) loadPerms()
        if (shown[4]) loadBrowser()
        reloadJournal()
    }

    fun say(@StringRes text: Int, vararg args: Any) { _messages.tryEmit(AgentMessage(text, args.toList())) }
    private fun sayError(e: Throwable) {
        val detail = (e as? ApiError)?.takeIf { !it.isNetwork }?.detail
        _messages.tryEmit(AgentMessage(if (detail != null) R.string.agent_error_detail else R.string.connect_failed, detail = detail))
    }

    private val _workers = MutableStateFlow(Loadable<WorkerList>())
    /** Background jobs (GET /api/workers); an older server without them answers 404, read as none */
    val workers: StateFlow<Loadable<WorkerList>> = _workers.asStateFlow()

    /** Everything the 작업 timeline shows. */
    fun loadWork() {
        loadTasks()
        loadWorkers()
        loadWatchers()
        loadPerms()
        loadSchedules()
        loadBrowser()
        loadFollowups()
        loadEntries()
    }

    /** What changes while the agent works (the agenda and the rules change only when the user changes them). */
    fun refreshLive() {
        loadTasks()
        loadWorkers()
        loadWatchers()
        loadSchedules()
        loadPerms()
        loadBrowser()
        loadFollowups()
        loadEntries()
    }

    private val _entries = MutableStateFlow(Loadable<List<chat.kelo.app.chat.vault.SecretRequest>>())
    /** Secure entries waiting for a value (GET /api/secret-requests): rows in 답이 필요해요; an older server without them counts as none */
    val entries: StateFlow<Loadable<List<chat.kelo.app.chat.vault.SecretRequest>>> = _entries.asStateFlow()

    fun loadEntries() {
        refresh(_entries) {
            try { chat.kelo.app.chat.vault.SecretCalls.list(api()) } catch (e: ApiError) { if (e.status == 404) emptyList() else throw e }
        }
    }

    /** 그만두기 on an entry's row: the agent is told and does not ask again; the row and the badge go at once. */
    fun cancelEntry(r: chat.kelo.app.chat.vault.SecretRequest, onClosed: (String) -> Unit = {}) {
        _entries.update { l -> l.copy(data = l.data?.filterNot { it.id == r.id }) }
        onClosed(r.kind)
        viewModelScope.launch {
            try {
                chat.kelo.app.chat.vault.SecretCalls.cancel(api(), r.id)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                if (e.status != 404) sayError(e)
            } finally {
                loadEntries()
            }
        }
    }

    /** A server without follow-ups (404) counts as none. */
    fun loadFollowups() { refresh(_followups) { chat.kelo.app.followup.FollowupCalls(api()).list() } }

    /** One button of a follow-up card (다시 시작, 그만 묻기, …); the list loads again after it, and the jobs too when one went on. */
    fun runFollowup(f: chat.kelo.app.followup.Followup, step: chat.kelo.app.followup.FollowupStep) {
        if (f.id in _followupBusy.value || step is chat.kelo.app.followup.FollowupStep.OpenLogin) return
        _followupBusy.update { it + f.id }
        viewModelScope.launch {
            try {
                val (result, detail) = chat.kelo.app.followup.FollowupCalls(api()).run(step)
                when (result) {
                    chat.kelo.app.followup.StepResult.DONE -> say(when (step) {
                        is chat.kelo.app.followup.FollowupStep.Drop -> R.string.followup_done_dropped
                        is chat.kelo.app.followup.FollowupStep.Snooze -> R.string.followup_done_snoozed
                        is chat.kelo.app.followup.FollowupStep.Answer -> if (step.approve) R.string.followup_done_approved else R.string.followup_done_denied
                        else -> R.string.followup_done_resumed
                    })
                    chat.kelo.app.followup.StepResult.GONE -> {}   // already closed: the list refresh drops it
                    chat.kelo.app.followup.StepResult.FAILED -> if (detail.isBlank()) say(R.string.connect_failed) else _messages.tryEmit(AgentMessage(R.string.agent_error_detail, detail = detail))
                }
            } catch (e: ApiError) {
                sayError(e)
            } finally {
                _followupBusy.update { it - f.id }
                loadFollowups()
                if (step is chat.kelo.app.followup.FollowupStep.ResumeWorker || step is chat.kelo.app.followup.FollowupStep.Retry) { loadWorkers(); loadPerms() }
                if (step is chat.kelo.app.followup.FollowupStep.Answer) loadPerms()
            }
        }
    }

    private fun <T> refresh(flow: MutableStateFlow<Loadable<T>>, block: suspend () -> T): kotlinx.coroutines.Job? {
        if (flow.value.loading) return null
        flow.update { it.copy(loading = true) }
        return viewModelScope.launch {
            try {
                val v = block()
                flow.update { Loadable(v) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                flow.update { it.copy(loading = false, failed = true) }
            }
        }
    }

    // ---- tasks
    private var markedUpTo = 0L

    fun loadTasks() { refresh(_tasks) {
        val p = api().get<TaskPage>("/api/tasks?limit=$PAGE")
        TaskList(p.items, p.nextBefore)
    } }

    fun loadMoreTasks() {
        val cur = _tasks.value.data ?: return
        val before = cur.nextBefore ?: return
        if (cur.loadingMore) return
        _tasks.update { it.copy(data = cur.copy(loadingMore = true)) }
        viewModelScope.launch {
            try {
                val p = api().get<TaskPage>("/api/tasks?limit=$PAGE&before=$before")
                _tasks.update { l ->
                    val d = l.data ?: return@update l
                    val known = d.items.mapTo(HashSet()) { it.id }
                    l.copy(data = d.copy(items = d.items + p.items.filter { it.id !in known }, nextBefore = p.nextBefore, loadingMore = false))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _tasks.update { l -> l.copy(data = l.data?.copy(loadingMore = false)) }
            }
        }
    }

    /** The list was looked at: everything finished up to [upTo] counts as seen on the server (the dots stay for this visit). */
    fun markSeen(upTo: Long) {
        if (upTo <= markedUpTo) return
        markedUpTo = upTo
        viewModelScope.launch { runCatching { api().post<JsonElement>("/api/tasks/seen", buildJsonObject { put("up_to", upTo) }) } }
    }

    suspend fun task(id: Long): TaskDetail = api().get("/api/tasks/$id")

    fun stopTask(id: Long, then: () -> Unit) {
        viewModelScope.launch {
            try {
                api().post<JsonElement>("/api/tasks/$id/stop")
                say(R.string.agent_task_stopped)
            } catch (e: ApiError) {
                if (e.status == 404) say(R.string.agent_task_not_running) else sayError(e)
            }
            then()
            loadTasks()
        }
    }

    // ---- background jobs
    fun loadWorkers() { refresh(_workers) {
        try {
            api().get<WorkerList>("/api/workers?limit=$PAGE")
        } catch (e: ApiError) {
            if (e.status == 404) WorkerList() else throw e
        }
    } }

    suspend fun worker(id: String): WorkerDetail = api().get("/api/workers/${KeloApi.segment(id)}")

    fun stopWorker(id: String, then: () -> Unit = {}) = workerCall(id, "stop", null, R.string.work_job_stopped, then)

    fun resumeWorker(id: String, note: String = "", then: () -> Unit = {}) =
        workerCall(id, "resume", buildJsonObject { if (note.isNotBlank()) put("note", note.trim()) }, R.string.work_job_resumed, then)

    fun messageWorker(id: String, text: String, then: () -> Unit = {}) =
        workerCall(id, "message", buildJsonObject { put("text", text.trim()) }, R.string.work_job_message_sent, then)

    private fun workerCall(id: String, action: String, body: JsonObject?, @StringRes done: Int, then: () -> Unit) {
        viewModelScope.launch {
            try {
                api().post<JsonElement>("/api/workers/${KeloApi.segment(id)}/$action", body)
                say(done)
                then()
            } catch (e: ApiError) {
                when (e.status) {
                    404 -> say(R.string.work_job_gone)
                    409 -> say(R.string.work_job_busy)
                    429 -> say(R.string.work_job_too_many)
                    else -> sayError(e)
                }
            }
            loadWorkers()
            loadTasks()
        }
    }

    // ---- watchers
    private val _watchers = MutableStateFlow(Loadable<WatcherList>())
    /** The watchers (GET /api/watchers); an older server without them answers 404, read as none */
    val watchers: StateFlow<Loadable<WatcherList>> = _watchers.asStateFlow()

    fun loadWatchers() { refresh(_watchers) {
        try {
            api().get<WatcherList>("/api/watchers")
        } catch (e: ApiError) {
            if (e.status == 404) WatcherList() else throw e
        }
    } }

    suspend fun watcherEvents(id: Long): List<WatcherEvent> = api().get<WatcherEvents>("/api/watchers/$id/events").items

    private fun replaceWatcher(w: Watcher) =
        _watchers.update { l -> l.copy(data = l.data?.let { d -> d.copy(items = d.items.map { if (it.id == w.id) w else it }) }) }

    /** On or off; turning on needs a fresh successful check first (409: the server says why). */
    fun setWatcherEnabled(w: Watcher, on: Boolean) {
        replaceWatcher(w.copy(enabled = on))
        viewModelScope.launch {
            try {
                replaceWatcher(api().post<Watcher>("/api/watchers/${w.id}/enable", buildJsonObject { put("enabled", on) }))
            } catch (e: ApiError) {
                replaceWatcher(w)
                if (e.status == 409) say(R.string.work_watch_needs_check) else sayError(e)
            }
        }
    }

    /** Checks once now, without the agent; the answer replaces the watcher (its dry run and whether it may be turned on). */
    fun dryRunWatcher(w: Watcher, then: (DryRun?) -> Unit = {}) {
        viewModelScope.launch {
            try {
                val a = api().post<DryRunAnswer>("/api/watchers/${w.id}/dry-run")
                a.watcher?.let(::replaceWatcher)
                then(a.result)
            } catch (e: ApiError) {
                sayError(e)
                then(null)
            }
        }
    }

    fun deleteWatcher(w: Watcher, then: () -> Unit) {
        viewModelScope.launch {
            try {
                api().delete<JsonElement>("/api/watchers/${w.id}")
                say(R.string.work_watch_deleted)
                then()
                loadWatchers()
            } catch (e: ApiError) {
                if (e.status == 404) { then(); loadWatchers() } else sayError(e)
            }
        }
    }

    // ---- schedules
    fun loadSchedules() { refresh(_schedules) { api().get<ScheduleList>("/api/schedules") } }

    fun setEnabled(s: Schedule, on: Boolean) {
        _schedules.update { l -> l.copy(data = l.data?.let { d -> d.copy(items = d.items.map { if (it.id == s.id) it.copy(enabled = on) else it }) }) }
        viewModelScope.launch {
            try {
                val saved = api().patch<Schedule>("/api/schedules/${s.id}", buildJsonObject { put("enabled", on) })
                replaceSchedule(saved)
            } catch (e: Exception) {
                if (e is CancellationException) throw e
                replaceSchedule(s)   // back as it was
                sayError(e)
            }
        }
    }

    private fun replaceSchedule(s: Schedule) =
        _schedules.update { l -> l.copy(data = l.data?.let { d -> d.copy(items = d.items.map { if (it.id == s.id) s else it }) }) }

    /**
     * Saves a new schedule (id null) or changes one. [plan] is the simple form's choice (null keeps a saved schedule's time as it is:
     * only the name and what to do change). A repeat goes as cron (+ starts_on / ends_on), a one-time run as run_at; the start-time
     * spread ("window") is never offered, so it is always 0. [onSaved] runs once the server took it; a refusal says why.
     */
    fun saveSchedule(
        id: Long?, name: String, prompt: String, plan: SchedulePlan?, timezone: String?, onFailed: () -> Unit = {},
        now: java.time.LocalDateTime = java.time.LocalDateTime.now(), onSaved: (Schedule) -> Unit,
    ) {
        val body = buildJsonObject {
            put("name", name.trim())
            put("prompt", prompt.trim())
            val none = kotlinx.serialization.json.JsonNull
            if (plan != null) {
                val cron = PlanRules.cron(plan)
                if (cron != null) {
                    put("cron", cron)
                    put("window_minutes", 0)
                    put("starts_on", PlanRules.startsOn(plan, now))
                    put("ends_on", PlanRules.endsOn(plan))
                    if (id != null) { put("run_at", none); put("every_minutes", none) }
                } else {
                    put("run_at", PlanRules.runAt(plan))
                    if (id != null) { put("cron", none); put("every_minutes", none) }
                }
            }
            if (!timezone.isNullOrBlank()) put("timezone", timezone)
        }
        viewModelScope.launch {
            try {
                val s = if (id == null) api().post<Schedule>("/api/schedules", body) else api().patch<Schedule>("/api/schedules/$id", body)
                say(R.string.agent_schedule_saved)
                onSaved(s)
                loadSchedules()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                onFailed()
                sayError(e)
            }
        }
    }

    /** Back on after a pause (422: a one-time job whose time has passed; the server says so). */
    fun resumeSchedule(id: Long) {
        viewModelScope.launch {
            try {
                replaceSchedule(api().post<Schedule>("/api/schedules/$id/resume"))
                say(R.string.work_schedule_resumed)
            } catch (e: ApiError) {
                sayError(e)
            }
            loadSchedules()
        }
    }

    suspend fun scheduleRuns(id: Long): List<ScheduleRun> = api().get<ScheduleRuns>("/api/schedules/$id/runs?limit=20").items

    /** What a schedule may do without asking (allowed once at setup), and taking one back. */
    suspend fun scheduleGrants(id: Long): List<ScheduleGrant> = ScheduleGrantCalls.list(api(), id)
    suspend fun revokeScheduleGrant(g: ScheduleGrant) = ScheduleGrantCalls.revoke(api(), g)
    /** What a watcher's / a job's task may do without asking (allowed once at setup) */
    suspend fun watcherGrants(id: Long): List<ScheduleGrant> = ScheduleGrantCalls.forWatcher(api(), id)
    suspend fun jobGrants(id: String): List<ScheduleGrant> = ScheduleGrantCalls.forJob(api(), id)

    private val _archive = MutableStateFlow(Loadable<ScheduleArchive>())
    /** Schedules put away (DELETE archives them for a while) */
    val archive: StateFlow<Loadable<ScheduleArchive>> = _archive.asStateFlow()

    fun loadArchive() { refresh(_archive) {
        val a = try {
            api().get<ScheduleArchive>("/api/schedules/archive")
        } catch (e: ApiError) {
            if (e.status == 404) ScheduleArchive() else throw e
        }
        // one swiped away whose delete is still waiting for the undo message stays hidden
        val waiting = synchronized(pendingErase) { pendingErase.keys + erasing }
        if (waiting.isEmpty()) a else a.copy(items = a.items.filterNot { it.id in waiting })
    } }

    fun restoreSchedule(id: Long) {
        viewModelScope.launch {
            try {
                api().post<Schedule>("/api/schedules/$id/restore")
                say(R.string.work_schedule_restored)
            } catch (e: ApiError) {
                sayError(e)
            }
            loadArchive()
            loadSchedules()
        }
    }

    fun runNow(id: Long) {
        viewModelScope.launch {
            try {
                api().post<JsonElement>("/api/schedules/$id/run")
                say(R.string.agent_schedule_started)
                loadSchedules()
            } catch (e: ApiError) {
                if (e.status == 409) say(R.string.agent_schedule_already_running) else sayError(e)
            }
        }
    }

    /** The detail page's 삭제 dialog: into the archive (restorable for a while), or [forever]: gone at once. */
    fun deleteSchedule(id: Long, then: () -> Unit, forever: Boolean = false) {
        viewModelScope.launch {
            try {
                api().delete<JsonElement>("/api/schedules/$id" + if (forever) "?forever=true" else "")
                say(if (forever) R.string.schedule_deleted_forever else R.string.agent_schedule_deleted)
                then()
                loadSchedules()
                loadArchive()
            } catch (e: ApiError) {
                sayError(e)
            }
        }
    }

    /** A swipe on the schedules list: into the archive at once (the snackbar's 실행 취소 puts it back: [restoreSchedule]). */
    fun archiveSchedule(id: Long) {
        _schedules.update { l -> l.copy(data = l.data?.let { d -> d.copy(items = d.items.filterNot { it.id == id }) }) }
        viewModelScope.launch {
            try { api().delete<JsonElement>("/api/schedules/$id") } catch (e: ApiError) { sayError(e) }
            loadSchedules()
            loadArchive()
        }
    }

    /**
     * Deletes waiting for the undo message of a swipe in 보관한 예약, by schedule id. They run in [viewModelScope], not in the
     * page's scope, so leaving the page never cancels one: [commitArchivedErases] (called when the page closes) sends them at once,
     * and a delete nobody committed still goes out after [ERASE_GRACE_MS].
     */
    private val pendingErase = LinkedHashMap<Long, kotlinx.coroutines.Job>()

    /** Deletes on their way to the server (guarded by [pendingErase]): a list loaded meanwhile must not show them again. */
    private val erasing = mutableSetOf<Long>()

    /** A swipe in 보관한 예약: hidden at once and deleted for good unless [undoEraseArchived] comes first. */
    fun eraseArchivedSoon(id: Long) {
        _archive.update { l -> l.copy(data = l.data?.let { d -> d.copy(items = d.items.filterNot { it.id == id }) }) }
        val job = viewModelScope.launch(start = kotlinx.coroutines.CoroutineStart.LAZY) {
            delay(ERASE_GRACE_MS)
            eraseNow(id)
        }
        synchronized(pendingErase) { pendingErase.put(id, job) }?.cancel()
        job.start()
    }

    /** 실행 취소 on the message: the delete is dropped and the schedule shows again. */
    fun undoEraseArchived(id: Long) {
        val job = synchronized(pendingErase) { pendingErase.remove(id) } ?: return
        job.cancel()
        loadArchive()
    }

    /** The message ended without 실행 취소: delete [id] now. */
    fun eraseArchived(id: Long) {
        val job = synchronized(pendingErase) { pendingErase.remove(id) }
        job?.cancel()
        viewModelScope.launch { eraseNow(id) }
    }

    /** The page is closing (back, another tab, the app going away): every waiting delete is sent now. */
    fun commitArchivedErases() {
        val ids = synchronized(pendingErase) { pendingErase.keys.toList() }
        ids.forEach { eraseArchived(it) }
    }

    private suspend fun eraseNow(id: Long) {
        synchronized(pendingErase) { pendingErase.remove(id); erasing += id }
        try {
            api().delete<JsonElement>("/api/schedules/archive/$id")
        } catch (e: ApiError) {
            if (e.status != 404) sayError(e)
        } finally {
            synchronized(pendingErase) { erasing -= id }
        }
        loadArchive()
    }

    // ---- approvals and permission rules
    fun loadPerms() { refresh(_perms) {
        val api = api()
        val pending = PendingApproval.parseList(api.request("GET", "/api/approvals/pending"))
        val rules = api.get<RuleList>("/api/approvals/rules").items
        val history = api.get<ApprovalHistory>("/api/approvals/history?limit=$PAGE")
        val grants = try { api.get<TaskPermissionList>("/api/task-permissions").items } catch (e: ApiError) { if (e.status == 404) emptyList() else throw e }
        Permissions(pending, rules, history.items, history.nextBefore, taskGrants = grants)
    } }

    fun loadMoreHistory() {
        val cur = _perms.value.data ?: return
        val before = cur.historyNext ?: return
        viewModelScope.launch {
            runCatching { api().get<ApprovalHistory>("/api/approvals/history?limit=$PAGE&before=$before") }.onSuccess { h ->
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
        rememberSchedule: Boolean = false,
    ) {
        val cur = _perms.value.data ?: return
        if (a.id in cur.answering) return
        _perms.update { l -> l.copy(data = l.data?.copy(answering = l.data.answering + a.id)) }
        viewModelScope.launch {
            try {
                if (approve && beforeAllow != null && !beforeAllow()) return@launch
                api().post<JsonElement>("/api/approvals/${KeloApi.segment(a.id)}", buildJsonObject {
                    put("approve", approve)
                    if (approve && remember && a.canRemember) put("remember", "site")
                    else if (approve && rememberSchedule && a.canRememberSchedule) put("remember", "schedule")
                })
                if (approve) chat.kelo.app.device.DeviceTasks.afterApproval()   // a text, call or calendar change may now wait for this phone
                say(when {
                    !approve -> R.string.agent_approval_denied
                    remember && a.canRemember -> R.string.agent_approval_always
                    rememberSchedule && a.canRememberSchedule -> R.string.approval_kept_for_schedule
                    else -> R.string.agent_approval_allowed
                })
            } catch (e: ApiError) {
                if (e.status == 404) say(R.string.agent_approval_gone) else sayError(e)
            } finally {
                _perms.update { l -> l.copy(data = l.data?.copy(answering = l.data.answering - a.id)) }
                _perms.update { it.copy(loading = false) }
                loadPerms()
            }
        }
    }

    fun revokeRule(r: ApprovalRule) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(rules = d.rules.filter { it.id != r.id }) }) }
        viewModelScope.launch {
            try {
                api().delete<JsonElement>("/api/approvals/rules/${r.id}")
                say(R.string.agent_rule_revoked, r.site)
            } catch (e: ApiError) {
                if (e.status != 404) { sayError(e); loadPerms() }
            }
        }
    }

    /** Stops a task's permission at once: its next such step asks again. */
    fun revokeTaskPermission(g: TaskPermission) {
        _perms.update { l -> l.copy(data = l.data?.let { d -> d.copy(taskGrants = d.taskGrants.filter { it.id != g.id }) }) }
        viewModelScope.launch {
            try {
                api().delete<JsonElement>("/api/task-permissions/${KeloApi.segment(g.id)}")
                say(R.string.work_grant_revoked)
            } catch (e: ApiError) {
                if (e.status != 404) { sayError(e); loadPerms() }
            }
        }
    }

    // ---- browser tabs
    fun loadBrowser() { refresh(_browser) { api().get<BrowserTabs>("/api/browser/sessions?thumbnails=true") } }

    suspend fun screenshot(tabId: String): ByteArray = api().download("/api/browser/sessions/${KeloApi.segment(tabId)}/screenshot")

    fun closeTab(t: BrowserTab, then: () -> Unit) {
        viewModelScope.launch {
            try {
                api().delete<JsonElement>("/api/browser/sessions/${KeloApi.segment(t.id)}")
                say(R.string.agent_tab_closed)
                then()
                loadBrowser()
            } catch (e: ApiError) {
                when (e.status) {
                    409 -> say(R.string.agent_tab_busy)
                    404 -> { then(); loadBrowser() }
                    else -> sayError(e)
                }
            }
        }
    }

    fun startBrowserJob(prompt: String, url: String, onStarted: () -> Unit) {
        viewModelScope.launch {
            try {
                api().post<BrowserJobStarted>("/api/browser/sessions", buildJsonObject {
                    put("prompt", prompt.trim())
                    if (url.isNotBlank()) put("url", url.trim())
                })
                say(R.string.agent_browser_started)
                onStarted()
                delay(800)   // the tab opens on the task's first step
                loadBrowser()
                loadTasks()
            } catch (e: ApiError) {
                if (e.status == 404) say(R.string.agent_browser_none) else sayError(e)
            }
        }
    }

    // ---- journal
    private var journalJob: kotlinx.coroutines.Job? = null

    fun loadJournal(month: YearMonth) {
        if (month != journalMonth) {
            _month.value = month
            journalJob?.cancel()
            _journal.value = Loadable()
        }
        journalJob = refresh(_journal) {
            val m = api().get<JournalMonth>("/api/journal?month=$month")
            Journal(month, m.items, m.pending)
        }
    }

    private val _week = MutableStateFlow(Loadable<WeekDigest>())
    /** The week the 기록 page shows: its journal entries and the days still waiting to be written */
    val week: StateFlow<Loadable<WeekDigest>> = _week.asStateFlow()
    private var weekJob: kotlinx.coroutines.Job? = null

    /** The week starting [start] (a Monday): one or two months of the journal, cut to the week. */
    fun loadWeek(start: java.time.LocalDate) {
        if (_week.value.data?.start != start) {
            weekJob?.cancel()
            _week.value = Loadable(loading = false)
        }
        weekJob = refresh(_week) {
            val months = DigestRules.months(start).map { m -> api().get<JournalMonth>("/api/journal?month=$m") }
            DigestRules.week(start, months.flatMap { it.items }, months.flatMap { it.pending })
        }
    }

    private fun reloadJournal() {
        if (_journal.value.data != null) loadJournal(journalMonth)
        _week.value.data?.let { _week.update { l -> l.copy(loading = false) }; loadWeek(it.start) }
    }

    fun deleteEntry(e: JournalEntry) {
        _journal.update { l -> l.copy(data = l.data?.let { d -> d.copy(entries = d.entries.filter { it.id != e.id }) }) }
        _week.update { l -> l.copy(data = l.data?.let { d -> d.copy(entries = d.entries.filter { it.id != e.id }) }) }
        viewModelScope.launch {
            try {
                api().delete<JsonElement>("/api/journal/${e.id}")
                say(R.string.agent_journal_deleted)
            } catch (ex: ApiError) {
                if (ex.status != 404) { sayError(ex); reloadJournal() }
            }
        }
    }

    /** POST /api/journal/rebuild?date=: writes that day's entries again with the main model. */
    fun rebuild(date: String) {
        if (date in _rebuilding.value) return
        _rebuilding.update { it + date }
        viewModelScope.launch {
            try {
                api().post<JsonElement>("/api/journal/rebuild?date=$date")
                say(R.string.agent_journal_rebuilt)
            } catch (e: ApiError) {
                if (e.status == 409) _messages.tryEmit(AgentMessage(R.string.agent_journal_openai)) else sayError(e)
            } finally {
                _rebuilding.update { it - date }
                _journal.update { it.copy(loading = false) }
                reloadJournal()
            }
        }
    }

    // ---- the nightly look-back (GET/PUT /api/reflection)
    fun loadLookback() { refresh(_lookback) {
        try {
            api().get<Lookback>("/api/reflection")
        } catch (e: ApiError) {
            if (e.status == 404) Lookback(available = false) else throw e
        }
    } }

    // ---- the memory pass (GET /api/reflection/memory, POST /api/reflection/memory/undo)
    fun loadMemoryChanges() {
        _memoryChanges.value = Loadable()
        refresh(_memoryChanges) { api().get<MemoryChanges>("/api/reflection/memory") }
    }

    /** Puts the memory files back as they were before the last pass, then reloads the page's line. */
    fun undoMemoryPass(onDone: () -> Unit = {}) {
        if (_memoryUndoing.value) return
        _memoryUndoing.value = true
        viewModelScope.launch {
            try {
                api().post<JsonElement>("/api/reflection/memory/undo")
                say(R.string.memory_pass_undone)
                onDone()
            } catch (e: ApiError) {
                if (e.status == 404) { say(R.string.memory_pass_undo_gone); onDone() } else sayError(e)
            } finally {
                _memoryUndoing.value = false
                loadLookback()
            }
        }
    }

    /** The switch: shown at once, put back if the server says no. */
    fun setLookback(on: Boolean) {
        val before = _lookback.value.data ?: return
        _lookback.update { it.copy(data = before.copy(enabled = on)) }
        viewModelScope.launch {
            try {
                val after = api().put<Lookback>("/api/reflection/enabled", buildJsonObject { put("enabled", on) })
                _lookback.update { Loadable(after) }
            } catch (e: ApiError) {
                _lookback.update { it.copy(data = before) }
                sayError(e)
            }
        }
    }

    /**
     * 맞아요 / 아니에요 on a learned rule (also 그만 지키기 and 다시 지키기): shown at once, put back if the server says no. A rule the
     * server no longer has (404) makes the list load again.
     */
    fun setRuleStatus(id: Long, status: String) {
        val before = _lookback.value.data ?: return
        _lookback.update { it.copy(data = before.copy(rules = LookbackRules.withStatus(before.rules, id, status))) }
        viewModelScope.launch {
            try {
                val rule = api().patch<LearnedRule>("/api/reflection/rules/$id", buildJsonObject { put("status", status) })
                _lookback.update { l -> l.copy(data = l.data?.let { d -> d.copy(rules = LookbackRules.replace(d.rules, rule)) }) }
            } catch (e: ApiError) {
                _lookback.update { it.copy(data = before) }
                if (e.status == 404) loadLookback() else sayError(e)
            }
        }
    }

    // ---- custom skills waiting for approval (GET /api/skills/proposals) and "Save as skill" on a finished task or job
    fun loadSkillProposals() {
        val job = refresh(_skills) {
            try {
                api().get<SkillProposals>("/api/skills/proposals")
            } catch (e: ApiError) {
                if (e.status == 404) SkillProposals(available = false) else throw e
            }
        }
        // while a draft is being written, look again now and then, so the draft and the button's state show up without a reload
        if (job != null && skillPoll?.isActive != true) {
            skillPoll = viewModelScope.launch {
                job.join()
                repeat(SkillRules.POLL_TIMES) {
                    if (_skills.value.data?.drafting.isNullOrEmpty()) return@launch
                    delay(SkillRules.POLL_MILLIS)
                    refresh(_skills) { api().get<SkillProposals>("/api/skills/proposals") }?.join()
                }
            }
        }
    }
    private var skillPoll: kotlinx.coroutines.Job? = null

    /**
     * Approve (written as shown and used from now on) or discard a proposal; an edited skill is approved only as the owner saw it
     * (its hash goes along). [done] runs with true on success; on failure the list loads again and the reason is shown.
     */
    fun answerSkill(p: SkillProposal, approve: Boolean, done: (Boolean) -> Unit) {
        viewModelScope.launch {
            try {
                if (approve) {
                    api().post<JsonElement>(SkillRules.approvePath(p), p.contentHash?.let { h -> buildJsonObject { put("expected_hash", h) } })
                } else {
                    api().post<JsonElement>(SkillRules.discardPath(p))
                }
                done(true)
                delay(SkillRules.DONE_MILLIS)
                _skills.update { l -> l.copy(data = l.data?.let { SkillRules.without(it, p.id) }) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                done(false)
                if (e.status != 404) sayError(e)
                loadSkillProposals()
            }
        }
    }

    /**
     * Ask Kelo to draft a skill from a finished chat task or background job; the draft waits for approval on the Records page.
     * [where]: the names of the tab and page it will be on (마이, 기록), as the app shows them.
     */
    fun saveAsSkill(taskId: Long? = null, workerId: String? = null, where: List<String> = emptyList()) {
        viewModelScope.launch {
            try {
                api().post<JsonElement>(SkillRules.draftPath(taskId, workerId))
                say(R.string.skill_save_started, *where.toTypedArray())
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiError) {
                when (e.status) {
                    409 -> say(R.string.skill_save_busy)
                    404 -> say(R.string.skill_save_unavailable)
                    503 -> say(R.string.skill_save_no_model)
                    else -> sayError(e)
                }
            }
            loadSkillProposals()
        }
    }

    companion object {
        const val PAGE = 50
        /** How long a swiped-away archived schedule waits for 실행 취소 when the page never says (a little over the short message) */
        const val ERASE_GRACE_MS = 6_000L
    }
}
