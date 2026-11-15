package chat.kelo.app.chat

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TurnEventParseTest {
    @Test fun parsesAPoll() {
        val poll = TurnPoll.parse(Json.parseToJsonElement("""
            {"events":[
              {"type":"thinking"},
              {"type":"text","text":"hi"},
              {"type":"tool_started","id":"t","name":"connect_service","arguments":{"service":"github"}},
              {"type":"tool_result","id":"t","name":"connect_service","output":"ok","is_error":false,"approved":null},
              {"type":"tool_result","id":"u","name":"x","output":"no","is_error":true,"approved":false},
              {"type":"approval_request","approval_id":"a1","name":"send","arguments":{"k":1}},
              {"type":"done","input_tokens":3,"output_tokens":4,"steps":2},
              {"type":"something_new"}
            ],"next":8,"done":true}
        """))
        assertEquals(8, poll.next)
        assertTrue(poll.done)
        val e = poll.events
        assertEquals(TurnEvent.Thinking, e[0])
        assertEquals(TurnEvent.Text("hi"), e[1])
        assertEquals("github", (e[2] as TurnEvent.ToolStarted).service)
        assertNull((e[3] as TurnEvent.ToolResult).approved)
        assertEquals(false, (e[4] as TurnEvent.ToolResult).approved)
        assertTrue((e[4] as TurnEvent.ToolResult).isError)
        assertNull((e[5] as TurnEvent.ApprovalRequest).detail)
        assertEquals(TurnEvent.Done(3, 4, 2), e[6])
        assertEquals(TurnEvent.Unknown("something_new"), e[7])
    }
}
