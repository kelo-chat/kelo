package chat.kelo.app.chat

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TurnEventsTest {
    @Test fun `call_offer event parses`() {
        val json = """{"type":"call_offer","call_id":"c_12345678","topic":"flight booking"}"""
        val ev = TurnEvent.parse(Json.parseToJsonElement(json))
        assertTrue(ev is TurnEvent.CallOffer)
        val offer = ev as TurnEvent.CallOffer
        assertEquals("c_12345678", offer.callId)
        assertEquals("flight booking", offer.topic)
    }

    @Test fun `stored message with via call maps to fromCall true in ChatItem Msg`() {
        val callMsg = StoredMessage(id = 10, role = "assistant", content = "hello", via = "call")
        val normalMsg = StoredMessage(id = 11, role = "user", content = "hi", via = "")
        val itemCall = toChatItem(callMsg)
        val itemNormal = toChatItem(normalMsg)
        assertTrue(itemCall.fromCall)
        assertFalse(itemNormal.fromCall)
    }

    @Test fun `kelo turn api puts call and can_call on every start`() = runBlocking {
        val server = MockWebServer().apply { start() }
        try {
            server.enqueue(MockResponse().setBody("""{"id":"turn_1"}"""))
            val api = chat.kelo.app.core.KeloApi(server.url("/").toString(), "token")
            val turnApi = KeloTurnApi(api, canCall = { true })
            val id = turnApi.start(
                text = "hello",
                timezone = "Asia/Seoul",
                answers = emptyList(),
                secrets = null,
                attachments = emptyList(),
                autoText = false,
                call = "call_abc999",
            )
            assertEquals("turn_1", id)
            val req = server.takeRequest()
            val body = Json.parseToJsonElement(req.body.readUtf8()) as JsonObject
            assertEquals("call_abc999", body["call"]?.jsonPrimitive?.content)
            assertEquals(true, body["can_call"]?.jsonPrimitive?.booleanOrNull)

            // When call is null, "call" is omitted, and can_call is still sent
            server.enqueue(MockResponse().setBody("""{"id":"turn_2"}"""))
            val turnApi2 = KeloTurnApi(api, canCall = { false })
            val id2 = turnApi2.start(text = "hi", timezone = "Asia/Seoul")
            assertEquals("turn_2", id2)
            val req2 = server.takeRequest()
            val body2 = Json.parseToJsonElement(req2.body.readUtf8()) as JsonObject
            assertNull(body2["call"])
            assertEquals(false, body2["can_call"]?.jsonPrimitive?.booleanOrNull)
        } finally {
            server.shutdown()
        }
    }

    @Test fun `a fake TurnApi without 7-arg override still works via default implementation`() = runBlocking {
        var called6Arg = false
        val fakeApi = object : TurnApi {
            override suspend fun start(text: String, timezone: String): String = "t"
            override suspend fun start(
                text: String, timezone: String, answers: List<ReplyAnswer>, secrets: String?,
                attachments: List<String>, autoText: Boolean,
            ): String {
                called6Arg = true
                return "from_6_arg"
            }
            override suspend fun poll(id: String, after: Int): TurnPoll = throw UnsupportedOperationException()
            override suspend fun stop(id: String) {}
        }

        val res = fakeApi.start(
            text = "test",
            timezone = "UTC",
            answers = emptyList(),
            secrets = null,
            attachments = emptyList(),
            autoText = false,
            call = "call_123",
        )
        assertTrue(called6Arg)
        assertEquals("from_6_arg", res)
    }
}
