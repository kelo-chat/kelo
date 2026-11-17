package chat.kelo.app.notify

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.app.RemoteInput
import androidx.core.content.ContextCompat
import chat.kelo.app.MainActivity
import chat.kelo.app.R
import chat.kelo.app.agent.ServerNotification
import chat.kelo.app.chat.ReplyLive

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
             actions: List<Pair<String, OpenTarget>> = emptyList(), item: String? = null, buttons: List<NotifyAction> = emptyList(),
             quiet: Boolean = false) {
        if (!enabled()) return
        ensureChannels()
        val n = NotificationCompat.Builder(context, channel.id)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ACCENT)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setOnlyAlertOnce(quiet)   // the same notification again with its buttons: no second sound
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
            .setGroup(NotifyRules.GROUP)
            .apply {
                actions.forEachIndexed { i, (label, to) ->
                    addAction(0, label, openIntent(NotifyRules.idFor("action$i", id.toString()), to, noteId))
                }
                // buttons that answer without opening the app: each needs the phone unlocked; swiping the notification away answers
                // nothing (no delete intent on purpose)
                buttons.forEach { addAction(compatAction(it)) }
            }
            .build()
        try {
            manager.notify(item, id, n)   // the tag is the waiting item's id (same on every app): settled items are found by it
            postSummary()
        } catch (e: SecurityException) {
            // the permission was taken away between the check and now
        }
    }

    /**
     * The one bundle: shown once two or more Kelo notifications are up; lists the newest titles (the lock screen keeps them private).
     * Tapping it opens the chat. It never makes a sound of its own (GROUP_ALERT_CHILDREN).
     */
    private fun postSummary() {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val up = runCatching {
            nm.activeNotifications.filter { it.notification.group == NotifyRules.GROUP && it.id != NotifyRules.SUMMARY_ID }
                .sortedByDescending { it.postTime }
        }.getOrDefault(emptyList())
        if (up.size < 2) { manager.cancel(NotifyRules.SUMMARY_ID); return }
        val text = context.resources.getQuantityString(R.plurals.notify_group_count, up.size, up.size)
        val style = NotificationCompat.InboxStyle().setSummaryText(text)
        up.take(NotifyRules.SUMMARY_LINES).forEach { sbn ->
            sbn.notification.extras.getCharSequence(Notification.EXTRA_TITLE)?.let { style.addLine(it) }
        }
        val n = NotificationCompat.Builder(context, Channel.REPLIES.id)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ACCENT)
            .setContentTitle(context.getString(R.string.app_name))
            .setContentText(text)
            .setStyle(style)
            .setGroup(NotifyRules.GROUP)
            .setGroupSummary(true)
            .setGroupAlertBehavior(NotificationCompat.GROUP_ALERT_CHILDREN)
            .setAutoCancel(true)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setContentIntent(openIntent(NotifyRules.SUMMARY_ID, NotifyRules.SUMMARY_TARGET, null))
            .build()
        manager.notify(NotifyRules.SUMMARY_ID, n)
    }

    /**
     * A server notification (from the 15-minute check or a push), in the app's own words. [offered]: the names on the waiting item's
     * notify_actions (GET /api/approvals/pending), which decide the Allow / Deny buttons of an approval; none = no buttons.
     */
    fun post(n: ServerNotification, offered: Collection<String> = emptyList()) {
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
        val title = NotifyRules.titleOf(n, context.getString(own), context.getString(R.string.notify_open_hint))
        val body = n.body.ifBlank { n.title }.ifBlank { context.getString(R.string.notify_open_hint) }
        val key = n.approvalId ?: n.taskId?.toString() ?: n.scheduleId?.toString() ?: n.id.toString()
        val actions = NotifyRules.actions(n.type).map { to -> context.getString(R.string.storage_clean_up) to to }
        val noteId = NotifyRules.idFor(n.type, key)
        val tag = ItemIds.of(n)
        val buttons = when {
            n.type == NoteTypes.APPROVAL_WAITING && n.approvalId != null -> approvalButtons(n.approvalId!!, offered, noteId, tag)
            ReplyActionRules.replyable(n.type) -> listOf(replyButton(noteId, tag))
            else -> emptyList()
        }
        post(noteId, NotifyRules.channelOf(n.type, n.scheduleId, n.urgent), title, body, NotifyRules.target(n), n.id, actions,
            item = tag, buttons = buttons)
    }

    /** Allow / Deny buttons for the names the server put on the pending item's notify_actions (none: only tapping opens the app). */
    fun approvalButtons(approvalId: String, offered: Collection<String>, noteId: Int, tag: String?): List<NotifyAction> =
        ApprovalActionRules.plan(approvalId, offered).buttons.map { b ->
            val intent = Intent(context, ApprovalActionReceiver::class.java)
                .putExtra(ApprovalActionRules.EXTRA_ID, approvalId)
                .putExtra(ApprovalActionRules.EXTRA_APPROVE, b.approve)
                .putExtra(ApprovalActionRules.EXTRA_OFFERED, offered.joinToString(","))
                .putExtra(ApprovalActionRules.EXTRA_NOTE_ID, noteId)
                .putExtra(ApprovalActionRules.EXTRA_NOTE_TAG, tag)
            NotifyAction(
                context.getString(if (b.approve) R.string.chat_approval_allow else R.string.chat_approval_deny),
                PendingIntent.getBroadcast(context, b.requestCode, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT),
                b.authRequired,
            )
        }

    /** The "Reply" action of an answer's notification: a text field in the notification; the text goes to [ReplyActionReceiver]. */
    fun replyButton(noteId: Int, tag: String?): NotifyAction {
        val intent = Intent(context, ReplyActionReceiver::class.java)
            .putExtra(ApprovalActionRules.EXTRA_NOTE_ID, noteId)
            .putExtra(ApprovalActionRules.EXTRA_NOTE_TAG, tag)
        val label = context.getString(R.string.notify_action_reply)
        // RemoteInput needs a mutable PendingIntent (the system fills in the typed text); before Android 12 every one is
        val mutable = if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0
        return NotifyAction(
            label,
            PendingIntent.getBroadcast(context, noteId, intent, mutable or PendingIntent.FLAG_UPDATE_CURRENT),
            authRequired = true,
            remoteInput = RemoteInput.Builder(ReplyActionRules.KEY_TEXT).setLabel(label).build(),
        )
    }

    private fun compatAction(a: NotifyAction): NotificationCompat.Action =
        NotificationCompat.Action.Builder(0, a.label, a.pendingIntent).apply {
            a.remoteInput?.let { addRemoteInput(it); setAllowGeneratedReplies(false) }
            setAuthenticationRequired(a.authRequired)
        }.build()

    /** What a notification says after an answer from it, under the same id (see [ApprovalNotes] and [ReplyNotes]). */
    enum class Repost { KeepActions, OpenOnly }

    /**
     * Posts the notification (tag, id) again with [text]: the same title, channel and group, the text replaced; the actions kept or
     * replaced by one Open. [history]: a sent reply, shown under the text (this also stops the reply field's spinner). The notification
     * is made new when it was dismissed meanwhile, so a failure is never silent.
     */
    fun repost(tag: String?, id: Int, text: String, mode: Repost, history: String? = null, channel: Channel = Channel.APPROVALS) {
        if (!enabled()) return
        val old = runCatching {
            context.getSystemService(NotificationManager::class.java)?.activeNotifications?.firstOrNull { it.id == id && it.tag == tag }?.notification
        }.getOrNull()
        ensureChannels()
        val b = NotificationCompat.Builder(context, old?.channelId ?: channel.id).setSmallIcon(R.drawable.ic_notification).setColor(ACCENT)
            .setContentTitle(old?.extras?.getCharSequence(Notification.EXTRA_TITLE) ?: context.getString(R.string.app_name))
            .setGroup(NotifyRules.GROUP).setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setContentIntent(old?.contentIntent ?: openIntent(id, OpenTarget.Chat, null))
        old?.publicVersion?.let { b.setPublicVersion(it) }
        if (mode == Repost.KeepActions && old != null) {
            for (i in 0 until NotificationCompat.getActionCount(old)) NotificationCompat.getAction(old, i)?.let { b.addAction(it) }
        } else if (mode == Repost.KeepActions && channel == Channel.REPLIES) {
            b.addAction(compatAction(replyButton(id, tag)))   // the notification was swiped away meanwhile: a new one still lets the user reply
        }
        b.setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text)).setOnlyAlertOnce(true).setAutoCancel(true)
        if (mode == Repost.OpenOnly) {
            b.addAction(0, context.getString(R.string.nest_open), openIntent(NotifyRules.idFor("open", id.toString()), OpenTarget.Chat, null))
        }
        if (history != null) b.setRemoteInputHistory(arrayOf<CharSequence>(history))
        try {
            manager.notify(tag, id, b.build())
        } catch (e: SecurityException) {
            // the permission was taken away
        }
    }

    /** The text a shown notification has now (null: it is not up). */
    fun bodyOf(tag: String?, id: Int): String? = runCatching {
        context.getSystemService(NotificationManager::class.java)?.activeNotifications?.firstOrNull { it.id == id && it.tag == tag }
            ?.notification?.extras?.getCharSequence(Notification.EXTRA_TEXT)?.toString()
    }.getOrNull()

    /** Takes down one notification by its tag and id. */
    fun cancel(tag: String?, id: Int) = manager.cancel(tag, id)

    /**
     * The quiet ongoing line while a reply is followed in the background (the foreground service's notification): the step it is
     * on as the title, the site as the text, the newest browser picture, and a Stop button. Without [live] data: the fixed words.
     */
    fun working(live: ReplyLive = ReplyLive()): Notification {
        ensureChannels()
        val bmp = NotifyPictures.bitmap(live.image)
        val b = NotificationCompat.Builder(context, Channel.WORKING.id)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(ACCENT)
            .setContentTitle(live.step.ifEmpty { context.getString(R.string.notify_working_title) })
            .setContentText(if (live.site.isNotEmpty()) context.getString(R.string.notify_working_site, live.site) else context.getString(R.string.notify_working_body))
            .setOngoing(true)
            .setSilent(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(openIntent(WORKING_ID, OpenTarget.Chat, null))
            .addAction(
                0, context.getString(R.string.notify_stop),
                PendingIntent.getService(
                    context, WORKING_ID, Intent(context, ReplyService::class.java).setAction(ReplyService.ACTION_STOP),
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                ),
            )
        if (bmp != null) b.setLargeIcon(bmp).setStyle(NotificationCompat.BigPictureStyle().bigPicture(bmp).bigLargeIcon(null as Bitmap?))
        else b.setProgress(0, 0, true)
        return b.build()
    }

    /** A newer step or picture for the ongoing line (the same id as the service's notification). */
    fun updateWorking(live: ReplyLive) {
        if (!enabled()) return
        try {
            manager.notify(WORKING_ID, working(live))
        } catch (e: SecurityException) {
            // the permission was taken away between the check and now
        }
    }

    fun cancel(id: Int) = manager.cancel(id)

    /** Takes down every notification the app has up (after the chat history was cleared: they name tasks that are gone). */
    fun cancelAll() = manager.cancelAll()

    /**
     * The app came to the front (any way: a tapped notification, the launcher, the recent apps): the notifications that only told
     * something are taken down; the ones still waiting for an answer stay. The bundle is then redrawn (or removed).
     */
    fun clearNews() {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val up = runCatching { nm.activeNotifications.toList() }.getOrDefault(emptyList())
        up.filter { it.id != NotifyRules.SUMMARY_ID && it.id != WORKING_ID }
            .filter { sbn -> NotifyRules.channelOfId(sbn.notification.channelId)?.let(NotifyRules::isNews) == true }
            .forEach { manager.cancel(it.tag, it.id) }
        postSummary()
    }

    /** The ids of the waiting items whose notifications are up (the notifications' tags). */
    fun shownItems(): Set<String> {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return emptySet()
        return runCatching { nm.activeNotifications.mapNotNull { it.tag } }.getOrDefault(emptyList()).toSet()
    }

    /**
     * These waiting items do not wait any more (answered here or on another device, ran out, cancelled): their notifications are taken
     * down, and the bundle is redrawn (removed with its last child).
     */
    fun settle(items: Collection<String>) {
        if (items.isEmpty()) return
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val up = runCatching { nm.activeNotifications.toList() }.getOrDefault(emptyList())
        val gone = up.filter { it.tag != null && it.tag in items }
        gone.forEach { manager.cancel(it.tag, it.id) }
        if (gone.isNotEmpty()) postSummary()
    }

    /** Redraws the bundle after one notification went away by itself (DeviceTasks posts outside [post]). */
    fun refreshSummary() = postSummary()

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
