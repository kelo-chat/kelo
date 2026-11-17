package chat.kelo.app.agent

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
import chat.kelo.app.my.ForgottenTopicRow
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.app.ui.theme.KeloTheme
import java.io.File
import javax.imageio.ImageIO
import kotlin.test.Test
import kotlin.test.assertTrue

/** The new rows and the card summary at 320 and 412 dp with long German-like words and spaceless Japanese text: nothing is wider than the screen. */
@OptIn(ExperimentalTestApi::class)
class PaymentCoverLayoutTest {
    private val longWord = "Donaudampfschifffahrtsgesellschaftskapitänsmützenhersteller"
    private val japanese = "とても長い名前のスケジュールとても長い名前のスケジュールとても長い名前のスケジュール"

    private val cover = PaymentCover(
        true,
        listOf(
            Preapproval(id = "op_1", ownerKind = "schedule", ownerName = japanese, intent = japanese, items = listOf(PreapprovalItem(longWord, 2)),
                maxTotal = 1234567.89, currency = "KRW", expiresAt = 1794744000L),
            Preapproval(id = "op_2", ownerKind = "task", intent = "Running shoes, size 42, any colour but red, delivered before Friday please",
                maxTotal = 120.0, currency = "EUR", expiresAt = 1794000000L),
        ),
        listOf(
            PayAllow("sa_1", longWord, "https://$longWord.example.com", "Visa ••4242 $longWord", "EUR"),
            PayAllow("sa_2", japanese, "https://shop.example", "Visa", "KRW"),
        ),
    )

    private val ask = PreapprovalAsk(
        intent = "$longWord and $japanese", items = listOf(PreapprovalItem("$longWord $japanese", 3), PreapprovalItem("Socks", 1)),
        requirements = listOf("$japanese $longWord"), shipping = "Home, $longWord", merchantPolicy = "Any shop that meets the requirements $longWord",
        delegatedChoices = listOf(longWord), maxTotal = 240.5, currency = "EUR", maxTotalText = "240.50 EUR", expiresAt = 1794744000L, ownerKind = "schedule",
    )

    private fun check(name: String, content: @Composable () -> Unit) {
        for (w in listOf(320, 412)) runComposeUiTest {
            setContent { KeloTheme(dark = false) { Column(Modifier.width(w.dp).background(Kelo.colors.bg).padding(horizontal = 16.dp).testTag("root")) { content() } } }
            val bounds = onNodeWithTag("root").getBoundsInRoot()
            val shown = bounds.right - bounds.left
            assertTrue(shown <= w.dp, "$name at $w dp is $shown wide")
            val dir = File(System.getenv("KELO_SHOTS_DIR") ?: "build/screens/payment-cover").apply { mkdirs() }
            ImageIO.write(onNodeWithTag("root").captureToImage().toAwtImage(), "png", File(dir, "$name-$w.png"))
        }
    }

    @Test fun thePermissionsSectionFitsNarrowScreens() = check("section") {
        PaymentCoverSection(cover, onSwitch = {}, onRemoveOrder = {}, onRemoveAllow = {})
    }

    @Test fun theSectionWhenSwitchedOffAndWhenEmpty() = check("section-off") {
        PaymentCoverSection(cover.copy(enabled = false), onSwitch = {}, onRemoveOrder = {}, onRemoveAllow = {})
        PaymentCoverSection(PaymentCover(true), onSwitch = {}, onRemoveOrder = {}, onRemoveAllow = {})
    }

    @Test fun theCardSummaryFitsNarrowScreens() = check("card") { PreapprovalFacts(ask) }

    @Test fun aForgottenTopicRowFitsNarrowScreens() = check("forget") {
        ForgottenTopicRow(ForgottenTopic("f1", "$longWord $japanese", 1794000000.0, mapOf("memory" to 12, "notes" to 3, "reminders" to 1, "skills" to 2, "history" to 40)), false, {})
    }
}
