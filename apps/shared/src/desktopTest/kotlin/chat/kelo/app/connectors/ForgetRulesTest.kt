package chat.kelo.app.connectors

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ForgetRulesTest {
    private fun answer(json: String) = ConnectorCalls.dataOf(Json.parseToJsonElement(json))

    @Test fun `ask only when something is kept`() {
        assertFalse(ForgetRules.offer(null))                       // an older server sends no "data": no question
        assertFalse(ForgetRules.offer(ConnectorData()))
        assertTrue(ForgetRules.offer(ConnectorData(files = 1)))
        assertTrue(ForgetRules.offer(ConnectorData(items = 4)))
    }

    @Test fun `the counts leave out the kinds that are zero`() {
        assertEquals(listOf(ForgetPart.FILES to 3, ForgetPart.ITEMS to 5), ForgetRules.parts(ConnectorData(files = 3, notes = 0, items = 5)))
        assertEquals(listOf(ForgetPart.NOTES to 2), ForgetRules.parts(ConnectorData(notes = 2)))
    }

    @Test fun `files are said to go to the trash only when there are files`() {
        assertTrue(ForgetRules.mentionsTrash(ConnectorData(files = 3, notes = 2, items = 5)))
        assertFalse(ForgetRules.mentionsTrash(ConnectorData(notes = 2, items = 5)))
    }

    @Test fun `the data of the disconnect answer is read, and an answer without it is no data`() {
        assertEquals(ConnectorData(3, 2, 5), answer("""{"ok":true,"data":{"files":3,"notes":2,"items":5}}"""))
        assertNull(answer("""{"ok":true}"""))                    // an older server
        assertNull(answer("null"))
        assertFalse(ForgetRules.offer(answer("""{"ok":true,"data":{}}""")))   // a connector with nothing kept
    }

    @Test fun `a disconnect with kept data asks, one without does not`() {
        assertTrue(ForgetRules.offer(answer("""{"ok":true,"data":{"files":0,"notes":0,"items":1}}""")))
        assertFalse(ForgetRules.offer(answer("""{"ok":true,"data":{"files":0,"notes":0,"items":0}}""")))
    }
}
