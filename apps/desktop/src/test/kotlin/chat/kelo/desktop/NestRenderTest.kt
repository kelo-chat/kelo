package chat.kelo.desktop

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.core.KeloApi
import chat.kelo.app.shell.NestAction
import chat.kelo.app.shell.NestScreenBody
import chat.kelo.app.shell.NestScreenParse
import chat.kelo.app.shell.NestScreenState
import chat.kelo.app.shell.NestUi
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** The habit tracker's screen of docs/nest-ui.md ("A complete example") drawn by the shared renderer inside the desktop app's own frame. */
class NestRenderTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null

    @After fun stop() { mock?.stop() }

    private val habits = """{"schema":1,"package":"habit-tracker","surface":"habits","place":"tab","title":"Habits","components":[
      {"type":"header","text":"Keep the streak going","subtitle":"2 check-ins so far"},
      {"type":"form","fields":[
        {"id":"habit","type":"select","label":"Habit","required":true,"placeholder":"","options":[{"value":"Walk","label":"Walk 30 minutes"},{"value":"Read","label":"Read 20 pages"}]},
        {"id":"note","type":"text","label":"Note","required":false,"placeholder":"","max_length":200}],
       "submit":{"label":"Check in","action":{"id":"check_in","type":"store_add","confirm":false,"label":"Check in"}}},
      {"type":"list","rows":[
        {"key":"a1","title":"Walk","subtitle":"park","trailing":"2026-11-09","icon":"check","image":"","action":{"id":"remove","type":"store_delete","confirm":false,"label":"Remove","item":"a1","data":"checkins"}},
        {"key":"a2","title":"Read","subtitle":"","trailing":"2026-11-08","icon":"check","image":"","action":{"id":"remove","type":"store_delete","confirm":false,"label":"Remove","item":"a2","data":"checkins"}}],"empty":null},
      {"type":"button","label":"Ask Kelo for a weekly review","style":"secondary","action":{"id":"weekly","type":"agent","confirm":false,"label":"Weekly review"}},
      {"type":"future_widget"}
    ],"errors":[{"data":"trackings","error":"Connect Example Parcels first."}],"fetched_at":1762600000.0}"""

    @Test fun theCompleteExampleIsDrawnAndItsButtonRunsItsAction() {
        val app = MockServer("--lang", "en").also { mock = it }.app(tmp.newFolder(), lang = "en")
        val ran = mutableListOf<NestAction>()
        val state = NestScreenState(loaded = true, screen = NestScreenParse.screen(KeloApi.readJson(habits)))
        compose.setContent {
            KeloFrame(app) {
                Column(Modifier.width(520.dp).verticalScroll(rememberScrollState()).padding(16.dp)) {
                    NestScreenBody(state, NestUi(emptySet(), { a, _ -> ran += a }) { null })
                }
            }
        }
        compose.waitForIdle()
        compose.onNodeWithText("Connect Example Parcels first.").assertExists()
        compose.onNodeWithText("Keep the streak going").assertExists()
        compose.onNodeWithText("2 check-ins so far").assertExists()
        compose.onNodeWithText("Walk 30 minutes").assertExists()
        compose.onNodeWithText("park").assertExists()
        compose.onNodeWithText("2026-11-09").assertExists()
        compose.onAllNodesWithText("Remove").assertCountEquals(2)
        compose.onAllNodesWithText("Update the app to see this part").assertCountEquals(1)   // the part of a newer schema
        compose.onNodeWithText("Ask Kelo for a weekly review").performClick()
        assertEquals(listOf("weekly"), ran.map { it.id })
    }
}
