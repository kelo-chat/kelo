package chat.kelo.app.shell

import chat.kelo.app.core.Profile
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ModelGateTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun aServerWithoutAModelLeadsToTheModelSettings() {
        assertTrue(ModelGate.needsModel(json.decodeFromString<Profile>("""{"agent_name":"Kelo","model_ready":false}""")))
    }

    @Test
    fun aChosenModelOrTheServerDefaultNeedsNothing() {
        assertFalse(ModelGate.needsModel(json.decodeFromString<Profile>("""{"provider":"openrouter","model":"m","model_ready":true}""")))
        assertFalse(ModelGate.needsModel(json.decodeFromString<Profile>("""{"model_ready":true}""")))   // ASSISTANT_PROVIDER/MODEL
    }

    @Test
    fun anOlderServerThatDoesNotSayIsNeverAsked() {
        val p = json.decodeFromString<Profile>("""{"agent_name":"Kelo"}""")
        assertEquals(null, p.modelReady)
        assertFalse(ModelGate.needsModel(p))
    }
}
