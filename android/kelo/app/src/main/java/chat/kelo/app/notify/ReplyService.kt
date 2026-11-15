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
        val container = appContainer
        try {
            ServiceCompat.startForeground(
                this, Notifier.WORKING_ID, container.notifier.working(),
                if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC else 0,
            )
        } catch (e: Exception) {
            stopSelf()
            return START_NOT_STICKY
        }
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
    }
}
