package chat.kelo.app.chat

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull

class KeySavedTest {
    private var n = 0
    private val key = { "k${++n}" }
    private fun run(vararg evs: TurnEvent, start: List<ChatItem> = emptyList()) =
        evs.fold(start) { items, ev -> ChatReducer.apply(items, ev, key) }

    private fun started(id: String, name: String) =
        TurnEvent.ToolStarted(id, name, JsonObject(emptyMap()))

    private fun result(id: String, name: String, output: String = "", isError: Boolean = false, facts: JsonObject = JsonObject(emptyMap())) =
        TurnEvent.ToolResult(id, name, output, isError, null, facts = facts)

    private fun saved(service: String, vararg hosts: String) = buildJsonObject {
        putJsonObject("saved_key") { put("service", service); putJsonArray("hosts") { hosts.forEach { add(JsonPrimitive(it)) } } }
    }

    @Test fun `the server facts give the name and hosts`() {
        val s = KeySavedRules.of(result("1", "save_found_secret", facts = saved("openweather", "api.openweathermap.org", "openweathermap.org")))
        assertEquals("openweather", s?.name)
        assertEquals(listOf("api.openweathermap.org", "openweathermap.org"), s?.hosts)
    }

    @Test fun `the words of the text are never read`() {
        val text = "Saved the key for 'openweather' in the keychain (hosts: api.openweathermap.org). You never see it."
        assertNull(KeySavedRules.of(result("1", "save_found_secret", output = text)))
        assertNull(KeySavedRules.of(result("1", "save_found_secret", output = "error: no hidden secret #2 in this conversation turn")))
    }

    @Test fun `save_found_secret adds a KeySaved card`() {
        val items = run(started("s1", "save_found_secret"), result("s1", "save_found_secret", facts = saved("acme", "api.acme.com", "other.acme.com")))
        val card = items.filterIsInstance<ChatItem.KeySaved>().single()
        assertEquals("acme", card.name)
        assertEquals(listOf("api.acme.com", "other.acme.com"), card.hosts)
    }
}
