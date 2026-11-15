package chat.kelo.app.notify

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import chat.kelo.app.MainActivity
import chat.kelo.app.R
import chat.kelo.app.agent.ServerNotification

/**
 * Kelo's phone notifications: the channels (made once, named in the app's language), and posting one that opens the right place in
 * the app when tapped. Nothing here answers anything: a tap only opens the app (an approval or a payment is answered there).
 */
class Notifier(private val context: Context) {
    private val manager get() = NotificationManagerCompat.from(context)

    /** Notifications can be shown (the permission on Android 13+, and the app's switch in the system settings). */
    fun enabled(): Boolean = manager.areNotificationsEnabled() &&
        (Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)

    /** Makes (or renames, after a language change) the channels. */
    fun ensureChannels() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        fun ch(c: Channel, name: Int, desc: Int, importance: Int) = NotificationChannel(c.id, context.getString(name), importance).apply {
            description = context.getString(desc)
            setShowBadge(c.badges)   // only what waits for the owner may put a dot on the launcher icon
            if (c == Channel.WORKING) { setSound(null, null); enableVibration(false) }
            // asked for at setup: a late answer means missing it. Do Not Disturb lets it through only if the user allows that
            if (c == Channel.URGENT) { setBypassDnd(true); enableVibration(true) }
        }
        Channel.RETIRED.forEach { id -> if (nm.getNotificationChannel(id) != null) nm.deleteNotificationChannel(id) }
        nm.createNotificationChannels(
            listOf(
                ch(Channel.REPLIES, R.string.notify_channel_replies, R.string.notify_channel_replies_desc, NotificationManager.IMPORTANCE_DEFAULT),
                ch(Channel.APPROVALS, R.string.notify_channel_approvals, R.string.notify_channel_approvals_desc, NotificationManager.IMPORTANCE_HIGH),
                ch(Channel.PAYMENTS, R.string.notify_channel_payments, R.string.notify_channel_payments_desc, NotificationManager.IMPORTANCE_HIGH),
                ch(Channel.SCHEDULES, R.string.notify_channel_schedules, R.string.notify_channel_schedules_desc, NotificationManager.IMPORTANCE_DEFAULT),
                ch(Channel.URGENT, R.string.notify_channel_urgent, R.string.notify_channel_urgent_desc, NotificationManager.IMPORTANCE_HIGH),
                ch(Channel.WORKING, R.string.notify_channel_working, R.string.notify_channel_working_desc, NotificationManager.IMPORTANCE_LOW),
            ),
        )
    }

    fun post(id: Int, channel: Channel, title: String, body: String, target: OpenTarget, noteId: Long? = null,
             actions: List<Pair<String, OpenTarget>> = emptyList()) {
        if (!enabled()) return
        ensureChannels()
        val n = NotificationCompat.Builder(context, channel.id)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ACCENT)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setPriority(if (channel == Channel.URGENT) NotificationCompat.PRIORITY_MAX else NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(
                when (channel) {
                    Channel.APPROVALS -> NotificationCompat.CATEGORY_REMINDER
                    Channel.URGENT -> NotificationCompat.CATEGORY_ALARM
                    Channel.PAYMENTS -> NotificationCompat.CATEGORY_STATUS
                    else -> NotificationCompat.CATEGORY_MESSAGE
                },
            )
            // the words may name a site or a payment: on the lock screen only "Kelo" and the kind of notification show
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(
                NotificationCompat.Builder(context, channel.id).setSmallIcon(R.drawable.ic_notification).setColor(ACCENT)
                    .setContentTitle(context.getString(R.string.app_name)).setContentText(title).build(),
            )
            .setContentIntent(openIntent(id, target, noteId))
            .setGroup(NotifyRules.group(channel))
            .apply {
                actions.forEachIndexed { i, (label, to) ->
                    addAction(0, label, openIntent(NotifyRules.idFor("action$i", id.toString()), to, noteId))
                }
            }
            .build()
        try {
            manager.notify(id, n)
            postSummary(channel)
        } catch (e: SecurityException) {
            // the permission was taken away between the check and now
        }
    }

    /**
     * The bundle of one channel: shown once two or more of its notifications are up, and tapping it opens that channel's list
     * (활동, 권한, 결제 카드, 예약) instead of wherever the system's own mixed bundle would go.
     */
    private fun postSummary(channel: Channel) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val key = NotifyRules.group(channel)
        val summaryId = NotifyRules.summaryId(channel)
        val count = runCatching { nm.activeNotifications.count { it.notification.group == key && it.id != summaryId } }.getOrDefault(0)
        if (count < 2) { manager.cancel(summaryId); return }
        val name = context.getString(channelName(channel))
        val text = context.resources.getQuantityString(R.plurals.notify_group_count, count, count)
        val n = NotificationCompat.Builder(context, channel.id)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ACCENT)
            .setContentTitle(name)
            .setContentText(text)
            .setStyle(NotificationCompat.InboxStyle().setSummaryText(text))
            .setGroup(key)
            .setGroupSummary(true)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setContentIntent(openIntent(summaryId, NotifyRules.summaryTarget(channel), null))
            .build()
        manager.notify(summaryId, n)
    }

    private fun channelName(channel: Channel): Int = when (channel) {
        Channel.REPLIES -> R.string.notify_channel_replies
        Channel.APPROVALS -> R.string.notify_channel_approvals
        Channel.PAYMENTS -> R.string.notify_channel_payments
        Channel.SCHEDULES -> R.string.notify_channel_schedules
        Channel.URGENT -> R.string.notify_channel_urgent
        Channel.WORKING -> R.string.notify_channel_working
    }

    /** A server notification (from the 15-minute check or a push), in the app's own words. */
    fun post(n: ServerNotification) {
        val own = when (n.type) {
            NoteTypes.TASK_DONE -> R.string.notify_task_done
            NoteTypes.TASK_FAILED -> R.string.notify_task_failed
            NoteTypes.APPROVAL_WAITING -> if (n.urgent) R.string.notify_approval_urgent else R.string.notify_approval_waiting
            NoteTypes.PAYMENT_AUTO -> R.string.notify_payment_auto
            NoteTypes.SCHEDULE_PAUSED -> R.string.notify_schedule_paused
            NoteTypes.WATCH_FIRED -> R.string.notify_watch_fired
            NoteTypes.WATCH_DISABLED -> R.string.notify_watch_disabled
            NoteTypes.PROACTIVE -> R.string.notify_proactive
            NoteTypes.BETA_REQUEST -> R.string.notify_beta_request
            NoteTypes.CLI_UPDATE -> R.string.notify_cli_update
            NoteTypes.SERVER_UPDATE -> R.string.server_update_notify
            NoteTypes.STORAGE -> R.string.storage_notify
            NoteTypes.BRIEFING -> R.string.notify_briefing
            else -> R.string.notify_schedule_disabled
        }
        // a watcher's and the agent's own notes carry their own title (the watcher's name, the agent's name)
        val title = if (n.type in OWN_TITLES && n.title.isNotBlank()) n.title else context.getString(own)
        val body = n.body.ifBlank { n.title }.ifBlank { context.getString(R.string.notify_open_hint) }
        val key = n.approvalId ?: n.taskId?.toString() ?: n.scheduleId?.toString() ?: n.id.toString()
        val actions = NotifyRules.actions(n.type).map { to -> context.getString(R.string.storage_clean_up) to to }
        post(NotifyRules.idFor(n.type, key), NotifyRules.channelOf(n.type, n.scheduleId, n.urgent), title, body, NotifyRules.target(n), n.id, actions)
    }

    private val OWN_TITLES = setOf(NoteTypes.WATCH_FIRED, NoteTypes.WATCH_DISABLED, NoteTypes.PROACTIVE, NoteTypes.BETA_REQUEST, NoteTypes.CLI_UPDATE, NoteTypes.SERVER_UPDATE,
        NoteTypes.STORAGE)

    /** The quiet ongoing line while a reply is followed in the background (the foreground service's notification). */
    fun working(): Notification {
        ensureChannels()
        return NotificationCompat.Builder(context, Channel.WORKING.id)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ACCENT)
            .setContentTitle(context.getString(R.string.notify_working_title))
            .setContentText(context.getString(R.string.notify_working_body))
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setProgress(0, 0, true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(openIntent(WORKING_ID, OpenTarget.Chat, null))
            .build()
    }

    fun cancel(id: Int) = manager.cancel(id)

    /** Takes down every notification the app has up (after the chat history was cleared: they name tasks that are gone). */
    fun cancelAll() = manager.cancelAll()

    private fun openIntent(requestCode: Int, target: OpenTarget, noteId: Long?): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            action = ACTION_OPEN
            putExtra(EXTRA_OPEN, target.encode())
            if (noteId != null) putExtra(EXTRA_NOTE, noteId)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        return PendingIntent.getActivity(context, requestCode, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }

    companion object {
        const val ACTION_OPEN = "chat.kelo.app.OPEN"
        const val EXTRA_OPEN = "kelo.open"
        const val EXTRA_NOTE = "kelo.note"
        const val WORKING_ID = 7001
        const val REPLY_ID = 7002
        private const val ACCENT = 0xFF6366F1.toInt()
    }
}
