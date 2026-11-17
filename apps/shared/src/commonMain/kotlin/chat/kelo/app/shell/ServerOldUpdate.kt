package chat.kelo.app.shell

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.my.ServerUpdateRules
import chat.kelo.app.my.ServerUpdateStatus
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * "Update the server" on the server-too-old screen (docs/version-notices.md, part 2). The screen offers the button only when
 * GET /api/server-update answers for this device (the server has in-app updates and this is the owner's device); otherwise it says
 * where to update by hand ([INSTALL_URL], the update section). The work is the one My → Server update does: check the release list,
 * start the update, follow it through the server's restart; when it is done the screen asks the server's version again and closes.
 * Plain Kotlin over injected calls, so it is tested without a server.
 */
object ServerOldRules {
    const val INSTALL_URL = "https://kelo.chat/install"

    sealed interface Phase {
        /** Asking whether the server can update itself */
        data object Probing : Phase
        /** No in-app update here (an older server, not the owner's device, built from source): the short text and the link */
        data object Manual : Phase
        /** The button shows */
        data class Ready(val version: String?) : Phase
        data class Running(val percent: Int, val step: ServerUpdateRules.Step, val waiting: Boolean) : Phase
        data object Done : Phase
        /** [detail] is the server's own reason (its language), if it gave one */
        data class Failed(val detail: String?) : Phase
    }

    /** What the status says: nothing to press ([Phase.Manual]), a running update to follow, or the button. */
    fun phaseOf(st: ServerUpdateStatus?): Phase = when {
        st == null || st.state != ServerUpdateRules.OK -> Phase.Manual
        ServerUpdateRules.running(st) -> Phase.Running(ServerUpdateRules.percent(st.job), ServerUpdateRules.step(st.job), false)
        else -> Phase.Ready(st.latest?.version?.takeIf { it.isNotBlank() })
    }

    /** The first look: an answer from GET /api/server-update, a refusal / old server / any failure = manual. */
    suspend fun probe(load: suspend () -> ServerUpdateStatus?): Phase = try {
        phaseOf(load())
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        Phase.Manual
    }

    /** How a followed job ended. */
    fun finished(st: ServerUpdateStatus): Phase {
        val job = st.job
        return if (job?.state == ServerUpdateRules.DONE) Phase.Done
        else Phase.Failed(job?.error?.trim()?.takeIf { it.isNotEmpty() })
    }

    /**
     * Check the list again, start the update, follow it. [onPhase] gets every step. An update that cannot start (nothing newer, one is
     * running already, built from source) comes back as [Phase.Failed] with the server's reason; the manual text stays under it.
     */
    suspend fun run(
        check: suspend () -> ServerUpdateStatus,
        start: suspend () -> ServerUpdateStatus,
        fetch: suspend () -> ServerUpdateStatus,
        onPhase: (Phase) -> Unit,
        follow: suspend (ServerUpdateStatus, suspend () -> ServerUpdateStatus, (ServerUpdateRules.Follow) -> Unit) -> ServerUpdateRules.Follow =
            { s, f, on -> ServerUpdateRules.follow(s, fetch = f, onUpdate = on) },
    ): Phase {
        val last: Phase = try {
            var st = check()
            if (!ServerUpdateRules.running(st)) {
                if (!ServerUpdateRules.canUpdate(st)) return Phase.Failed(null).also(onPhase)
                st = start()
            }
            onPhase(Phase.Running(ServerUpdateRules.percent(st.job), ServerUpdateRules.step(st.job), false))
            val f = follow(st, fetch) { fo -> onPhase(Phase.Running(ServerUpdateRules.percent(fo.status.job), ServerUpdateRules.step(fo.status.job), fo.waiting)) }
            if (f.gaveUp) Phase.Failed(null) else finished(f.status)
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiError) {
            Phase.Failed(e.detail.takeIf { it.isNotBlank() && !e.isNetwork })
        } catch (e: Exception) {
            Phase.Failed(null)
        }
        onPhase(last)
        return last
    }
}

/** The state the screen shows; [onDone] runs once the update finished (the screen asks the server's version again). */
class ServerOldUpdater(private val scope: CoroutineScope, private val api: () -> KeloApi?, private val onDone: () -> Unit) {
    private val _phase = MutableStateFlow<ServerOldRules.Phase>(ServerOldRules.Phase.Probing)
    val phase: StateFlow<ServerOldRules.Phase> = _phase.asStateFlow()
    private var job: Job? = null

    /** Asks whether the update button can show; safe to call again (Try again). */
    fun probe() {
        if (_phase.value is ServerOldRules.Phase.Running) return
        job?.cancel()
        job = scope.launch {
            val a = api()
            val p = if (a == null) ServerOldRules.Phase.Manual else ServerOldRules.probe { ServerUpdateRules.load(a) }
            _phase.value = p
            if (p is ServerOldRules.Phase.Running) follow(a!!)
        }
    }

    fun update() {
        val a = api() ?: return
        if (_phase.value is ServerOldRules.Phase.Running) return
        job?.cancel()
        job = scope.launch { follow(a, fresh = true) }
    }

    private suspend fun follow(a: KeloApi, fresh: Boolean = false) {
        val end = ServerOldRules.run(
            check = { if (fresh) ServerUpdateRules.check(a) else a.get<ServerUpdateStatus>(ServerUpdateRules.PATH) },
            start = { ServerUpdateRules.start(a, rollback = false) },
            fetch = { a.get<ServerUpdateStatus>(ServerUpdateRules.PATH) },
            onPhase = { _phase.value = it },
        )
        if (end is ServerOldRules.Phase.Done) onDone()
    }
}
