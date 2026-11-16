package chat.kelo.app.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.compositeOver
import androidx.compose.ui.graphics.luminance
import chat.kelo.app.connectors.ConnectorList
import chat.kelo.app.ui.theme.DarkKeloColors
import chat.kelo.app.ui.theme.KeloColors
import chat.kelo.app.ui.theme.LightKeloColors
import chat.kelo.app.ui.theme.TileInk
import chat.kelo.app.wallet.CardBrand
import chat.kelo.app.wallet.CardFaceRules
import chat.kelo.app.wallet.CardInk
import org.junit.Assert.assertTrue
import org.junit.Test

/** WCAG contrast of every text / icon colour the app draws on its background, in the light and the dark theme. */
class ThemeContrastTest {
    private fun ratio(a: Color, b: Color): Double {
        val la = a.luminance().toDouble()
        val lb = b.luminance().toDouble()
        return (maxOf(la, lb) + 0.05) / (minOf(la, lb) + 0.05)
    }

    private val body = 4.5
    private val large = 3.0

    private fun pairs(c: KeloColors): List<Triple<String, Pair<Color, Color>, Double>> {
        val surfaces = listOf("bg" to c.bg, "card" to c.card, "bubble" to c.bubble)
        val out = mutableListOf<Triple<String, Pair<Color, Color>, Double>>()
        // text on every surface it is drawn on
        for ((sn, s) in surfaces) {
            out += Triple("ink/$sn", c.ink to s, body)
            out += Triple("muted/$sn", c.muted to s, body)
            out += Triple("accent/$sn", c.accent to s, body)
            out += Triple("danger/$sn", c.danger to s, body)
            out += Triple("success/$sn", c.success to s, body)
            out += Triple("warn/$sn", c.warn to s, body)
            out += Triple("disabled/$sn", c.disabledInk to s, large)
        }
        out += Triple("ink/mine", c.ink to c.mine, body)
        out += Triple("muted/mine", c.muted to c.mine, large)
        // fills with their own text
        out += Triple("onAccent/accent", c.onAccent to c.accent, body)
        out += Triple("onDanger/danger", c.onDanger to c.danger, body)
        out += Triple("onSuccess/success", c.onSuccess to c.success, body)
        out += Triple("onWarn/warn", c.onWarn to c.warn, body)
        out += Triple("disabled/disabledFill", c.disabledInk to c.disabledFill, large)
        out += Triple("onMedia/media", c.onMedia to c.media, body)
        // badges and bubbles
        out += Triple("dangerInk/dangerSoft", c.dangerInk to c.dangerSoft, body)
        out += Triple("successInk/successSoft", c.successInk to c.successSoft, body)
        out += Triple("infoInk/infoSoft", c.infoInk to c.infoSoft, body)
        out += Triple("neutralInk/neutralSoft", c.neutralInk to c.neutralSoft, body)
        out += Triple("muted/bubble chip", c.muted to c.bubble, body)
        // a status chip: the tone on its own 12% tint over the card
        for ((n, tone) in listOf("accent" to c.accent, "warn" to c.warn, "danger" to c.danger, "success" to c.success, "muted" to c.muted)) {
            out += Triple("$n chip", tone to tone.copy(alpha = 0.12f).compositeOver(c.card), body)
        }
        // a proposed rule's highlighted card: ink and muted on accent 8% over the page
        val hl = c.accent.copy(alpha = 0.08f).compositeOver(c.bg)
        out += Triple("ink/rule highlight", c.ink to hl, body)
        out += Triple("muted/rule highlight", c.muted to hl, body)
        // the floating header's name pill (card at 92% over the page) and its status line
        val pill = c.card.copy(alpha = 0.92f).compositeOver(c.bg)
        out += Triple("ink/name pill", c.ink to pill, body)
        out += Triple("muted/name pill", c.muted to pill, body)
        // icons on the page (3:1)
        out += Triple("ink icon/bg", c.ink to c.bg, large)
        out += Triple("muted icon/card", c.muted to c.card, large)
        return out
    }

    private fun check(name: String, c: KeloColors) {
        val bad = pairs(c).mapNotNull { (n, p, min) ->
            val r = ratio(p.first, p.second)
            if (r + 1e-9 < min) "$n ${"%.2f".format(r)} < $min" else null
        }
        assertTrue("$name: $bad", bad.isEmpty())
    }

    @Test fun lightThemePairs() = check("light", LightKeloColors)
    @Test fun darkThemePairs() = check("dark", DarkKeloColors)

    @Test fun cardFacesKeepTheirTextReadable() {
        CardBrand.entries.forEach { b ->
            val (a, z) = CardFaceRules.palette(b)
            listOf(a, z).forEach { argb ->
                val r = ratio(CardInk, Color(argb.toInt()))
                assertTrue("$b ${"%.2f".format(r)}", r >= body)
            }
        }
    }

    @Test fun serviceTilesAndTheAvatarKeepLargeLettersReadable() {
        for (dark in listOf(false, true)) for (h in 0 until 360) {
            val r = ratio(TileInk, ConnectorList.tileColor(h.toFloat(), dark))
            assertTrue("hue $h dark=$dark ${"%.2f".format(r)}", r >= large)
        }
    }
}
