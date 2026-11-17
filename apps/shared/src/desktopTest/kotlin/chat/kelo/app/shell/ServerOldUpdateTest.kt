package chat.kelo.app.shell

import chat.kelo.app.core.ApiError
import chat.kelo.app.core.KeloApi
import chat.kelo.app.my.ServerUpdateRules
import chat.kelo.app.my.ServerUpdateStatus
import chat.kelo.app.shell.ServerOldRules.Phase
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** docs/version-notices.md part 2: "Update the server" on the too-old screen shows only when the server can update itself. */
class ServerOldUpdateTest {
    private fun st(s: String) = KeloApi.json.decodeFromString<ServerUpdateStatus>(s)
    private val available = st("""{"state":"ok","current":"0.8.0","latest":{"version":"0.10.2"},"update_available":true}""")
    private val none = st("""{"state":"ok","current":"0.8.0","update_available":false}""")
    private fun job(state: String, step: String = "restart", progress: Int = 50, error: String? = null) = st(
        """{"state":"ok","current":"0.8.0","latest":{"version":"0.10.2"},"update_available":true,
            "job":{"id":"j1","action":"update","state":"$state","step":"$step","progress":$progress,"error":${error?.let { "\"$it\"" } ?: "null"}}}""")

    @Test fun theButtonShowsOnlyWhenTheServerAnswersForThisDevice() = runBlocking {
        assertEquals(Phase.Ready("0.10.2"), ServerOldRules.probe { available })
        assertEquals(Phase.Ready(null), ServerOldRules.probe { none })                       // check runs when the button is pressed
        assertEquals(Phase.Manual, ServerOldRules.probe { null })                           // 403 / 404 / "unavailable" (ServerUpdateRules.load)
        assertEquals(Phase.Manual, ServerOldRules.probe { st("""{"state":"local","message":"from source"}""") })
        assertEquals(Phase.Manual, ServerOldRules.probe { throw ApiError(0, "no answer") })
        assertTrue(ServerOldRules.probe { job("running") } is Phase.Running)               // an update already running is followed
    }

    @Test fun anUpdateChecksStartsFollowsAndEndsDone() = runBlocking {
        val calls = mutableListOf<String>()
        val seen = mutableListOf<Phase>()
        val end = ServerOldRules.run(
            check = { calls += "check"; available },
            start = { calls += "start"; job("running", "download", 10) },
            fetch = { job("done", "done", 100) },
            onPhase = { seen += it },
            follow = { s, _, on -> on(ServerUpdateRules.Follow(job("running", "restart", 60), waiting = true)); ServerUpdateRules.Follow(job("done", "done", 100)) },
        )
        assertEquals(listOf("check", "start"), calls)
        assertEquals(Phase.Done, end)
        assertTrue(seen.any { it is Phase.Running && it.step == ServerUpdateRules.Step.DOWNLOAD })
        assertTrue(seen.any { it is Phase.Running && it.waiting && it.percent == 60 })
        assertEquals(Phase.Done, seen.last())
    }

    @Test fun nothingNewerOrARefusalOrAFailedJobIsFailedNotDone() = runBlocking {
        val noop = suspend { none }
        assertEquals(Phase.Failed(null), ServerOldRules.run(check = { none }, start = { noop() }, fetch = { none }, onPhase = {}))
        val refused = ServerOldRules.run(check = { available }, start = { throw ApiError(409, "an update is already running") }, fetch = { available }, onPhase = {})
        assertEquals(Phase.Failed("an update is already running"), refused)
        val failed = ServerOldRules.run(check = { available }, start = { job("running") }, fetch = { available }, onPhase = {},
            follow = { _, _, _ -> ServerUpdateRules.Follow(job("rolled_back", error = "health check failed")) })
        assertEquals(Phase.Failed("health check failed"), failed)
        val gaveUp = ServerOldRules.run(check = { available }, start = { job("running") }, fetch = { available }, onPhase = {},
            follow = { s, _, _ -> ServerUpdateRules.Follow(s, waiting = true, gaveUp = true) })
        assertEquals(Phase.Failed(null), gaveUp)
    }
}
