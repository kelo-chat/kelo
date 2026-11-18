package chat.kelo.desktop.vnc

import org.junit.Assume
import java.io.File
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * Every message type this client can send must pass the server's filter (apps/server/.../tools/vnc.py, RFB_CLIENT_SIZES). Otherwise the
 * server closes the relay on the first such message. Skipped where the server's source is not in the checkout.
 */
class RfbServerParityTest {
    @Test fun everyTypeTheClientSendsIsInTheServersFilter() {
        val file = listOf("../../server/src/assistant/tools/vnc.py", "server/src/assistant/tools/vnc.py").map(::File).firstOrNull { it.exists() }
        Assume.assumeTrue("the server's source is not in this checkout", file != null)
        val text = file!!.readText()

        // TYPE_KEY, TYPE_POINTER, TYPE_CUT_TEXT, TYPE_QEMU = 4, 5, 6, 255
        val named = HashMap<String, Int>()
        Regex("^((?:TYPE_\\w+,\\s*)+TYPE_\\w+)\\s*=\\s*([0-9,\\s]+)$", RegexOption.MULTILINE).findAll(text).forEach { m ->
            val names = m.groupValues[1].split(',').map { it.trim() }
            val values = m.groupValues[2].split(',').map { it.trim().toInt() }
            names.zip(values).forEach { (n, v) -> named[n] = v }
        }
        val table = text.substringAfter("RFB_CLIENT_SIZES").substringBefore("\n}")
        val allowed = Regex("^\\s+(\\d+|TYPE_\\w+):\\s*\\(", RegexOption.MULTILINE).findAll(table).map { m ->
            m.groupValues[1].toIntOrNull() ?: named.getValue(m.groupValues[1])
        }.toSet()
        assertTrue(allowed.isNotEmpty(), "no entries read from RFB_CLIENT_SIZES")

        val sent = listOf(
            RfbClientMessage.SET_PIXEL_FORMAT, RfbClientMessage.SET_ENCODINGS, RfbClientMessage.UPDATE_REQUEST,
            RfbClientMessage.KEY, RfbClientMessage.POINTER, RfbClientMessage.CUT_TEXT,
        )
        for (type in sent) assertTrue(type in allowed, "the server's filter does not pass client message $type")
    }
}
