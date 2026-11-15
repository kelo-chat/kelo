package chat.kelo.desktop

import chat.kelo.app.R
import chat.kelo.app.RNames
import chat.kelo.app.core.Languages
import chat.kelo.desktop.i18n.PluralRules
import chat.kelo.desktop.i18n.Strings
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The desktop text is in step with the Android strings and complete in every language (tools/sync_strings.py). */
class StringsTest {
    private fun table(code: String): JsonObject =
        Json.parseToJsonElement(javaClass.getResourceAsStream("/i18n/$code.json")!!.readBytes().toString(Charsets.UTF_8)) as JsonObject

    @Test fun everyLanguageHasItsFile() {
        Languages.ALL.forEach { assertNotNull("i18n/${it.code}.json", javaClass.getResource("/i18n/${it.code}.json")) }
    }

    @Test fun everyNameInREsistsInEnglish() {
        val s = table("en")["s"] as JsonObject
        val missing = RNames.string.filter { it !in s }
        assertTrue("R.kt and en.json differ (run tools/sync_strings.py): $missing", missing.isEmpty())
    }

    @Test fun desktopLinesAreTranslatedEverywhere() {
        val desktop = RNames.string.filter { it.startsWith("desktop_") }
        assertTrue(desktop.size >= 20)
        Languages.ALL.forEach { l ->
            val s = table(l.code)["s"] as JsonObject
            val missing = desktop.filter { it !in s }
            assertTrue("${l.code} lacks $missing", missing.isEmpty())
        }
    }

    /** The desktop's own lines (the Android ones are checked by android/tools/strings_check.py, Korean particles and all). */
    @Test fun placeholdersMatchEnglish() {
        val ph = Regex("%(\\d+\\$)?[sd]")
        val en = table("en")["s"] as JsonObject
        val bad = mutableListOf<String>()
        Languages.ALL.filter { it.code != "en" }.forEach { l ->
            val s = table(l.code)["s"] as JsonObject
            s.forEach { (k, v) ->
                if (!k.startsWith("desktop_")) return@forEach
                val e = (en[k] as? JsonPrimitive)?.content ?: return@forEach
                if (ph.findAll(e).map { it.value }.toSet() != ph.findAll((v as JsonPrimitive).content).map { it.value }.toSet()) bad += "${l.code}:$k"
            }
        }
        assertTrue("placeholders differ: ${bad.take(20)}", bad.isEmpty())
    }

    @Test fun formatsAndFallsBack() {
        val ko = Strings.of("ko")
        assertEquals("Kelo 데스크톱 1.0.0", ko.get(R.string.desktop_version, "1.0.0"))
        assertEquals("Kelo", ko.get(R.string.app_name))   // untranslatable: from English
        assertEquals("en", Strings.of("xx").code)
        assertTrue(Strings.of("en").get(R.string.connect_confirm_body).contains("server's"))   // aapt escapes undone
    }

    @Test fun pluralCategories() {
        assertEquals("one", PluralRules.category("en", 1))
        assertEquals("other", PluralRules.category("en", 2))
        assertEquals("other", PluralRules.category("ko", 1))
        assertEquals("few", PluralRules.category("ru", 3))
        assertEquals("many", PluralRules.category("ru", 11))
        assertEquals("one", PluralRules.category("ru", 21))
        assertEquals("few", PluralRules.category("pl", 22))
        assertEquals("many", PluralRules.category("pl", 12))
        assertEquals("two", PluralRules.category("ar", 2))
        assertEquals("many", PluralRules.category("ar", 11))
        assertEquals("one", PluralRules.category("fr", 0))
        assertEquals("one", PluralRules.category("pt-BR", 0))
        assertEquals("other", PluralRules.category("es", 0))
    }

    @Test fun pluralsPickTheirForm() {
        val en = Strings.of("en")
        val one = en.plural(R.plurals.work_badge_waiting, 1, 1)
        val many = en.plural(R.plurals.work_badge_waiting, 5, 5)
        assertTrue(one.contains("1") && many.contains("5") && one != many.replace("5", "1"))
    }
}
