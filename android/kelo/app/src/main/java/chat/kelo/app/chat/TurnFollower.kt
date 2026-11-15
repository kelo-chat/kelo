package chat.kelo.app.chat

import chat.kelo.app.core.ApiError
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.cancellation.CancellationException

/**
 * Web api.ts talk(): start a reply, then follow it with short requests instead of one long stream. A request that fails for a
 * passing reason (no signal, a gateway hiccup) is made again from the same place, so nothing is lost and the reply keeps running
 * on the server meanwhile. Returns when the reply is over.
 *
 * Cancelling the calling coroutine is the Stop button (or leaving the screen): the server is told to stop with DELETE /api/turns/{id}.
 */
class TurnFollower(
    private val api: TurnApi,
    private val sleep: suspend (Long) -> Unit = { delay(it) },
) {
    suspend fun talk(
        text: String, timezone: String, answers: List<ReplyAnswer> = emptyList(), secrets: String? = null,
        attachments: List<String> = emptyList(), autoText: Boolean = false, onEvent: (TurnEvent) -> Unit,
    ) {
        val id = when {
            attachments.isNotEmpty() -> api.start(text, timezone, answers, secrets, attachments, autoText)   // files kept with the message
            secrets != null -> api.start(text, timezone, answers, secrets)   // the owner sent a held-back message as it is ([SecretGuard])
            answers.isEmpty() -> api.start(text, timezone)
            else -> api.start(text, timezone, answers)
        }
        follow(id, onEvent)
    }

    /** Follows the reply [id] until it is over: one started here, or one the server started for this phone (a secure entry saved). */
    suspend fun follow(id: String, onEvent: (TurnEvent) -> Unit) {
        try {
            var after = 0
            var failures = 0
            while (true) {
                val r = try {
                    api.poll(id, after)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    if (!isTransient(e) || ++failures >= GIVE_UP_AFTER) throw e
                    if (failures == 2) onEvent(TurnEvent.Reconnecting)
                    sleep(backoff(failures))
                    continue
                }
                failures = 0
                r.events.forEach(onEvent)
                after = r.next
                if (r.done) return
            }
        } catch (e: CancellationException) {
            withContext(NonCancellable) {
                withTimeoutOrNull(STOP_TIMEOUT_MS) { runCatching { api.stop(id) } }
            }
            throw e
        }
    }

    companion object {
        /** Failed requests in a row (about two minutes) before the connection counts as lost. */
        const val GIVE_UP_AFTER = 30
        private const val STOP_TIMEOUT_MS = 10_000L

        /** Anything that is not an answer from the server (a broken line, an unreadable body) counts as passing. */
        fun isTransient(e: Throwable): Boolean = e !is ApiError || e.isTransient

        fun backoff(failures: Int): Long = minOf(500L * failures, 4000L)
    }
}
