package chat.kelo.app.work

import chat.kelo.app.agent.PickedUp
import chat.kelo.app.agent.Worker
import chat.kelo.app.res.*
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** A job the server picked up by itself after a restart or a lost browser: the card line and the waiting line. */
class PickupRulesTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun pickedUpIsDecodedFromTheServersJobAndAbsentForOlderServers() {
        val w = json.decodeFromString<Worker>("""{"id":"w1","status":"running","picked_up":{"at":1000.5,"why":"browser"}}""")
        assertEquals(PickedUp(1000.5, "browser"), w.pickedUp)
        assertNull(json.decodeFromString<Worker>("""{"id":"w2","status":"running"}""").pickedUp)
    }

    @Test fun aCutJobThatWaitsToBePickedUpSaysSoAndAPlainInterruptedJobDoesNot() {
        val cut = json.decodeFromString<Worker>("""{"id":"w3","status":"interrupted","waiting_for":"cut","wake_at":1090.0}""")
        assertEquals(Res.string.work_wait_pickup, waitingRes(cut))
        val plain = json.decodeFromString<Worker>("""{"id":"w4","status":"interrupted"}""")
        assertEquals(Res.string.work_wait_restart, waitingRes(plain))
    }
}
