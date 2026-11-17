package chat.kelo.app.chat

import android.os.SystemClock
import android.util.Log
import chat.kelo.app.approval.AndroidApprovalSigner
import chat.kelo.app.approval.ApprovalSigner

/** What the shared chat state layer takes from the phone: its clock since boot, the device work after an approval, and the log. */
object AndroidChatPlatform : ChatPlatform {
    override fun uptimeMs(): Long = SystemClock.elapsedRealtime()
    override fun afterApproval() = chat.kelo.app.device.DeviceTasks.afterApproval()
    /** Set once by [chat.kelo.app.KeloApp]: where an answered item's notification is taken down. */
    @Volatile var notifier: chat.kelo.app.notify.Notifier? = null
    override fun itemSettled(id: String) { notifier?.settle(listOf(id)) }
    override val approvalSigner: ApprovalSigner get() = AndroidApprovalSigner
    override fun log(tag: String, message: String, error: Throwable?) { Log.w(tag, message, error) }
}
