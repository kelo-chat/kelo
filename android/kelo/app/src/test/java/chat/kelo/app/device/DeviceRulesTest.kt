package chat.kelo.app.device

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.time.ZoneId

class DeviceRulesTest {
    private val seoul = ZoneId.of("Asia/Seoul")

    @Test fun `the sideload and play builds leave out text messages, notifications and the call log`() {
        assertEquals(DeviceFeature.entries, DeviceRules.visible(full = true))
        val play = DeviceRules.visible(full = false)
        assertFalse(DeviceFeature.MESSAGES in play)
        assertFalse(DeviceFeature.CALL_LOG in play)
        assertEquals(listOf(DeviceFeature.CALENDAR, DeviceFeature.CONTACTS, DeviceFeature.DIALER, DeviceFeature.HEALTH), play)
    }

    @Test fun `the play bundle leaves out Health Connect`() {
        assertEquals(listOf(DeviceFeature.CALENDAR, DeviceFeature.CONTACTS, DeviceFeature.DIALER),
            DeviceRules.visible(full = false, health = false))
    }

    @Test fun `coming back sends again only after each row's gap`() {
        val now = 10_000_000_000L
        assertTrue(DeviceRules.due(DeviceFeature.CONTACTS, 0, now))
        assertFalse(DeviceRules.due(DeviceFeature.CONTACTS, now - 3600_000, now))
        assertTrue(DeviceRules.due(DeviceFeature.MESSAGES, now - 11 * 60_000, now))
        assertTrue(DeviceRules.due(DeviceFeature.MESSAGES, now + 5_000, now))   // the clock went back
        assertEquals(30, DeviceRules.messageDays(45))
        assertEquals(90, DeviceRules.messageDays(90))
    }

    @Test fun `own, ongoing, system and left-out notifications are not forwarded`() {
        val n = SeenNote("com.bank", "은행", "입금", "50,000원", 1_000)
        assertTrue(DeviceRules.forward(n, "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n.copy(packageName = "chat.kelo.app"), "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n, "chat.kelo.app", setOf("com.bank")))
        assertFalse(DeviceRules.forward(n.copy(ongoing = true), "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n.copy(foregroundService = true), "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n.copy(groupSummary = true), "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n.copy(category = "progress"), "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n.copy(packageName = "com.android.systemui"), "chat.kelo.app", emptySet()))
        assertFalse(DeviceRules.forward(n.copy(title = " ", text = ""), "chat.kelo.app", emptySet()))
        val body = DeviceRules.notesBody(listOf(n), "Pixel")
        assertEquals("1.0", (body["items"] as JsonArray)[0].let { (it as JsonObject)["at"]!!.jsonPrimitive.content })
    }

    @Test fun `contacts, messages and calls become the server's shapes`() {
        val c = DeviceRules.contactsBody(listOf(ContactRow("엄마", listOf("010-1234-5678" to "mobile", "01012345678" to "home"), listOf("m@x.kr"), birthday = "19600508"),
            ContactRow("", emptyList(), emptyList())), "Pixel")
        val items = c["items"] as JsonArray
        assertEquals(1, items.size)
        val mom = items[0] as JsonObject
        assertEquals(1, (mom["phones"] as JsonArray).size)                 // the same number twice is kept once
        assertEquals("1960-05-08", mom["birthday"]!!.jsonPrimitive.content)
        assertNull(DeviceRules.birthday("May 8"))
        val m = DeviceRules.messagesBody(listOf(MessageRow("010", "a".repeat(3000), 2_000, sent = true), MessageRow("011", "b", 3_000, sent = false, mms = true)), 30, true, "Pixel")
        val ms = m["items"] as JsonArray
        assertEquals("011", (ms[0] as JsonObject)["address"]!!.jsonPrimitive.content)   // newest first
        assertEquals("mms", (ms[0] as JsonObject)["kind"]!!.jsonPrimitive.content)
        assertEquals(DeviceRules.MAX_BODY, (ms[1] as JsonObject)["body"]!!.jsonPrimitive.content.length)
        assertEquals("true", m["can_send"]!!.jsonPrimitive.content)
        assertEquals(listOf("incoming", "outgoing", "missed", "voicemail", "rejected", "blocked"), (1..6).map(DeviceRules::callType))
    }

    @Test fun `health readings become one summary per day of the ticked kinds`() {
        val d1 = LocalDate.of(2026, 11, 6)
        val sleepEnd = d1.plusDays(1).atTime(7, 0).atZone(seoul).toInstant().toEpochMilli()
        val r = HealthReadings(
            steps = mapOf(d1 to 8123L, d1.plusDays(1) to 0L),
            heart = mapOf(d1 to Triple(55.0, 70.0, 140.0)),
            sleep = listOf(sleepEnd - 7 * 3600_000L to sleepEnd, sleepEnd + 3 * 3600_000L to sleepEnd + 3 * 3600_000L + 20 * 60_000L),
            weight = listOf(sleepEnd to 60.04),
        )
        val days = DeviceRules.healthDays(r, setOf("steps", "sleep"), seoul)
        assertEquals(listOf("2026-11-06", "2026-11-07"), days.map { it["day"]!!.jsonPrimitive.content })
        assertEquals("8123", days[0]["steps"]!!.jsonPrimitive.content)
        assertNull(days[0]["heart_avg"])                                       // not ticked: not sent
        assertEquals("440", days[1]["sleep_minutes"]!!.jsonPrimitive.content)  // both sessions that ended that day
        assertNull(days[1]["weight_kg"])
        val all = DeviceRules.healthDays(r, DeviceRules.HEALTH_TYPES.toSet(), seoul)
        assertEquals("60.0", all[1]["weight_kg"]!!.jsonPrimitive.content)
        assertEquals("70.0", all[0]["heart_avg"]!!.jsonPrimitive.content)
    }

    @Test fun `the phone checks a task again before doing it`() {
        fun task(json: String) = DeviceTask.parseList(Json.parseToJsonElement("""{"items": [$json]}""")).single()
        val sms = task("""{"id": 7, "kind": "sms", "number": "010-1234-5678", "text": "곧 가요", "expires_at": 2000}""")
        val allOn: (DeviceFeature) -> Boolean = { true }
        assertNull(DeviceRules.refuse(sms, full = true, on = allOn, smsAllowed = true, calWrite = false, nowS = 1000.0))
        assertEquals("expired", DeviceRules.refuse(sms, true, allOn, true, false, 3000.0))
        assertEquals("this build cannot send texts", DeviceRules.refuse(sms, false, allOn, true, false, 1000.0))
        assertEquals("sending texts is turned off on the phone", DeviceRules.refuse(sms, true, allOn, false, false, 1000.0))
        assertEquals("not a full phone number", DeviceRules.refuse(task("""{"id": 8, "kind": "sms", "number": "1234", "text": "x"}"""), true, allOn, true, false, 0.0))
        val dial = task("""{"id": 9, "kind": "dial", "number": "+82 10-1234-5678"}""")
        assertEquals("the dialer is turned off on the phone", DeviceRules.refuse(dial, true, { it != DeviceFeature.DIALER }, true, true, 0.0))
        assertEquals("+821012345678", DeviceRules.dialNumber(dial.s("number")))
        assertEquals("calendar changes are turned off on the phone",
            DeviceRules.refuse(task("""{"id": 10, "kind": "cal_add", "calendar_id": 3}"""), true, allOn, true, calWrite = false, nowS = 0.0))
        assertEquals(DeviceRules.DialWay.OPEN, DeviceRules.dialWay(visible = true))
        assertEquals(DeviceRules.DialWay.NOTIFY, DeviceRules.dialWay(visible = false))
        assertEquals(1_794_614_400_000L, DeviceRules.allDayMillis("2026-11-14"))
    }
}
