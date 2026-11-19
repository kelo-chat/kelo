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
import chat.kelo.app.agent.DayGroups
import chat.kelo.app.res.*
import chat.kelo.app.my.MyFakeServer.Companion.fail
import chat.kelo.app.my.MyFakeServer.Companion.ok
import chat.kelo.app.my.MyFakeServer.Companion.until
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.KeloTheme
import java.io.File
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import javax.imageio.ImageIO
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/** "What Kelo looked at": the codes mapped to what the screen says, the day grouping in a local zone, the decoding, the page's state and the rows at 320 and 412 dp. */
@OptIn(ExperimentalCoroutinesApi::class, ExperimentalTestApi::class)
class SituationRulesTest {
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false; coerceInputValues = true }
    private val vms = chat.kelo.app.TestViewModels()
    private var server: MyFakeServer? = null
    private val savedLocale = Locale.getDefault()
    private val seoul = ZoneId.of("Asia/Seoul")

    @Before fun main() { Dispatchers.setMain(UnconfinedTestDispatcher()) }

    @After fun stop() { vms.clear(); server?.shutdown(); Dispatchers.resetMain(); Locale.setDefault(savedLocale) }

    // ---- the mapping

    @Test fun everyPassCodeHasItsOutcomeAndAnUnknownOneIsGeneric() {
        assertEquals(SituationRules.Outcome.NO_CHANGE, SituationRules.outcome("no_change"))
        assertEquals(SituationRules.Outcome.NOTHING, SituationRules.outcome("nothing"))
        assertEquals(SituationRules.Outcome.PROPOSED, SituationRules.outcome("proposed"))
        assertEquals(SituationRules.Outcome.SELECTED, SituationRules.outcome("selected"))
        assertEquals(SituationRules.Outcome.DELIVERED, SituationRules.outcome("delivered"))
        assertEquals(SituationRules.Outcome.RUNNING, SituationRules.outcome("running"))
        assertEquals(SituationRules.Outcome.FAILED, SituationRules.outcome("failed"))
        assertEquals(SituationRules.Outcome.FAILED, SituationRules.outcome("skipped"))
        assertEquals(SituationRules.Outcome.OTHER, SituationRules.outcome("a_new_code"))
        assertEquals(SituationRules.Outcome.OTHER, SituationRules.outcome(""))
    }

    @Test fun everySourceStateFallsIntoOneOfTheDetailGroups() {
        assertEquals(SituationRules.Bucket.CHANGED, SituationRules.bucket("new"))
        assertEquals(SituationRules.Bucket.CHANGED, SituationRules.bucket("changed"))
        assertEquals(SituationRules.Bucket.SAME, SituationRules.bucket("same"))
        assertEquals(SituationRules.Bucket.SAME, SituationRules.bucket("not_due"))
        assertEquals(SituationRules.Bucket.FAILED, SituationRules.bucket("failed"))
        assertEquals(SituationRules.Bucket.FAILED, SituationRules.bucket("timeout"))
        assertEquals(SituationRules.Bucket.FAILED, SituationRules.bucket("skipped"))
        assertEquals(SituationRules.Bucket.OFF, SituationRules.bucket("off"))
        assertEquals(SituationRules.Bucket.OTHER, SituationRules.bucket("something_else"))
        val sources = listOf(SituationSource("a", "A", "connector", "new"), SituationSource("b", "B", "connector", "same"),
            SituationSource("c", "C", "connector", "timeout"))
        val b = SituationRules.bucketed(sources)
        assertEquals(listOf("a"), b.getValue(SituationRules.Bucket.CHANGED).map { it.id })
        assertEquals(listOf("b"), b.getValue(SituationRules.Bucket.SAME).map { it.id })
        assertEquals(listOf("c"), b.getValue(SituationRules.Bucket.FAILED).map { it.id })
    }

    @Test fun anItemsStatusChangesMapToTheTimelineKinds() {
        assertEquals(SituationRules.EventKind.PROPOSED, SituationRules.eventKind("proposed", ""))
        assertEquals(SituationRules.EventKind.UPDATED, SituationRules.eventKind("updated", ""))
        assertEquals(SituationRules.EventKind.SELECTED_TODAY, SituationRules.eventKind("selected", "today"))
        assertEquals(SituationRules.EventKind.SELECTED_REGULAR, SituationRules.eventKind("selected", "regular"))
        assertEquals(SituationRules.EventKind.SELECTED_REGULAR, SituationRules.eventKind("selected", "a_new_priority"))
        assertEquals(SituationRules.EventKind.TOLD, SituationRules.eventKind("told", "regular"))
        assertEquals(SituationRules.EventKind.DROPPED, SituationRules.eventKind("dropped", ""))
        assertEquals(SituationRules.EventKind.EXPIRED, SituationRules.eventKind("expired", ""))
        assertEquals(SituationRules.EventKind.WAITING, SituationRules.eventKind("waiting", ""))
        assertEquals(SituationRules.EventKind.OTHER, SituationRules.eventKind("moved", ""))
    }

    @Test fun theSectionsComeFirstAndTheOnDemandCoversAreSeparate() {
        val looked = listOf(
            SituationLooked("home_assistant", "Home Assistant", "connector", hourly = true),
            SituationLooked("memory", "", "section", hourly = true),
            SituationLooked("conversation", "", "section", hourly = true),
            SituationLooked("mail", "", "section", hourly = true),
            SituationLooked("notes", "Notes", "connector", hourly = false),
            SituationLooked("calendar", "", "section", hourly = true),
        )
        assertEquals(listOf("conversation", "mail", "calendar", "memory", "home_assistant"), SituationRules.hourly(looked).map { it.id })
        assertEquals(listOf("notes"), SituationRules.onDemand(looked).map { it.id })
    }

    @Test fun passesGroupByTheLocalDayAndAPassWithoutATimeIsUnknown() {
        val late = ZonedDateTime.of(2026, 11, 17, 23, 30, 0, 0, seoul).toEpochSecond().toDouble()
        val early = ZonedDateTime.of(2026, 11, 18, 0, 10, 0, 0, seoul).toEpochSecond().toDouble()
        val today = LocalDate.of(2026, 11, 18)
        val passes = listOf(
            SituationPass(id = 3, at = early, status = "nothing"),
            SituationPass(id = 2, at = late, status = "no_change"),
            SituationPass(id = 1, at = 0.0, status = "failed"),
        )
        val groups = SituationRules.groupPasses(passes, today, seoul)
        assertEquals(3, groups.size)
        assertEquals(DayGroups.Label.Today, groups[0].label)
        assertEquals(listOf(3L), groups[0].items.map { it.id })
        assertEquals(DayGroups.Label.Yesterday, groups[1].label)
        assertEquals(listOf(2L), groups[1].items.map { it.id })
        assertEquals(DayGroups.Label.Unknown, groups[2].label)
        // the same instant is on another day in UTC: the grouping follows the owner's zone
        assertEquals(LocalDate.of(2026, 11, 17), SituationRules.dayOf(early, ZoneId.of("UTC")))
    }

    @Test fun eventsGroupByItemInTheOrderTheyHappened() {
        val events = listOf(
            SituationEvent(itemId = 7, title = "Invoice due", status = "proposed", at = 1.0),
            SituationEvent(itemId = 8, title = "", status = "proposed", at = 2.0),
            SituationEvent(itemId = 7, title = "", status = "selected", priority = "today", at = 3.0),
            SituationEvent(itemId = 7, title = "Invoice due", status = "told", priority = "today", at = 4.0),
        )
        val groups = SituationRules.groupEvents(events)
        assertEquals(listOf(7L, 8L), groups.map { it.itemId })
        assertEquals("Invoice due", groups[0].title)
        assertEquals(listOf("proposed", "selected", "told"), groups[0].events.map { it.status })
        assertEquals("", groups[1].title)
    }

    // ---- the decoding

    @Test fun readsTheListAndTheDetailAndIgnoresWhatItDoesNotKnow() {
        val list = json.decodeFromString<SituationList>(
            """{"research": true, "every_minutes": 60, "checkpoint": 1762650000, "keep_days": 90, "future": 1,
            "looked_at": [{"id": "mail", "name": "", "kind": "section", "hourly": true}],
            "passes": [{"id": 12, "at": 1762650000.5, "status": "proposed", "proposed": 1, "selected": 0, "delivered": 0, "dropped": 0,
            "counts": {"proposed": 1, "selected": 0, "told": 0, "dropped": 0},
            "sources": [{"id": "mail", "name": "", "kind": "section", "state": "changed", "entries": 3}]}],
            "open": [], "next_before": 12}""",
        )
        assertTrue(list.research)
        assertEquals(90, list.keepDays)
        assertEquals(12L, list.nextBefore)
        assertEquals("changed", list.passes.single().sources.single().state)
        assertEquals(1, list.passes.single().counts.proposed)
        val detail = json.decodeFromString<SituationDetail>(
            """{"pass": {"id": 12, "at": 1762650000.5, "status": "proposed", "counts": {"proposed": 1}}, "sources": [],
            "events": [{"item_id": 7, "title": "Invoice due", "status": "told", "why": "told:today", "priority": "today", "at": 1762650100.0}]}""",
        )
        assertEquals(7L, detail.events.single().itemId)
        assertEquals("told", detail.events.single().status)
        assertTrue(json.decodeFromString<SituationList>("{}").passes.isEmpty())
    }

    // ---- the page's state

    private fun serve(answer: (String, String, String) -> okhttp3.mockwebserver.MockResponse?): MyFakeServer = MyFakeServer(answer).also { server = it }

    private val onePass = """{"research": true, "keep_days": 90, "looked_at": [{"id": "mail", "kind": "section", "hourly": true}],
        "passes": [{"id": 5, "at": 1762650000.0, "status": "nothing", "counts": {}, "sources": []}], "next_before": 5}"""

    @Test fun theLooksAreReadAndAnOlderServerHasNone() {
        val list = json.decodeFromString<SituationList>(
            """{"research": true, "keep_days": 90, "looked_at": [], "passes": [],
            "looks": [{"when": "hourly", "times": [], "covers": [{"id": "mail", "name": "", "kind": "section"}]},
                      {"when": "send", "times": ["12:00", "16:00", "x"], "covers": [{"id": "conversation", "name": "", "kind": "section"}]},
                      {"when": "briefing", "times": ["08:00"], "covers": [{"id": "corrections", "name": "", "kind": "section"}]},
                      {"when": "later_kind", "times": [], "covers": [{"id": "a", "name": "A", "kind": "connector"}]},
                      {"when": "needed", "times": [], "covers": []}]}""",
        )
        val shown = SituationRules.shownLooks(list.looks!!)
        assertEquals(listOf(SituationRules.LookKind.HOURLY, SituationRules.LookKind.SEND, SituationRules.LookKind.BRIEFING),
            shown.map { SituationRules.lookKind(it.whenCode) })
        assertEquals(listOf("12:00", "16:00"), SituationRules.lookTimes(shown[1]))
        assertEquals("corrections", shown[2].covers.single().id)
        assertNull(json.decodeFromString<SituationList>("""{"looked_at": []}""").looks)
    }

    @Test fun theNewSectionCodesHaveTheirNames() {
        assertEquals(Res.string.situation_section_corrections, sectionRes("corrections"))
        assertEquals(Res.string.deliveries_held_title, sectionRes("held"))
        assertEquals(Res.string.briefing_weather, sectionRes("weather"))
        assertEquals(Res.string.briefing_missed, sectionRes("missed"))
        assertEquals(Res.string.situation_section_conversation, sectionRes(" conversation "))
        assertEquals(Res.string.situation_section_other, sectionRes("a_new_code"))
    }

    @Test fun theListLoadsOnceAndAnOlderServerWithoutTheRouteIsMissing() {
        val s = serve { m, p, _ -> if (m == "GET" && p.startsWith("/api/proactive/research?limit=24")) ok(onePass) else null }
        val vm = vms.make { SituationViewModel(s.sessions()) }
        until("the list") { vm.state.value.list != null }
        assertEquals(5L, vm.state.value.list!!.passes.single().id)
        assertFalse(vm.state.value.missing)

        val none = serve { _, _, _ -> null }
        val missing = vms.make { SituationViewModel(none.sessions()) }
        until("missing") { missing.state.value.missing }
        assertEquals("", missing.state.value.error)
    }

    @Test fun moreRowsAreAddedWithTheBeforeId() {
        val s = serve { m, p, _ ->
            when {
                m == "GET" && p == "/api/proactive/research?limit=24" -> ok(onePass)
                m == "GET" && p == "/api/proactive/research?limit=24&before=5" ->
                    ok("""{"passes": [{"id": 4, "at": 1762640000.0, "status": "no_change"}], "next_before": null}""")
                else -> null
            }
        }
        val vm = vms.make { SituationViewModel(s.sessions()) }
        until("the list") { vm.state.value.list != null }
        vm.loadMore()
        until("more") { vm.state.value.list?.passes?.size == 2 }
        assertEquals(listOf(5L, 4L), vm.state.value.list!!.passes.map { it.id })
        assertNull(vm.state.value.list!!.nextBefore)
    }

    @Test fun aFailedDetailIsAnErrorNotAMissingPass() {
        val s = serve { _, p, _ -> if (p.startsWith("/api/proactive/research/passes/")) fail(500, "boom") else null }
        val vm = vms.make { SituationDetailViewModel(s.sessions(), 5L) }
        until("the error") { vm.state.value.error.isNotEmpty() }
        assertEquals("boom", vm.state.value.error)
        assertFalse(vm.state.value.missing)
    }

    // ---- the rows at narrow widths, in several languages

    private val longWord = "Donaudampfschifffahrtsgesellschaftskapitänsmützenhersteller"
    private val longName = "とても長い名前のスケジュールとても長い名前のスケジュールとても長い名前のスケジュール"

    private fun check(name: String, locale: Locale, content: @Composable () -> Unit) {
        Locale.setDefault(locale)
        for (w in listOf(320, 412)) runComposeUiTest {
            setContent { KeloTheme(dark = false) { Column(Modifier.width(w.dp).background(Kelo.colors.bg).padding(horizontal = 16.dp).testTag("root")) { content() } } }
            val bounds = onNodeWithTag("root").getBoundsInRoot()
            val shown = bounds.right - bounds.left
            assertTrue("$name at $w dp is $shown wide", shown <= w.dp)
            val dir = File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/situation").apply { mkdirs() }
            ImageIO.write(onNodeWithTag("root").captureToImage().toAwtImage(), "png", File(dir, "$name-${locale.language}-$w.png"))
        }
    }

    private val sources = listOf(
        SituationSource("", longName, "connector", "changed", 2),
        SituationSource("mail", "", "section", "new", 3),
        SituationSource("", longWord, "phone", "timeout", 0),
    )

    @Test fun theCoversAndTheSourcesFitInKorean() = check("covers", Locale.KOREAN) {
        NameList(listOf("대화", longName, longWord)); SourceList(sources)
    }

    @Test fun theCoversAndTheSourcesFitInGerman() = check("covers", Locale.GERMAN) {
        NameList(listOf("Gespräch", longName, longWord)); SourceList(sources)
    }

    @Test fun thePassRowsFitInEnglishAndJapanese() {
        val pass = SituationPass(id = 5, at = 1762650000.0, status = "proposed", counts = SituationCounts(proposed = 2))
        check("passes", Locale.ENGLISH) { PassRow(pass) {} }
        check("passes", Locale.JAPANESE) { PassRow(pass.copy(status = "failed")) {} }
    }

    @Test fun theEventRowsFitInGerman() = check("events", Locale.GERMAN) {
        EventGroupRow(SituationRules.groupEvents(listOf(
            SituationEvent(itemId = 1, title = longWord, status = "dropped", why = "Zu lang, um es zu melden, und es gibt keinen Grund dafür", at = 1762650000.0),
            SituationEvent(itemId = 1, title = longWord, status = "waiting", why = "waiting:busy", at = 1762650100.0),
        )).single())
    }

    @Test fun theLookGroupsFitInGermanAndJapanese() {
        val send = SituationLook("send", listOf("12:00", "16:00", "20:00"), listOf(
            SituationLooked("conversation", "", "section"), SituationLooked("corrections", "", "section"), SituationLooked("held", "", "section")))
        val briefing = SituationLook("briefing", listOf("08:00"), listOf(
            SituationLooked("missed", "", "section"), SituationLooked("", longWord, "connector")))
        check("looks", Locale.GERMAN) { LookGroup(send); LookGroup(briefing) }
        check("looks", Locale.JAPANESE) { LookGroup(send); LookGroup(briefing) }
    }
}
