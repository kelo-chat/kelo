package chat.kelo.app.notify

import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import androidx.core.app.RemoteInput
import androidx.core.content.ContextCompat
import chat.kelo.app.R
import chat.kelo.app.appContainer
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private val actionScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

/** POST /api/approvals/{id} {"approve"} through the same background API the push service uses (this device's token, KE2E). */
internal class ApiApprovalSink(private val api: () -> KeloApi?) : ApprovalSink {
    override suspend fun answer(approvalId: String, approve: Boolean): Int {
        val api = api() ?: return NO_SESSION
        return try {
            api.post<JsonElement>("/api/approvals/$approvalId", buildJsonObject { put("approve", approve) })
            200
        } catch (e: ApiError) {
            e.status
        }
    }

    companion object { const val NO_SESSION = -1 }
}

/** An approval notification's words after an answer from it, posted by the [Notifier]. */
internal class ApprovalNotifierNotes(private val context: Context, private val notifier: Notifier) : ApprovalNotes {
    override fun cancel(tag: String?, id: Int) = notifier.cancel(tag, id)

    override fun unlock(tag: String?, id: Int) =
        notifier.repost(tag, id, context.getString(R.string.notify_unlock_retry), Notifier.Repost.KeepActions)

    override fun failed(tag: String?, id: Int) =
        notifier.repost(tag, id, context.getString(R.string.notify_send_failed_open), Notifier.Repost.OpenOnly)
}

/** A reply notification's words while and after a reply is sent from it. */
internal class ReplyNotifierNotes(private val context: Context, private val notifier: Notifier) : ReplyNotes {
    override fun sent(tag: String?, id: Int, text: String) =
        notifier.repost(tag, id, bodyOf(tag, id), Notifier.Repost.KeepActions, history = text, channel = Channel.REPLIES)

    override fun restore(tag: String?, id: Int) =
        notifier.repost(tag, id, bodyOf(tag, id), Notifier.Repost.KeepActions, channel = Channel.REPLIES)

    /** The Reply action stays: the user can type again */
    override fun failed(tag: String?, id: Int) =
        notifier.repost(tag, id, context.getString(R.string.notify_send_failed_tap), Notifier.Repost.KeepActions, channel = Channel.REPLIES)

    private fun bodyOf(tag: String?, id: Int): String = notifier.bodyOf(tag, id) ?: context.getString(R.string.notify_open_hint)
}

/**
 * Allow / Deny from the notification. Not exported: only the notification's own PendingIntent reaches it. It never sends an Allow
 * the server did not offer for this notification (a payment is offered Deny only: its Allow needs the phone's signature, in the app).
 */
class ApprovalActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val approvalId = intent.getStringExtra(ApprovalActionRules.EXTRA_ID).orEmpty()
        val approve = intent.getBooleanExtra(ApprovalActionRules.EXTRA_APPROVE, false)
        val offered = ApprovalActionRules.offeredOf(intent.getStringExtra(ApprovalActionRules.EXTRA_OFFERED))
        val noteId = intent.getIntExtra(ApprovalActionRules.EXTRA_NOTE_ID, 0)
        val tag = intent.getStringExtra(ApprovalActionRules.EXTRA_NOTE_TAG)
        val c = context.applicationContext
        val pending = goAsync()
        actionScope.launch {
            try {
                val container = c.appContainer
                val locked = { c.getSystemService(KeyguardManager::class.java)?.isDeviceLocked == true }
                val sink = ApiApprovalSink { container.sessions.api.value }
                ApprovalAnswerer(sink, ApprovalNotifierNotes(c, container.notifier), locked, Build.VERSION.SDK_INT)
                    .handle(approvalId, approve, offered, tag, noteId)
            } catch (e: Exception) {
                Log.w("KeloApproval", "answer from the notification failed", e)
            } finally {
                pending.finish()
            }
        }
    }
}

/** Reply from the notification: the typed text goes to the foreground service that sends it and follows the answer. */
class ReplyActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val c = context.applicationContext
        val noteId = intent.getIntExtra(ApprovalActionRules.EXTRA_NOTE_ID, 0)
        val tag = intent.getStringExtra(ApprovalActionRules.EXTRA_NOTE_TAG)
        val text = ReplyActionRules.clean(RemoteInput.getResultsFromIntent(intent)?.getCharSequence(ReplyActionRules.KEY_TEXT))
        val notes = ReplyNotifierNotes(c, c.appContainer.notifier)
        if (text == null) {   // blank: nothing is sent; the reply field's spinner stops
            notes.restore(tag, noteId)
            return
        }
        try {
            ContextCompat.startForegroundService(
                c, Intent(c, ReplyService::class.java).setAction(ReplyService.ACTION_SEND_REPLY)
                    .putExtra(ReplyActionRules.EXTRA_TEXT, text)
                    .putExtra(ApprovalActionRules.EXTRA_NOTE_ID, noteId)
                    .putExtra(ApprovalActionRules.EXTRA_NOTE_TAG, tag),
            )
        } catch (e: Exception) {
            Log.w("KeloReply", "could not start the reply service", e)
            notes.failed(tag, noteId)
        }
    }
}
