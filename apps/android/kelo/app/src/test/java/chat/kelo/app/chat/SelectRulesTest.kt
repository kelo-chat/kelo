package chat.kelo.app.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class SelectRulesTest {
    private val none = SelectOption("", "서버 기본값", keep = true)
    private val providers = listOf(
        none,
        SelectOption("zai", "Z.ai (설정 필요)", sortKey = "Z.ai"),
        SelectOption("openai", "OpenAI ✓", rank = 0, sortKey = "OpenAI"),
        SelectOption("anthropic", "Anthropic (설정 필요)", sortKey = "Anthropic"),
        SelectOption("gemini", "Gemini ✓", rank = 0, sortKey = "Gemini"),
        SelectOption("deepseek", "deepseek · 기본 모델로 쓰는 중", enabled = false, sortKey = "deepseek"),
    )

    @Test fun readyFirstThenTheRestByName() {
        assertEquals(
            listOf("", "gemini", "openai", "anthropic", "deepseek", "zai"),
            SelectRules.arrange(providers, "", sorted = true).map { it.value },
        )
        // unsorted lists (a provider's models) keep the server's order
        assertEquals(providers.map { it.value }, SelectRules.arrange(providers, "", sorted = false).map { it.value })
    }

    @Test fun searchMatchesNameOrValueAndKeepsTheFixedOnes() {
        assertEquals(listOf("", "openai"), SelectRules.arrange(providers, " OPEN", sorted = true).map { it.value })
        assertEquals(listOf("", "zai"), SelectRules.arrange(providers, "zai", sorted = true).map { it.value })
        // a disabled option is still found (and shown greyed out)
        assertEquals(listOf("", "deepseek"), SelectRules.arrange(providers, "deep", sorted = true).map { it.value })
        val nothing = SelectRules.arrange(providers, "qqq", sorted = true)
        assertEquals(listOf(""), nothing.map { it.value })
        assertTrue(SelectRules.nothingFound(nothing, "qqq"))
        assertFalse(SelectRules.nothingFound(SelectRules.arrange(providers, "", sorted = true), ""))
    }

    @Test fun longListsOpenASheet() {
        assertFalse(SelectRules.useSheet(10))
        assertTrue(SelectRules.useSheet(11))
        assertTrue(SelectRules.useSheet(40))
    }
}
