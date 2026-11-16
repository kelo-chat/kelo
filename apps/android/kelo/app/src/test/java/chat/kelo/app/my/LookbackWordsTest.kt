package chat.kelo.app.my

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The suggested-rule buttons name the action plainly in every language ("Keep this rule" / "Discard"), never a yes / no that could be
 * read either way, and the old "That's right" / "Not really" pair and the sureness words are gone.
 */
class LookbackWordsTest {
    private val res = File("../../../shared/strings")   // the text of all apps (the unit tests run in apps/android/kelo/app)

    private fun value(dir: String, name: String): String? =
        Regex("""<string name="$name"[^>]*>(.*?)</string>""").find(File(res, "$dir/strings.xml").readText())?.groupValues?.get(1)

    private val dirs = listOf("values", "values-ko", "values-ja", "values-b+zh+Hans", "values-b+zh+Hant", "values-es", "values-fr", "values-de",
        "values-pt", "values-it", "values-ru", "values-in", "values-vi", "values-th", "values-tr", "values-ar", "values-hi", "values-pl",
        "values-nl", "values-uk")

    @Test fun `keep and discard in every language, ko and en as agreed`() {
        assertEquals("Keep this rule", value("values", "lookback_rule_keep"))
        assertEquals("Discard", value("values", "lookback_rule_discard"))
        assertEquals("이 규칙 지키기", value("values-ko", "lookback_rule_keep"))
        assertEquals("버리기", value("values-ko", "lookback_rule_discard"))
        assertEquals("지킬게요", value("values-ko", "lookback_rule_kept"))
        assertEquals("버렸어요", value("values-ko", "lookback_rule_discarded"))
        val yesNo = Regex("""^(yes|no|ok|네|아니요|아니에요|맞아요|はい|いいえ|是|否|sí|oui|non|ja|nein|sim|não|да|нет)$""", RegexOption.IGNORE_CASE)
        for (d in dirs) {
            val keep = value(d, "lookback_rule_keep")
            val discard = value(d, "lookback_rule_discard")
            assertTrue("$d keep", !keep.isNullOrBlank() && !yesNo.matches(keep))
            assertTrue("$d discard", !discard.isNullOrBlank() && !yesNo.matches(discard))
            assertNotEquals(d, keep, discard)
            for (gone in listOf("lookback_rule_accept", "lookback_rule_reject", "lookback_sure_medium", "lookback_last_run")) {
                assertEquals("$d $gone", null, value(d, gone))
            }
        }
    }

    @Test fun `the suggestions heading names what they are`() {
        val text = File(res, "values-ko/strings.xml").readText()
        assertTrue(text.contains("새 규칙 제안 · %d개") && !text.contains("확인해 주세요 · %d개"))
        assertTrue(File(res, "values/strings.xml").readText().contains("Suggested rules · %d"))
    }
}
