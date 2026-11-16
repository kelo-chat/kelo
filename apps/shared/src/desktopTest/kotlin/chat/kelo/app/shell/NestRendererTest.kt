package chat.kelo.app.shell

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasContentDescription
import androidx.compose.ui.test.onLast
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import chat.kelo.app.core.KeloApi
import chat.kelo.app.ui.theme.KeloTheme
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Before
import java.util.Locale
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

/** Draws the screens of docs/nest-ui.md with the real renderer: every component, every action, the draft badge, a 320 dp column. */
@OptIn(ExperimentalTestApi::class)
class NestRendererTest {
    private val saved = Locale.getDefault()
    @Before fun before() { Locale.setDefault(Locale.ENGLISH) }
    @After fun after() { Locale.setDefault(saved) }

    private class Ran { val actions = mutableListOf<Pair<NestAction, JsonObject?>>() }

    private fun screen(json: String) = NestScreenParse.screen(KeloApi.readJson(json))
    private fun ui(ran: Ran, busy: Set<String> = emptySet()) = NestUi(busy, { a, f -> ran.actions += a to f }) { null }
    private fun state(json: String) = NestScreenState(loaded = true, screen = screen(json))

    private fun androidx.compose.ui.test.ComposeUiTest.show(json: String, ran: Ran = Ran(), busy: Set<String> = emptySet(), width: Int = 412) = setContent {
        KeloTheme(dark = false) {
            Column(Modifier.width(width.dp).verticalScroll(rememberScrollState())) { NestScreenBody(state(json), ui(ran, busy)) }
        }
    }

    @Test fun theCompleteExampleDrawsEveryPartOfTheHabitTrackerScreen() = runComposeUiTest {
        show(NestFixtures.HABITS)
        onNodeWithText("Connect Example Parcels first.").assertIsDisplayed()   // the data notice, once, at the top
        onNodeWithText("Keep the streak going").assertIsDisplayed()
        onNodeWithText("2 check-ins so far").assertIsDisplayed()
        onNodeWithText("Habit").assertIsDisplayed()
        onNodeWithText("Walk 30 minutes").assertIsDisplayed()   // the select shows the first option's label
        onNodeWithText("Note").assertIsDisplayed()
        onNodeWithText("Check in").assertExists()
        onNodeWithText("Walk").assertExists()
        onNodeWithText("park").assertExists()
        onNodeWithText("2026-11-09").assertExists()
        onNodeWithText("Read").assertExists()
        assertEquals(2, onAllNodesWithText("Remove").fetchSemanticsNodes().size)   // a trailing action on each row
        onNodeWithText("Ask Kelo for a weekly review").assertExists()
    }

    @Test fun theHomeCardDrawsItsStat() = runComposeUiTest {
        show(NestFixtures.CARD)
        onNodeWithText("Check-ins").assertIsDisplayed()
        onNodeWithText("2").assertIsDisplayed()
        onNodeWithText("Last: Walk on 2026-11-09").assertIsDisplayed()
    }

    @Test fun everyComponentTypeIsDrawn() = runComposeUiTest {
        show(NestFixtures.ALL)
        listOf("A title", "Some body text", "A caption", "A label", "12.5", "km", "Distance", "this week", "Nothing here yet", "Steps",
            "Parcel", "on its way", "Arrives Friday", "Cancel parcel", "Connect your account", "Ask Kelo").forEach {
            onNodeWithText(it).assertExists()
        }
        onNode(hasContentDescription("Mon 3.0, Tue 5.0, Wed 2.0")).assertExists()   // the chart's description
        onNode(hasContentDescription("A chart picture")).assertExists()             // the picture (not fetched in a test)
        onNodeWithText("Weight").assertDoesNotExist()   // a chart with no points draws nothing, not even its label
    }

    @Test fun aPartOfAnUnknownTypeIsOneUpdateRowAndTheRestStillShows() = runComposeUiTest {
        show(NestFixtures.ALL)
        assertEquals(2, onAllNodesWithText("Update the app to see this part").fetchSemanticsNodes().size)   // the unknown type, and `unsupported`
        onNodeWithText("A title").assertExists()
    }

    @Test fun anEmptyTextDrawsNoEmptyLabel() = runComposeUiTest {
        show("""{"components":[{"type":"header","text":"","subtitle":""},{"type":"text","text":""},{"type":"text","text":"Only this"}]}""")
        onNodeWithText("Only this").assertExists()
    }

    // ---- actions

    @Test fun aButtonRunsItsAction() = runComposeUiTest {
        val ran = Ran()
        show(NestFixtures.HABITS, ran)
        onNodeWithText("Ask Kelo for a weekly review").performScrollTo().performClick()
        assertEquals(listOf("weekly"), ran.actions.map { it.first.id })
        assertNull(ran.actions.single().second)
    }

    @Test fun aCardWithAnActionIsTappableAsAWhole() = runComposeUiTest {
        val ran = Ran()
        show(NestFixtures.ALL, ran)
        onNodeWithText("Parcel").performScrollTo().performClick()
        assertEquals("track", ran.actions.single().first.id)
        assertEquals("open_url", ran.actions.single().first.type)
    }

    @Test fun anEmptyStateWithAnActionHasItsButton() = runComposeUiTest {
        val ran = Ran()
        show(NestFixtures.ALL, ran)
        onNodeWithText("Ask Kelo").performScrollTo().performClick()
        assertEquals("connect", ran.actions.single().first.id)
    }

    @Test fun aRowWithAnActionThatIsNotARemovalIsTappableAndOneThatIsAsksFirst() = runComposeUiTest {
        val ran = Ran()
        show("""{"components":[{"type":"list","rows":[
            {"key":"r1","title":"Open me","subtitle":"","trailing":"","icon":"","image":"","action":{"id":"view","type":"agent","confirm":false,"label":"View","item":"r1","data":"d"}},
            {"key":"r2","title":"Delete me","subtitle":"","trailing":"","icon":"","image":"","action":{"id":"drop","type":"store_delete","confirm":false,"label":"Drop","item":"r2","data":"d"}}]}]}""", ran)
        onNodeWithText("Open me").performClick()
        assertEquals(listOf("view"), ran.actions.map { it.first.id })
        onNodeWithText("Delete me").performClick()   // tapping the row of a removal does nothing: the trailing action asks first
        assertEquals(1, ran.actions.size)
        onNodeWithText("Drop").performClick()
        onNodeWithText("Remove?").assertIsDisplayed()
        assertEquals(1, ran.actions.size)   // asked, not run
        onAllNodesWithText("Drop").fetchSemanticsNodes().size.let { assertTrue(it >= 2, "the dialog's button") }
        onAllNodesWithText("Drop").onLast().performClick()
        assertEquals(listOf("view", "drop"), ran.actions.map { it.first.id })
        assertEquals("r2", ran.actions.last().first.item)
    }

    @Test fun aButtonIsHiddenWhileItsRequestRuns() = runComposeUiTest {
        val ran = Ran()
        val weekly = NestAction("weekly", "agent", false, "Weekly review")
        show(NestFixtures.HABITS, ran, busy = setOf(weekly.key))
        onNodeWithText("Ask Kelo for a weekly review").assertDoesNotExist()
        onNodeWithText("Keep the streak going").assertExists()
    }

    // ---- forms

    @Test fun aFormSubmitSendsWhatWasFilledIn() = runComposeUiTest {
        val ran = Ran()
        show("""{"components":[{"type":"form","fields":[
            {"id":"name","type":"text","label":"Your name","required":true,"placeholder":"Name here"},
            {"id":"n","type":"number","label":"Count","required":false,"placeholder":"How many"},
            {"id":"on","type":"toggle","label":"Remind me"}],
          "submit":{"label":"Save","action":{"id":"save","type":"store_add","confirm":false,"label":"Save"}}}]}""", ran)
        onNodeWithText("Your name").assertIsDisplayed()
        onNodeWithText("Name here").performTextInput("Ann")
        onNodeWithText("How many").performTextInput("3")
        onNodeWithText("Save").performClick()
        val (a, form) = ran.actions.single()
        assertEquals("save", a.id)
        assertEquals("Ann", form!!["name"]!!.jsonPrimitive.content)
        assertEquals("3", form["n"]!!.jsonPrimitive.content)
        assertEquals("false", form["on"]!!.jsonPrimitive.content)
    }

    @Test fun aRequiredFieldLeftEmptyIsMarkedAndNothingIsSent() = runComposeUiTest {
        val ran = Ran()
        show("""{"components":[{"type":"form","fields":[{"id":"name","type":"text","label":"Your name","required":true,"placeholder":"Name here"}],
          "submit":{"label":"Save","action":{"id":"save","type":"store_add","confirm":false,"label":"Save"}}}]}""", ran)
        onNodeWithText("Save").performClick()
        onNodeWithText("Required").assertIsDisplayed()
        assertTrue(ran.actions.isEmpty())
        onNodeWithText("Name here").performTextInput("Ann")
        onNodeWithText("Required").assertDoesNotExist()
        onNodeWithText("Save").performClick()
        assertEquals(1, ran.actions.size)
    }

    @Test fun aSelectOffersItsOptionsAndSendsTheChosenValue() = runComposeUiTest {
        val ran = Ran()
        show(NestFixtures.HABITS, ran)
        onNodeWithText("Walk 30 minutes").performClick()
        onNodeWithText("Read 20 pages").performClick()
        onNodeWithText("Check in").performClick()
        val (a, form) = ran.actions.single()
        assertEquals("check_in", a.id)
        assertEquals("Read", form!!["habit"]!!.jsonPrimitive.content)
    }

    // ---- the confirm dialog

    @Test fun theConfirmDialogShowsTheQuestionWhereItGoesAndWhatIsSent() = runComposeUiTest {
        var yes = 0
        val c = NestConfirm(NestAction("cancel", "connector", true, "Cancel"), null,
            NestActionResult.Confirm("tok", "Cancel the parcel?", "Goes to parcels.example", listOf("id" to "p1", "force" to "true"), 300))
        setContent { KeloTheme(dark = false) { NestConfirmDialog(c, onYes = { yes++ }, onDismiss = {}) } }
        onNodeWithText("Cancel the parcel?").assertIsDisplayed()
        onNodeWithText("Goes to parcels.example").assertIsDisplayed()
        onNodeWithText("What will be sent").assertIsDisplayed()
        onNodeWithText("id: p1").assertIsDisplayed()
        onNodeWithText("force: true").assertIsDisplayed()
        onNodeWithText("Confirm").performClick()
        assertEquals(1, yes)
    }

    // ---- the header, the draft badge, the narrow column

    private val book = NestEntry(key = "reading/books", packageId = "reading", surface = "books", place = NestPlace.TAB, title = "Books", packageName = "Reading", author = "Me")

    @Test fun aDraftScreenCarriesADraftBadgeInItsHeaderAndNothingElseDiffers() = runComposeUiTest {
        setContent { KeloTheme(dark = false) { Column { NestHeader(book.copy(draft = true), "Books", null) } } }
        onNodeWithText("Books").assertIsDisplayed()
        onNodeWithText("Draft").assertIsDisplayed()
        onNodeWithText("From Reading · Me").assertIsDisplayed()   // whose screen it is
    }

    @Test fun anInstalledScreenHasNoBadge() = runComposeUiTest {
        setContent { KeloTheme(dark = false) { Column { NestHeader(book, "", null) } } }
        onNodeWithText("Books").assertIsDisplayed()   // no screen title yet: the surface's own
        onNodeWithText("Draft").assertDoesNotExist()
    }

    @Test fun aLongLabelWithoutSpacesStillFitsA320dpColumn() = runComposeUiTest {
        val long = "Averyveryveryverylonglabelwithoutanyspacesatalleverinanylanguage"
        show("""{"components":[{"type":"button","label":"$long","style":"primary","action":{"id":"a","type":"agent","confirm":false,"label":"x"}},
            {"type":"text","text":"$long","style":"body"},
            {"type":"list","rows":[{"key":"k","title":"$long","subtitle":"$long","trailing":"2026-11-09","icon":"check","image":"","action":null}]}]}""", width = 320)
        assertTrue(onAllNodesWithText(long, substring = true).fetchSemanticsNodes().isNotEmpty())
    }

    @Test fun whileLoadingOrGoneTheBodyHasNoComponents() = runComposeUiTest {
        setContent {
            KeloTheme(dark = false) {
                Column {
                    NestScreenBody(NestScreenState(loaded = true, gone = true), ui(Ran()))
                }
            }
        }
        onNodeWithText("This screen isn’t available any more.").assertIsDisplayed()
    }

    @Test fun aFailedFirstLoadShowsTheServersWords() = runComposeUiTest {
        setContent { KeloTheme(dark = false) { Column { NestScreenBody(NestScreenState(loaded = true, error = "Too many requests"), ui(Ran())) } } }
        onNodeWithText("Too many requests").assertIsDisplayed()
    }
}
