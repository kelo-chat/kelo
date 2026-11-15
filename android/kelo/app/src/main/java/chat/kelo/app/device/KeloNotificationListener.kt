package chat.kelo.app.device

import android.app.Notification
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import chat.kelo.app.appContainer

/**
 * 알림: runs only while the user has given Kelo notification access in the system settings. Each new notification that the rules let
 * through (not Kelo's own, not ongoing or system ones, not an app the user left out) is gathered and sent to the user's own server in a
 * batch (DeviceSync.noteArrived). Nothing is sent while the 알림 row is off, even with access given.
 */
class KeloNotificationListener : NotificationListenerService() {
    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        sbn ?: return
        val c = runCatching { applicationContext.appContainer }.getOrNull() ?: return
        val prefs = c.devicePrefs
        if (!prefs.on(DeviceFeature.NOTIFICATIONS)) return
        val n = sbn.notification ?: return
        val extras = n.extras
        val title = extras?.getCharSequence(Notification.EXTRA_TITLE)?.toString().orEmpty()
        val text = (extras?.getCharSequence(Notification.EXTRA_BIG_TEXT) ?: extras?.getCharSequence(Notification.EXTRA_TEXT))?.toString().orEmpty()
        val label = runCatching {
            packageManager.getApplicationLabel(packageManager.getApplicationInfo(sbn.packageName, 0)).toString()
        }.getOrDefault(sbn.packageName)
        val seen = SeenNote(
            packageName = sbn.packageName, app = label, title = title, text = text, postedAt = sbn.postTime,
            ongoing = sbn.isOngoing || (n.flags and Notification.FLAG_ONGOING_EVENT) != 0,
            foregroundService = (n.flags and Notification.FLAG_FOREGROUND_SERVICE) != 0,
            groupSummary = (n.flags and Notification.FLAG_GROUP_SUMMARY) != 0,
            category = n.category,
        )
        if (sbn.packageName != packageName && !seen.ongoing) prefs.noteSeen(sbn.packageName, label)
        if (!DeviceRules.forward(seen, packageName, prefs.excluded)) return
        NoteBuffer.add(seen)
        DeviceSync.noteArrived(applicationContext)
    }
}
