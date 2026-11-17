package chat.kelo.app.notify

import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.ServiceCompat
import chat.kelo.app.appContainer
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/**
 * Keeps the app alive while a reply the chat follows finishes in the background (Android 14+: a dataSync foreground service, with
 * a quiet ongoing line "writing a reply"). It stops as soon as the reply is over, the app is opened again, or after
 * [MAX_MS] at most; the notification itself is posted by [ReplyWatch].
 */
class ReplyService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var watching = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {   // the notification's Stop: stop the reply; the watch then ends this service
            appContainer.replyWatch.stopNow()
            if (!watching) stopSelf()
            return START_NOT_STICKY
        }
        val container = appContainer
        val send = intent?.takeIf { it.action == ACTION_SEND_REPLY }
        try {
            ServiceCompat.startForeground(
                this, Notifier.WORKING_ID, container.notifier.working(container.replyWatch.live),
                if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0,
            )
        } catch (e: Exception) {
            if (send != null) {   // the system refused: say so on the notification the reply was typed in
                ReplyNotifierNotes(applicationContext, container.notifier)
                    .failed(send.getStringExtra(ApprovalActionRules.EXTRA_NOTE_TAG), send.getIntExtra(ApprovalActionRules.EXTRA_NOTE_ID, 0))
            }
            stopSelf()
            return START_NOT_STICKY
        }
        if (send != null) sendReply(container, send)
        if (!watching) {
            watching = true
            scope.launch {
                withTimeoutOrNull(MAX_MS) {
                    combine(container.replyWatch.following, container.visibility.visible) { following, visible -> following && !visible }
                        .first { keep -> !keep }
                }
                stop()
            }
        }
        return START_NOT_STICKY
    }

    /**
     * A reply typed into a notification ([ReplyActionReceiver]): POST /api/turns, then followed by the reply watch like a reply of the
     * chat, so the answer arrives as the normal reply notification. [ReplyRunner] starts the watch before its first request, so the
     * wait below sees the reply as followed.
     */
    private fun sendReply(container: chat.kelo.app.AppContainer, send: Intent) {
        val tag = send.getStringExtra(ApprovalActionRules.EXTRA_NOTE_TAG)
        val noteId = send.getIntExtra(ApprovalActionRules.EXTRA_NOTE_ID, 0)
        val notes = ReplyNotifierNotes(applicationContext, container.notifier)
        val api = container.sessions.api.value
        if (api == null) {
            notes.failed(tag, noteId)
            stopSelf()
            return
        }
        val runner = ReplyRunner(chat.kelo.app.chat.KeloTurnApi(api), container.replyWatch, notes) { java.util.TimeZone.getDefault().id }
        scope.launch { runner.run(send.getStringExtra(ReplyActionRules.EXTRA_TEXT), tag, noteId) }
    }

    /** Android 15: a dataSync service that ran out of its daily time is told to stop. */
    override fun onTimeout(startId: Int) {
        stop()
    }

    private fun stop() {
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        const val MAX_MS = 10 * 60 * 1000L
        const val ACTION_STOP = "chat.kelo.app.notify.STOP_REPLY"
        /** A reply typed into a notification: extras reply_text, note_id, note_tag */
        const val ACTION_SEND_REPLY = "chat.kelo.app.notify.SEND_REPLY"
    }
}
