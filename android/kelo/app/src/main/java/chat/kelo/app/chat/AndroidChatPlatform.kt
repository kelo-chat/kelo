package chat.kelo.app.chat

import android.os.SystemClock
import android.util.Log

/** What the shared chat state layer takes from the phone: its clock since boot, the device work after an approval, and the log. */
object AndroidChatPlatform : ChatPlatform {
    override fun uptimeMs(): Long = SystemClock.elapsedRealtime()
    override fun afterApproval() = chat.kelo.app.device.DeviceTasks.afterApproval()
    override fun log(tag: String, message: String, error: Throwable?) { Log.w(tag, message, error) }
}
