package chat.kelo.app.phonecal

import chat.kelo.app.connectors.ConnectMethod
import chat.kelo.app.connectors.Connector
import chat.kelo.app.connectors.ConnectorList
import chat.kelo.app.core.KeloApi
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

class PhoneCalendarRulesTest {
    private val seoul = ZoneId.of("Asia/Seoul")
    private fun ms(text: String) = ZonedDateTime.parse(text).toInstant().toEpochMilli()

    @Test
    fun theWindowRunsFromAWeekBackToTwoMonthsAheadInLocalDays() {
        val (start, end) = PhoneCalendarRules.window(ms("2026-11-08T15:30:00+09:00[Asia/Seoul]"), seoul)
        assertEquals(ms("2026-11-01T00:00:00+09:00[Asia/Seoul]"), start)
        assertEquals(ms("2027-01-08T00:00:00+09:00[Asia/Seoul]"), end)
    }

    @Test
    fun declinedAndCancelledAreLeftOutAndAllDayBecomesThePhonesDays() {
        val rows = listOf(
            PhoneEvent(1, "치과", ms("2026-11-09T15:00:00+09:00[Asia/Seoul]"), ms("2026-11-09T16:00:00+09:00[Asia/Seoul]"), false,
                location = "강남역", calendar = "개인", attendees = 2),
            PhoneEvent(2, "거절한 회의", ms("2026-11-09T17:00:00+09:00[Asia/Seoul]"), ms("2026-11-09T18:00:00+09:00[Asia/Seoul]"), false, selfStatus = 2),
            PhoneEvent(3, "취소됨", ms("2026-11-09T19:00:00+09:00[Asia/Seoul]"), ms("2026-11-09T20:00:00+09:00[Asia/Seoul]"), false, status = 2),
            // Android keeps all-day events at UTC midnight: 20th to 23rd (exclusive) = three days
            PhoneEvent(4, "제주 여행", ms("2026-11-20T00:00:00Z[UTC]"), ms("2026-11-23T00:00:00Z[UTC]"), true),
            PhoneEvent(1, "치과", ms("2026-11-09T15:00:00+09:00[Asia/Seoul]"), ms("2026-11-09T16:00:00+09:00[Asia/Seoul]"), false),
        )
        val out = PhoneCalendarRules.clean(rows, seoul)
        assertEquals(listOf("치과", "제주 여행"), out.map { it["title"]!!.jsonPrimitive.content })
        val dentist = out[0]
        assertEquals(ms("2026-11-09T15:00:00+09:00[Asia/Seoul]") / 1000.0, dentist["start"]!!.jsonPrimitive.double, 0.0)
        assertEquals(2, dentist["attendees"]!!.jsonPrimitive.int)
        assertEquals("강남역", dentist["location"]!!.jsonPrimitive.content)
        val trip = out[1]
        assertTrue(trip["all_day"]!!.jsonPrimitive.boolean)
        assertEquals("2026-11-20", trip["day"]!!.jsonPrimitive.content)
        assertEquals("2026-11-22", trip["last_day"]!!.jsonPrimitive.content)
        assertEquals(ms("2026-11-20T00:00:00+09:00[Asia/Seoul]") / 1000.0, trip["start"]!!.jsonPrimitive.double, 0.0)
        assertNull(trip["location"])
    }

    @Test
    fun theSnapshotCarriesTheWindowZoneAndEvents() {
        val w = 1_000_000L to 2_000_000L
        val s = PhoneCalendarRules.snapshot(emptyList(), w, seoul, "Pixel 8 · Kelo")
        assertEquals("Asia/Seoul", s["timezone"]!!.jsonPrimitive.content)
        assertEquals(1000.0, s["window_start"]!!.jsonPrimitive.double, 0.0)
        assertEquals(0, s["events"]!!.jsonArray.size)
        assertEquals("Pixel 8 · Kelo", s["device_name"]!!.jsonPrimitive.content)
    }

    @Test
    fun comingBackSendsAgainOnlyAfterAFewMinutes() {
        assertTrue(PhoneCalendarRules.dueOnResume(0, 10))
        assertFalse(PhoneCalendarRules.dueOnResume(1_000_000, 1_000_000 + 60_000))
        assertTrue(PhoneCalendarRules.dueOnResume(1_000_000, 1_000_000 + PhoneCalendarRules.RESUME_GAP_MS))
        assertTrue(PhoneCalendarRules.dueOnResume(5_000_000, 1_000_000))   // the clock moved back
    }

    @Test
    fun googleCalendarIsASignInWithTheServiceAccountAsTheOtherWay() {
        val row = KeloApi.json.decodeFromString(
            Connector.serializer(),
            """{"id":"google_calendar","name":"Google Calendar","status":"available","connect":"google","via":"calendar",
               "item":"google-calendar","fallback":{"connect":"key","via":"google","item":"google-api","blurb":"b","connected":false}}""",
        )
        assertEquals(ConnectMethod.GOOGLE, ConnectorList.method(row.connect))
        assertEquals("key", row.fallback!!.connect)
        assertEquals("google-api", row.fallback!!.item)
    }

    @Test
    fun theFactsShowHowItIsReallyConnectedAndWho() {
        val gmail = KeloApi.json.decodeFromString(
            Connector.serializer(),
            """{"id":"gmail","name":"Gmail","status":"connected","connect":"mail","item":"mail-gmail","method":"google","account":"me@gmail.com"}""",
        )
        assertEquals(ConnectMethod.GOOGLE, ConnectorList.methodOf(gmail))
        assertEquals("me@gmail.com", ConnectorList.account(gmail, emptyList()))
        assertEquals(ConnectMethod.MAIL, ConnectorList.methodOf(gmail.copy(method = "password")))
        assertEquals(ConnectMethod.MAIL, ConnectorList.methodOf(gmail.copy(method = "")))
    }
}
