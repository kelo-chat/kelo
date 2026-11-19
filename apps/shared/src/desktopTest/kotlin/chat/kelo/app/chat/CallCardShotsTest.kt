package chat.kelo.app.chat

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.width
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toAwtImage
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.unit.dp
import chat.kelo.app.ui.theme.KeloTheme
import chat.kelo.app.ui.theme.LocalReduceMotion
import java.io.File
import javax.imageio.ImageIO
import kotlin.test.Test
import kotlin.test.assertTrue

/** The call card in the chat at phone widths, light and dark: closed, then opened by a tap (owner 2026-11-20: looked at by eye). */
@OptIn(ExperimentalTestApi::class)
class CallCardShotsTest {

    private val card = run {
        val t = 1_700_000_000_000L
        fun line(key: String, mine: Boolean, text: String, at: Long) =
            ChatItem.Msg(key, mine = mine, text = text, id = at, at = at, callId = "c1", fromCall = true)
        ChatItem.CallCard("call:c1:l1", "c1", listOf(
            line("l1", true, "나 목소리 너무 끊겨. 다시 말해볼게, 내일 오전 회의 몇 시였지?", t),
            line("l2", false, "내일 오전 회의는 10시에 시작해요.", t + 20_000),
            line("l3", true, "고마워.", t + 30_000),
            line("l4", false, "네, 잘 들려요. 필요하면 회의 전에 알려 드릴게요.", t + 190_000),
        ))
    }

    private fun captureCard(width: Int, dark: Boolean) = runComposeUiTest {
        val outDir = File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/chat-call").apply { mkdirs() }
        setContent {
            CompositionLocalProvider(LocalReduceMotion provides true) {
                KeloTheme(dark = dark) {
                    Box(Modifier.width(width.dp).testTag("call.card.shot")) {
                        var open by remember { mutableStateOf(false) }
                        CallCardRow(card, open, "Kelo", onToggle = { open = !open })
                    }
                }
            }
        }
        waitForIdle()
        onNodeWithTag("chat.call.head").assertIsDisplayed()
        onAllNodesWithTag("chat.call.lines").assertCountEquals(0)
        onNodeWithTag("chat.call.head").performClick()
        waitForIdle()
        onNodeWithTag("chat.call.lines").assertIsDisplayed()
        val boxRight = onNodeWithTag("call.card.shot").fetchSemanticsNode().boundsInRoot.right
        val cardRight = onNodeWithTag("chat.call.card").fetchSemanticsNode().boundsInRoot.right
        assertTrue(cardRight <= boxRight, "the call card is clipped at ${width}dp")
        val image = onNodeWithTag("call.card.shot").captureToImage().toAwtImage()
        val file = File(outDir, "call-card-${if (dark) "dark" else "light"}-${width}dp.png")
        ImageIO.write(image, "png", file)
        assertTrue(file.exists() && file.length() > 0, "Saved ${file.name}")
    }

    @Test fun cardLight320() = captureCard(320, dark = false)
    @Test fun cardDark320() = captureCard(320, dark = true)
    @Test fun cardLight412() = captureCard(412, dark = false)
    @Test fun cardDark412() = captureCard(412, dark = true)
}
