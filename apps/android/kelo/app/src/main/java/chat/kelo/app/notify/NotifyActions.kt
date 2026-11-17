package chat.kelo.app.notify

import android.app.PendingIntent
import androidx.core.app.RemoteInput
import chat.kelo.app.chat.TurnApi
import chat.kelo.app.chat.TurnEvent
import chat.kelo.app.chat.TurnFollower
import chat.kelo.app.chat.TurnWatch
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withTimeoutOrNull

/**
 * A button under a notification that does its work without opening the app. [authRequired]: the phone must be unlocked first
 * (setAuthenticationRequired, Android 12+). [remoteInput]: the button takes typed text (a reply).
 */
class NotifyAction(val label: String, val pendingIntent: PendingIntent, val authRequired: Boolean, val remoteInput: RemoteInput? = null)

/** The decisions about Allow / Deny buttons, apart from Android so they can be tested. */
object ApprovalActionRules {
    const val ALLOW = "allow"
    const val DENY = "deny"
    const val EXTRA_ID = "approval_id"
    const val EXTRA_APPROVE = "approve"
    /** What the server offered for this notification ("allow,deny" / "deny"): the receiver never sends more than that */
    const val EXTRA_OFFERED = "offered"
    const val EXTRA_NOTE_ID = "note_id"
    const val EXTRA_NOTE_TAG = "note_tag"

    /** One button: [requestCode] keeps the Allow and the Deny of one approval apart (and one approval from another). */
    data class Button(val approve: Boolean, val requestCode: Int, val authRequired: Boolean = true)

    /** The buttons of a notification and whether swiping it away answers (never: no delete intent). */
    data class Plan(val buttons: List<Button>, val hasDeleteIntent: Boolean = false)

    /** The buttons for the names of the pending item's notify_actions, in the order Allow, Deny; names this app does not know are ignored. */
    fun plan(approvalId: String, offered: Collection<String>): Plan {
        val buttons = listOf(ALLOW to true, DENY to false).filter { (name, _) -> name in offered }
            .map { (_, approve) -> Button(approve, NotifyRules.idFor("approve:$approvalId", approve.toString())) }
        return Plan(buttons)
    }

    /** An Allow is sent only when the server offered it for this notification; a Deny always may be. A payment is offered "deny" only. */
    fun mayAnswer(approve: Boolean, offered: Set<String>): Boolean = !approve || ALLOW in offered

    fun offeredOf(raw: String?): Set<String> = raw.orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() }.toSet()
}

/** Replies from the notification: the receiver's rules apart from Android. */
object ReplyActionRules {
    const val KEY_TEXT = "kelo_reply_text"
    const val EXTRA_TEXT = "reply_text"
    /** What to do with a notification's reply: nothing for blank text. */
    fun clean(text: CharSequence?): String? = text?.toString()?.trim()?.takeIf { it.isNotEmpty() }

    /** Which server notifications are answers worth replying to (the assistant's own words), besides the reply notification. */
    fun replyable(type: String): Boolean = type == NoteTypes.TASK_DONE || type == NoteTypes.PROACTIVE
}

/** What the phone notification says after an answer from it (re-posted under the same id). */
interface ApprovalNotes {
    /** Took down: the answer was accepted (or was already given somewhere else) */
    fun cancel(tag: String?, id: Int)
    /** "Unlock your phone, then try again." with the buttons kept */
    fun unlock(tag: String?, id: Int)
    /** "Couldn't send. Open Kelo to answer." with an Open action only */
    fun failed(tag: String?, id: Int)
}

/** The call POST /api/approvals/{id} {"approve"}: the HTTP status it ended with (a network failure has its own non-2xx number). */
fun interface ApprovalSink {
    suspend fun answer(approvalId: String, approve: Boolean): Int
}

/** What [ApprovalAnswerer] did, for tests and logs. */
enum class ApprovalOutcome { Ignored, Refused, Locked, Sent, Failed }

/**
 * Answers an approval from its notification: only what the server offered, only on an unlocked phone (before Android 12 the system
 * does not ask for it, so it is checked here), and the notification goes away only when the server took the answer (2xx) or the
 * approval was answered already (404).
 */
class ApprovalAnswerer(
    private val sink: ApprovalSink, private val notes: ApprovalNotes, private val deviceLocked: () -> Boolean, private val sdk: Int,
    private val budgetMs: Long = BUDGET_MS,
) {
    suspend fun handle(approvalId: String, approve: Boolean, offered: Set<String>, tag: String?, noteId: Int): ApprovalOutcome {
        if (approvalId.isBlank()) return ApprovalOutcome.Ignored
        if (!ApprovalActionRules.mayAnswer(approve, offered)) return ApprovalOutcome.Refused   // a payment's Allow needs the phone's signature in the app
        if (sdk < 31 && deviceLocked()) {
            notes.unlock(tag, noteId)
            return ApprovalOutcome.Locked
        }
        val status = withTimeoutOrNull(budgetMs) { runCatching { sink.answer(approvalId, approve) }.getOrNull() }
        return if (status != null && (status in 200..299 || status == 404)) {
            notes.cancel(tag, noteId)
            ApprovalOutcome.Sent
        } else {
            notes.failed(tag, noteId)
            ApprovalOutcome.Failed
        }
    }

    companion object {
        /** goAsync() gives a broadcast receiver about ten seconds, more on newer systems: stop waiting after this */
        const val BUDGET_MS = 20_000L
    }
}

/** What the reply's notification says (re-posted under the same id). */
interface ReplyNotes {
    /** The text as sent (remote-input history), which also stops the spinner of the reply field */
    fun sent(tag: String?, id: Int, text: String)
    /** The spinner stops, nothing else changes (blank text) */
    fun restore(tag: String?, id: Int)
    /** "Couldn't send. Tap to open Kelo." with the Reply action kept */
    fun failed(tag: String?, id: Int)
}

/** Sends a typed reply from its notification, then follows it with the reply watch, so the answer arrives as a normal reply notification. */
class ReplyRunner(private val api: TurnApi, private val watch: TurnWatch, private val notes: ReplyNotes, private val zone: () -> String) {
    /** True when the reply was accepted by the server (what happens after is the watch's). */
    suspend fun run(text: CharSequence?, tag: String?, noteId: Int): Boolean {
        val clean = ReplyActionRules.clean(text)
        if (clean == null) {
            notes.restore(tag, noteId)
            return false
        }
        notes.sent(tag, noteId, clean)
        watch.started()   // before the request: the foreground service that hosts this keeps running while the reply is followed
        val turn = try {
            api.start(clean, zone())
        } catch (e: CancellationException) {
            watch.cancelled()
            throw e
        } catch (e: Exception) {
            watch.cancelled()
            notes.failed(tag, noteId)
            return false
        }
        try {
            TurnFollower(api).follow(turn) { ev: TurnEvent -> watch.event(ev) }
            watch.finished(true)
        } catch (e: CancellationException) {
            watch.cancelled()
            throw e
        } catch (e: Exception) {
            watch.finished(false)
        }
        return true
    }
}
