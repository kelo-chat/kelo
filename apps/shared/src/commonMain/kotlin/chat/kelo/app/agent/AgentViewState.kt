package chat.kelo.app.agent

import chat.kelo.app.res.*
import chat.kelo.app.ui.text.*


import org.jetbrains.compose.resources.StringResource
import java.time.YearMonth

/** A short message for the snackbar: a string resource with its arguments, or the server's own words. */
data class AgentMessage(val text: StringResource, val args: List<Any> = emptyList(), val detail: String? = null)

/** One list's loading state. */
data class Loadable<T>(val data: T? = null, val loading: Boolean = false, val failed: Boolean = false) {
    val loaded: Boolean get() = data != null
}

data class TaskList(val items: List<AgentTask> = emptyList(), val nextBefore: Long? = null, val loadingMore: Boolean = false)

data class Permissions(
    val pending: List<PendingApproval> = emptyList(),
    val rules: List<ApprovalRule> = emptyList(),
    val history: List<ApprovalRecord> = emptyList(),
    val historyNext: Double? = null,
    /** What the user allowed for a running task as a whole (one card for many steps) */
    val taskGrants: List<TaskPermission> = emptyList(),
    /** The recipients the user allowed for good ("always for this recipient" on a data card) */
    val recipients: List<RecipientAllow> = emptyList(),
    /** Payments approved in advance (the switch, orders and schedule allows); null from a server that does not have them */
    val cover: PaymentCover? = null,
    /** Approvals being answered right now */
    val answering: Set<String> = emptySet(),
)

/** The "Past approvals" list: the pages loaded so far, newest first; [next] is the cursor of the next page (null at the end). */
data class PastApprovals(
    val items: List<ApprovalRecord> = emptyList(),
    val next: Double? = null,
    val loadingMore: Boolean = false,
    /** The last "more" failed: the button stays so it can be tried again */
    val moreFailed: Boolean = false,
)

/** Rules of the past-approvals list (paging, what a row says). */
object PastApprovalsRules {
    /** [page] appended to [cur]: a row that is already there is not added twice (a new approval shifts the pages). */
    fun append(cur: PastApprovals, page: ApprovalHistory): PastApprovals {
        val seen = cur.items.mapTo(HashSet()) { it.id }
        return PastApprovals(cur.items + page.items.filter { seen.add(it.id) }, page.nextBefore)
    }

    /** The kind of answer a row shows: its [ApprovalRecord.result] from the server, else worked out from the older `decision`. */
    fun result(a: ApprovalRecord): String = a.result.ifEmpty {
        when (a.decision) {
            "allowed_once", "allowed_always", "allowed_by_rule" -> "allow"
            "denied" -> "deny"
            "expired" -> "expired"
            else -> a.decision
        }
    }

    /** Where to show the row: the site or recipient, else the server's one-line detail. */
    fun where(a: ApprovalRecord): String = a.where.ifBlank { a.site }

    /** Whether a person answered it (a rule's allow or a run-out request has no answerer). */
    fun answeredBy(a: ApprovalRecord): String? = a.via.takeIf { it in setOf("app", "notification", "messenger") }
}

data class Journal(val month: YearMonth, val entries: List<JournalEntry> = emptyList(), val gaps: List<JournalGap> = emptyList())
