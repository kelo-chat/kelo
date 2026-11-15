package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.i18n.Strings
import chat.kelo.desktop.pages.CLEAR_HISTORY
import chat.kelo.desktop.pages.MyPane
import chat.kelo.desktop.pages.MyView
import chat.kelo.desktop.pages.my.DigestRules
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.net.HttpURLConnection
import java.net.URI

/**
 * 마이 against tools/mock_server.py, in Korean, light and dark: the grouped home, each sub page (devices with the owner badge and a
 * new device's QR code, resources, privacy, language, notifications, usage, documents, records, profile, the agent, this device),
 * and clearing the chat history (the dialog, DELETE /api/messages, the chat told). Each page is saved as parity-my-<page>-<theme>.png.
 */
class MyPagesTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null
    private val ko = Strings.of("ko")
    /** The page MyPane opens next (a whole window of rows does not fit the test's 1024x768 screen; scrolling it there never settles) */
    private val start = androidx.compose.runtime.mutableStateOf<String?>(null)

    @After fun stop() { mock?.stop() }

    @Test fun myPagesWorkAgainstTheMock() {
        val server = MockServer("--lang", "ko").also { mock = it }
        val app = server.app(tmp.newFolder(), lang = "ko", theme = "light")
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent {
            Box(Modifier.size(1000.dp, 1400.dp)) {
                KeloFrame(app) { MyPane(app, topInset = 0.dp, start = start.value, onStartUsed = { start.value = null }, onSignedOut = {}) }
            }
        }
        try {
            for (theme in listOf("light", "dark")) {
                app.settings.setTheme(theme)
                pages(ui, app, theme)
            }
        } catch (e: Throwable) {
            runCatching { ui.shot("parity-my-zz-failed") }
            throw e
        }
    }

    private fun open(ui: UiSteps, view: String) {
        start.value = view
        ui.settle()
    }

    private fun back(ui: UiSteps) {
        compose.onAllNodesWithContentDescription(ko.get(R.string.back))[0].performClick()
        ui.waitText(ko.get(R.string.my_section_account))
    }

    private fun pages(ui: UiSteps, app: AppContainer, theme: String) {
        ui.waitText(ko.get(R.string.my_section_account))
        ui.waitText(ko.get(R.string.count_devices, 3))
        ui.shot("parity-my-home-$theme")

        // devices: the owner's phone carries the badge, this one says so; a new device gets a QR code
        open(ui, MyView.DEVICES)
        ui.waitText(ko.get(R.string.devices_owner_badge))
        ui.waitText("Desktop · Kelo")
        compose.onNodeWithText(ko.get(R.string.devices_add)).performClick()
        ui.waitUntil("the QR code") {
            runCatching { compose.onNodeWithContentDescription(ko.get(R.string.devices_qr_label)).fetchSemanticsNode() }.isSuccess
        }
        ui.shot("parity-my-devices-$theme")
        back(ui)

        open(ui, MyView.RESOURCES)
        ui.waitText("assistant limits --choose")
        ui.shot("parity-my-resources-$theme")
        back(ui)

        open(ui, MyView.PRIVACY)
        ui.waitText(ko.array(R.array.privacy_titles).first())
        ui.shot("parity-my-privacy-$theme")
        back(ui)

        open(ui, MyView.LANGUAGE)
        ui.waitText(ko.get(R.string.language_system))
        ui.shot("parity-my-language-$theme")
        back(ui)

        open(ui, MyView.NOTIFY)
        ui.waitText(ko.get(R.string.heads_switch))
        ui.waitText(ko.get(R.string.followup_settings_switch))
        ui.shot("parity-my-notify-$theme")
        back(ui)

        open(ui, MyView.USAGE)
        ui.waitText(ko.get(R.string.refresh))
        ui.shot("parity-my-usage-$theme")
        back(ui)

        open(ui, MyView.DOCUMENTS)
        ui.waitText(ko.get(R.string.docs_upload))
        ui.shot("parity-my-documents-$theme")
        back(ui)

        open(ui, MyView.RECORDS)
        ui.waitText(ko.get(R.string.lookback_title))
        // The mock's sample journal is dated yesterday (tools/mock_parts/my.py) and the page opens on this week (Monday to Sunday):
        // on a Monday yesterday is in last week, so go back one week there, as a person would, before looking for it.
        val today = java.time.LocalDate.now()
        if (DigestRules.startOf(today.minusDays(1)) < DigestRules.startOf(today)) {
            compose.onNodeWithContentDescription(ko.get(R.string.my_records_prev_week)).performClick()
        }
        ui.waitText("분기 보고서 뼈대", timeout = 15_000)
        ui.shot("parity-my-records-$theme")
        back(ui)

        open(ui, MyView.PROFILE)
        ui.waitText(ko.get(R.string.profile_user_name))
        ui.shot("parity-my-profile-$theme")
        back(ui)

        open(ui, MyView.AGENT)
        ui.waitText(ko.get(R.string.my_agent_now))
        ui.shot("parity-my-agent-$theme")
        back(ui)

        open(ui, MyView.DESKTOP)
        ui.waitText(ko.get(R.string.desktop_keep_in_tray))
        ui.shot("parity-my-desktop-$theme")
        back(ui)

        // clearing the chat: the full dialog, then the server's history is gone and the chat is told
        val before = app.sessions.historyCleared.value
        open(ui, CLEAR_HISTORY)
        ui.waitText(ko.get(R.string.my_clear_chat_confirm))
        ui.shot("parity-my-clear-history-$theme")
        compose.onNodeWithText(ko.get(R.string.delete)).performClick()
        ui.waitText(ko.get(R.string.my_clear_chat_done))
        assertTrue("the chat was told", app.sessions.historyCleared.value > before)
        val c = URI("${mock!!.base}/api/messages").toURL().openConnection() as HttpURLConnection
        c.setRequestProperty("Authorization", "Bearer mock-desktop-token")
        assertTrue("the server's history is empty", c.inputStream.readBytes().decodeToString().trim() == "[]")
    }
}

/** When 마이 shows the operator row (the phone's OperatorRules.operatorFrom / ownerFrom). */
class MyOperatorRulesTest {
    private val r = chat.kelo.desktop.pages.MyOperatorRules

    @Test fun theStatusCallDecides() {
        org.junit.Assert.assertEquals(true, r.operatorFrom(null, true))
        org.junit.Assert.assertEquals(false, r.operatorFrom(null, false))
        org.junit.Assert.assertEquals(false, r.operatorFrom(403, null) { true })
        org.junit.Assert.assertNull("another error keeps the last value", r.operatorFrom(500, null) { true })
    }

    @Test fun anOlderServerFallsBackToTheFirebaseCall() {
        org.junit.Assert.assertEquals(true, r.operatorFrom(404, null) { r.ownerFrom(null) })
        org.junit.Assert.assertEquals(false, r.operatorFrom(404, null) { r.ownerFrom(403) })
        org.junit.Assert.assertNull(r.operatorFrom(404, null) { r.ownerFrom(-1) })
    }
}
