package chat.kelo.app.agent

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ScheduleBlockLineTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false; coerceInputValues = true }

    @Test fun blockLineIsDecodedAndShownWhenNotPaused() {
        val blocked = json.decodeFromString<Schedule>(
            """{"id": 3, "name": "메일", "prompt": "check", "block_line": "Waiting for Gmail to be connected. It runs again at its next time once you connect it.", "paused": false}""")
        assertEquals("Waiting for Gmail to be connected. It runs again at its next time once you connect it.", blocked.blockLine)
        assertEquals(false, blocked.paused)
        val plain = json.decodeFromString<Schedule>("""{"id": 4, "name": "x", "prompt": "y"}""")
        assertNull(plain.blockLine)
    }
}
