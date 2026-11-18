package chat.kelo.app

import chat.kelo.app.my.MyFakeServer
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.UsageViewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.concurrent.TimeUnit

/** Regression tests for races in the shared desktop test harness: the request log and the view-model clean-up. */
@OptIn(ExperimentalCoroutinesApi::class)
class TestHarnessRacesTest {

    @Test
    fun theFakeServersRequestLogCanBeReadWhileTheServerAppends() = runTest {
        val s = MyFakeServer { _, _, _ -> ok("") }
        try {
            val writer = Thread { repeat(5_000) { i -> s.requests += "GET /x$i" } }
            writer.start()
            while (writer.isAlive) s.requests.count { it.startsWith("GET") }   // the test thread reads while the server thread appends
            writer.join()
            assertEquals(5_000, s.requests.count { it.startsWith("GET") })
        } finally {
            s.shutdown()
        }
    }

    @Test
    fun aViewModelClearedBeforeMainIsResetLeavesNothingForTheNextRunTest() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        val s = MyFakeServer { m, p, _ -> if (m == "GET" && p == "/api/providers") ok("[]").setHeadersDelay(300, TimeUnit.MILLISECONDS) else null }
        val vms = chat.kelo.app.TestViewModels()
        try {
            vms.make { UsageViewModel(s.sessions()) }   // its init starts the GET
            vms.clear()
            Dispatchers.resetMain()
            Thread.sleep(600)   // the answer's time has passed
            runTest { }
        } finally {
            s.shutdown()
            Dispatchers.resetMain()
        }
    }
}
