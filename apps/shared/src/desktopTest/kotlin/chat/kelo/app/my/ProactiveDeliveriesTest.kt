package chat.kelo.app.my

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toAwtImage
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.KeloTheme
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.time.ZoneId
import java.util.Locale
import javax.imageio.ImageIO
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** The delivery count and the held items: the model's mapping (fields present or absent, why codes, the 0..10 bound) and the rows at 320 and 412 dp. */
@OptIn(ExperimentalCoroutinesApi::class, ExperimentalTestApi::class)
class ProactiveDeliveriesTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false; coerceInputValues = true }
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null
    private val savedLocale = Locale.getDefault()

    @Before fun main() { Dispatchers.setMain(UnconfinedTestDispatcher()) }

    @After fun stop() { vms.clear(); server?.shutdown(); Dispatchers.resetMain(); Locale.setDefault(savedLocale) }

    // ---- the mapping

    @Test fun readsTheDeliveryFieldsWhenTheServerSendsThem() {
        val p = json.decodeFromString<ProactiveSettings>("""{"enabled": true, "daily_deliveries": 3, "today_deliveries": 1, "held": 4}""")
        assertEquals(3, p.dailyDeliveries)
        assertEquals(1, p.todayDeliveries)
        assertEquals(4, p.held)
        assertTrue(DeliveryRules.hasCount(p))
        assertTrue(DeliveryRules.hasHeld(p))
        // 0 is a real choice (only what cannot wait), not "missing"
        val zero = json.decodeFromString<ProactiveSettings>("""{"daily_deliveries": 0, "today_deliveries": 0, "held": 0}""")
        assertTrue(DeliveryRules.hasCount(zero))
        assertEquals(0, zero.dailyDeliveries)
    }

    @Test fun anOlderServerWithoutTheFieldsHidesTheRows() {
        val p = json.decodeFromString<ProactiveSettings>("""{"enabled": true, "daily_total": 3}""")
        assertNull(p.dailyDeliveries)
        assertNull(p.todayDeliveries)
        assertNull(p.held)
        assertFalse(DeliveryRules.hasCount(p))
        assertFalse(DeliveryRules.hasHeld(p))
        // a null the server sends explicitly is the same as absent
        assertFalse(DeliveryRules.hasCount(json.decodeFromString<ProactiveSettings>("""{"daily_deliveries": null}""")))
    }

    @Test fun theChoiceStaysInsideZeroToTen() {
        assertEquals(0, DeliveryRules.clamp(-1))
        assertEquals(0, DeliveryRules.clamp(0))
        assertEquals(10, DeliveryRules.clamp(10))
        assertEquals(10, DeliveryRules.clamp(11))
        assertEquals(7, DeliveryRules.clamp(7))
        assertEquals(0, DeliveryRules.MIN)
        assertEquals(10, DeliveryRules.MAX)
    }

    @Test fun whyCodesMapToTheirReasonAndAnUnknownOneToTheGeneralText() {
        assertEquals(DeliveryRules.Why.SLOTS, DeliveryRules.why("slots"))
        assertEquals(DeliveryRules.Why.OVER_CAP, DeliveryRules.why("over_cap"))
        assertEquals(DeliveryRules.Why.WAITING_FOR_REPLY, DeliveryRules.why("waiting_for_reply"))
        assertEquals(DeliveryRules.Why.OTHER, DeliveryRules.why(""))
        assertEquals(DeliveryRules.Why.OTHER, DeliveryRules.why("quiet_hours_or_something_new"))
    }

    @Test fun readsHeldItemsAndSurvivesMissingAndExtraFields() {
        val list = json.decodeFromString<HeldList>(
            """{"items": [{"key": "mail:1", "source": "mail", "title": "Invoice due", "urgency": "오늘", "message": "The invoice is due today.",
            "why": "slots", "held_at": 1762650000.5, "expires_at": 1762680000.0, "future_field": 1}, {"key": "x"}]}""",
        )
        assertEquals(2, list.items.size)
        val a = list.items[0]
        assertEquals("Invoice due", a.title)
        assertEquals(1762680000.0, a.expiresAt, 0.0)
        assertEquals("slots", a.why)
        val b = list.items[1]
        assertEquals("", b.title)
        assertNull(b.urgency)
        assertEquals(0.0, b.expiresAt, 0.0)
        assertTrue(json.decodeFromString<HeldList>("{}").items.isEmpty())
    }

    @Test fun theHeadingIsTheTitleElseTheMessagesFirstLineAndThePreviewNeverRepeatsIt() {
        val item = HeldItem(title = "Invoice due", message = "The invoice is due today.\n\n\nPay it before 5.")
        assertEquals("Invoice due", DeliveryRules.heading(item))
        assertEquals("The invoice is due today.\nPay it before 5.", DeliveryRules.preview(item))
        val untitled = HeldItem(message = "  Only a line  ")
        assertEquals("Only a line", DeliveryRules.heading(untitled))
        assertEquals("", DeliveryRules.preview(untitled))
        assertEquals("", DeliveryRules.heading(HeldItem()))
    }

    @Test fun aDeadlineOnTheSameLocalDayIsShownAsATimeOnly() {
        val zone = ZoneId.of("Asia/Seoul")
        val noon = java.time.ZonedDateTime.of(2026, 11, 16, 12, 0, 0, 0, zone).toEpochSecond().toDouble()
        val six = noon + 6 * 3600
        val nextDay = noon + 24 * 3600
        assertTrue(DeliveryRules.sameDay(six, noon, zone))
        assertFalse(DeliveryRules.sameDay(nextDay, noon, zone))
    }

    // ---- the calls

    private fun serve(answer: (String, String, String) -> okhttp3.mockwebserver.MockResponse?): MyFakeServer = MyFakeServer(answer).also { server = it }

    @Test fun theHeldPageLoadsTheListOnce() {
        val s = serve { m, p, _ ->
            if (m == "GET" && p == "/api/proactive/held") ok("""{"items": [{"key": "k1", "title": "T", "message": "M", "why": "over_cap", "expires_at": 1762680000.0}]}""") else null
        }
        val vm = vms.make { ProactiveHeldViewModel(s.sessions()) }
        until("the items") { vm.state.value.items != null }
        assertEquals("k1", vm.state.value.items!!.single().key)
        assertFalse(vm.state.value.missing)
    }

    @Test fun aServerWithoutTheListIsMissingNotAnError() {
        val s = serve { _, _, _ -> null }
        val vm = vms.make { ProactiveHeldViewModel(s.sessions()) }
        until("missing") { vm.state.value.missing }
        assertEquals("", vm.state.value.error)
    }

    @Test fun anErrorFromTheListIsShown() {
        val s = serve { _, p, _ -> if (p == "/api/proactive/held") fail(500, "boom") else null }
        val vm = vms.make { ProactiveHeldViewModel(s.sessions()) }
        until("the error") { vm.state.value.error.isNotEmpty() }
        assertEquals("boom", vm.state.value.error)
    }

    @Test fun theChoiceIsSavedWithPutProactive() {
        var body = ""
        val s = serve { m, p, b ->
            when {
                m == "PUT" && p == "/api/proactive" -> { body = b; ok("""{"enabled": true, "daily_deliveries": 4, "today_deliveries": 0, "held": 0}""") }
                m == "GET" && p == "/api/proactive" -> ok("""{"enabled": true, "daily_deliveries": 2}""")
                else -> null
            }
        }
        val vm = vms.make { NotifySettingsViewModel(s.sessions()) }
        until("loaded") { vm.state.value.proactive != null }
        vm.putProactive(buildJsonObject { put("daily_deliveries", DeliveryRules.clamp(4)) })
        until("saved") { vm.state.value.proactive?.dailyDeliveries == 4 }
        assertTrue(body.contains("\"daily_deliveries\":4"))
    }

    // ---- the rows at narrow widths, in Korean and English

    private val longWord = "Donaudampfschifffahrtsgesellschaftskapitänsmützenhersteller"
    private val japanese = "とても長い名前のスケジュールとても長い名前のスケジュールとても長い名前のスケジュール"

    private fun check(name: String, locale: Locale, content: @Composable () -> Unit) {
        Locale.setDefault(locale)
        for (w in listOf(320, 412)) runComposeUiTest {
            setContent { KeloTheme(dark = false) { Column(Modifier.width(w.dp).background(Kelo.colors.bg).padding(horizontal = 16.dp).testTag("root")) { content() } } }
            val bounds = onNodeWithTag("root").getBoundsInRoot()
            val shown = bounds.right - bounds.left
            assertTrue("$name at $w dp is $shown wide", shown <= w.dp)
            val dir = File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/proactive-deliveries").apply { mkdirs() }
            ImageIO.write(onNodeWithTag("root").captureToImage().toAwtImage(), "png", File(dir, "$name-${locale.language}-$w.png"))
        }
    }

    private val settings = ProactiveSettings(enabled = true, dailyDeliveries = 2, todayDeliveries = 1, held = 3)
    private val held = listOf(
        HeldItem("a", "mail", "Invoice due", "오늘", "The invoice is due today. Pay it before 5, or the late fee applies and the account is blocked.", "slots", 0.0, 1794744000.0),
        HeldItem("b", "calendar", longWord, "보통", "$japanese $longWord", "over_cap", 0.0, 1794000000.0),
        HeldItem("c", "memory", "", null, "No title, only words", "waiting_for_reply", 0.0, 1794000000.0),
        HeldItem("d", "memory", "Odd", null, "Something new", "a_code_this_app_does_not_know", 0.0, 0.0),
    )

    @Test fun theDeliveryRowsFitInKorean() = check("settings", Locale.KOREAN) { DeliveriesBlock(settings, onChange = {}, onOpenHeld = {}) }

    @Test fun theDeliveryRowsFitInEnglish() = check("settings", Locale.ENGLISH) { DeliveriesBlock(settings, onChange = {}, onOpenHeld = {}) }

    @Test fun theDeliveryRowsFitInGermanWithLargeCounts() =
        check("settings-big", Locale.GERMAN) { DeliveriesBlock(settings.copy(dailyDeliveries = 10, todayDeliveries = 10, held = 12), onChange = {}, onOpenHeld = {}) }

    @Test fun theHeldItemsFitInKorean() = check("held", Locale.KOREAN) { held.forEach { HeldRow(it) } }

    @Test fun theHeldItemsFitInEnglish() = check("held", Locale.ENGLISH) { held.forEach { HeldRow(it) } }
}
