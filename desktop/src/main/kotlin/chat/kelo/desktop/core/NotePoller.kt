package chat.kelo.desktop.core

import chat.kelo.app.agent.NotificationPage
import chat.kelo.app.agent.ServerNotification
import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.notify.NoteTypes
import chat.kelo.app.notify.NotifyRules
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.coroutines.coroutineContext

/**
 * The desktop's notifications from the server: what the phone gets as a push (a finished scheduled run, an approval waiting, a
 * message the agent sent by itself under "Kelo가 먼저 알려주기"), asked for every [PERIOD_MS] while Kelo runs, also from the tray.
 * GET /api/notifications?after=<last>: the first check only notes where the list stands (old ones are not replayed); later ones
 * show what nobody has seen, but not while the window is in front (the app itself shows it then).
 */
class NotePoller(
    private val settings: Settings,
    private val shown: () -> Boolean,
    private val post: (ServerNotification) -> Unit,
) {
    private val lock = Mutex()

    suspend fun check(api: KeloApi) = lock.withLock {
        val last = settings.lastNote(api.baseUrl)
        val page = api.get<NotificationPage>("/api/notifications?after=${last ?: 0}&limit=50")
        if (last != null) {
            NotifyRules.fresh(page.items, last).forEach { n ->
                if (NotifyRules.shouldPost(foreground = shown(), seen = n.seen, alreadyPosted = n.id <= last)) post(n)
            }
        }
        settings.setLastNote(api.baseUrl, NotifyRules.nextCursor(last ?: 0, page.lastId, page.items))
    }

    /** Runs until cancelled; [api] is read each round (signing out stops the checks, signing in starts them again). */
    suspend fun loop(api: () -> KeloApi?) {
        while (coroutineContext.isActive) {
            val a = api()
            if (a != null) {
                try {
                    check(a)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: ApiError) {
                    // offline or signed out: the next round tries again
                } catch (e: Exception) {
                }
            }
            delay(PERIOD_MS)
        }
    }

    companion object {
        const val PERIOD_MS = 30_000L

        /** Whether a notification type is a warning (an approval waiting, something paused) rather than news. */
        fun isWarning(n: ServerNotification): Boolean = n.urgent || n.type == NoteTypes.APPROVAL_WAITING || n.type == NoteTypes.SCHEDULE_DISABLED ||
            n.type == NoteTypes.SCHEDULE_PAUSED || n.type == NoteTypes.WATCH_DISABLED
    }
}

/** A server notification in the app's own words, exactly as the phone's Notifier.post (android/kelo .../notify/Notifier.kt). */
object NoteText {
    private val OWN_TITLES = setOf(NoteTypes.WATCH_FIRED, NoteTypes.WATCH_DISABLED, NoteTypes.PROACTIVE, NoteTypes.BETA_REQUEST, NoteTypes.CLI_UPDATE, NoteTypes.SERVER_UPDATE,
        NoteTypes.STORAGE)

    fun titleRes(type: String, urgent: Boolean = false): Int = when (type) {
        NoteTypes.TASK_DONE -> chat.kelo.app.R.string.notify_task_done
        NoteTypes.TASK_FAILED -> chat.kelo.app.R.string.notify_task_failed
        NoteTypes.APPROVAL_WAITING -> if (urgent) chat.kelo.app.R.string.notify_approval_urgent else chat.kelo.app.R.string.notify_approval_waiting
        NoteTypes.PAYMENT_AUTO -> chat.kelo.app.R.string.notify_payment_auto
        NoteTypes.SCHEDULE_PAUSED -> chat.kelo.app.R.string.notify_schedule_paused
        NoteTypes.WATCH_FIRED -> chat.kelo.app.R.string.notify_watch_fired
        NoteTypes.WATCH_DISABLED -> chat.kelo.app.R.string.notify_watch_disabled
        NoteTypes.PROACTIVE -> chat.kelo.app.R.string.notify_proactive
        NoteTypes.BETA_REQUEST -> chat.kelo.app.R.string.notify_beta_request
        NoteTypes.CLI_UPDATE -> chat.kelo.app.R.string.notify_cli_update
        NoteTypes.SERVER_UPDATE -> chat.kelo.app.R.string.server_update_notify
        NoteTypes.STORAGE -> chat.kelo.app.R.string.storage_notify
        NoteTypes.BRIEFING -> chat.kelo.app.R.string.notify_briefing
        else -> chat.kelo.app.R.string.notify_schedule_disabled
    }

    /** (title, body): a watcher's and the agent's own notes carry their own title; an empty body says to open the app. */
    fun of(n: ServerNotification, words: (Int) -> String): Pair<String, String> {
        val title = if (n.type in OWN_TITLES && n.title.isNotBlank()) n.title else words(titleRes(n.type, n.urgent))
        val body = n.body.ifBlank { n.title }.ifBlank { words(chat.kelo.app.R.string.notify_open_hint) }
        return title to body
    }
}
