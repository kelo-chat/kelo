package chat.kelo.desktop

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.unit.dp
import chat.kelo.app.my.AndroidDeviceContent
import chat.kelo.app.my.AndroidDeviceInfo
import chat.kelo.app.my.AndroidDeviceUiState
import chat.kelo.app.my.AndroidScreen
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** My → 휴대폰 기기 on the desktop: the status card, the two check lines, the buttons and the .env note. */
class AndroidDeviceRenderTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null

    @After fun stop() { mock?.stop() }

    private class Taps { var check = 0; var screen = 0; var readme = 0 }

    private fun render(lang: String, ui: AndroidDeviceUiState, taps: Taps) {
        val app = MockServer("--lang", lang).also { mock = it }.app(tmp.newFolder(), lang = lang)
        compose.mainClock.autoAdvance = false
        compose.setContent {
            KeloFrame(app) {
                Column(Modifier.fillMaxSize().padding(16.dp)) {
                    AndroidDeviceContent(ui, onCheck = { taps.check++ }, onOpenScreen = { taps.screen++ }, onOpenReadme = { taps.readme++ })
                }
            }
        }
    }

    private val connected = AndroidDeviceInfo(
        configured = true, connected = true, model = "Pixel 7", androidVersion = "15", screen = AndroidScreen(720, 1280),
        playStore = true, koreanInput = false, checkedAt = 1.0,
    )

    @Test fun aConnectedDeviceShowsItsFactsAndOpensTheScreenInKorean() {
        val taps = Taps()
        render("ko", AndroidDeviceUiState(info = connected), taps)
        val ui = UiSteps(compose, parityShots())
        ui.waitText("연결됨")
        compose.onNodeWithText("Pixel 7").assertExists()
        compose.onNodeWithText("Android 15 · 720 × 1280").assertExists()
        compose.onNodeWithText("Play 스토어").assertExists()
        compose.onNodeWithText("✓").assertExists()           // Play Store is there
        compose.onNodeWithText("한글 입력").assertExists()
        compose.onNodeWithText("없음").assertExists()         // Korean input is not
        compose.onNodeWithText("기기 주소는 서버의 .env에서 바꿔요.", substring = true).assertExists()
        compose.onNodeWithText("연결 확인").assertIsEnabled().performClick()
        compose.onNodeWithText("화면 보기").assertIsEnabled().performClick()
        compose.onNodeWithText("README 열기").performClick()
        assertEquals(listOf(1, 1, 1), listOf(taps.check, taps.screen, taps.readme))
    }

    @Test fun aDeviceThatIsNotConnectedHasNoFactsAndCannotBeViewed() {
        val taps = Taps()
        render("en", AndroidDeviceUiState(info = AndroidDeviceInfo(configured = true)), taps)
        UiSteps(compose, parityShots()).waitText("Not connected")
        compose.onNodeWithText("Play Store").assertDoesNotExist()
        compose.onNodeWithText("View screen").assertIsNotEnabled()
        compose.onNodeWithText("Check connection").assertIsEnabled()
        compose.onNodeWithText("The device address is set in the server's .env.", substring = true).assertExists()
    }

    @Test fun theCheckButtonIsDisabledWhileACheckRuns() {
        render("en", AndroidDeviceUiState(info = connected, checking = true), Taps())
        UiSteps(compose, parityShots()).waitText("Connected")
        compose.onNodeWithText("Check connection").assertIsNotEnabled()
    }
}
