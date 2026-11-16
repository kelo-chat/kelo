package chat.kelo.desktop

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onLast
import androidx.compose.ui.test.performSemanticsAction
import androidx.compose.ui.unit.dp
import chat.kelo.app.my.KeloFilesPage
import chat.kelo.app.res.*
import chat.kelo.desktop.ui.KeloFrame
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * 마이 → Kelo의 파일, against tools/mock_server.py (mock_parts/skills.py): the "Skills Kelo made" row is the first row with its count,
 * opens the list (name, description, no built-in skill), a skill opens read only with the "tell Kelo in the chat" line, deleting it
 * (the confirm dialog) goes back to Kelo's files and the row is gone because it was the last one.
 */
class KeloFilesSkillsTest {
    @get:Rule val compose = createComposeRule()
    @get:Rule val tmp = TemporaryFolder()
    private var mock: MockServer? = null

    @After fun stop() { mock?.stop() }

    @Test fun english() = flow("en")
    @Test fun korean() = flow("ko")

    private fun flow(lang: String) {
        val words = Words.of(lang)
        val m = MockServer("--lang", if (lang == "ko") "ko" else "en").also { mock = it }
        val app = m.app(tmp.newFolder(), lang = lang)
        val ui = UiSteps(compose, parityShots())
        compose.mainClock.autoAdvance = false
        compose.setContent { KeloFrame(app) { Box(Modifier.size(900.dp, 900.dp)) { KeloFilesPage(onBack = {}) } } }
        fun click(text: String) = compose.onAllNodes(hasText(text) and hasClickAction()).onFirst().performSemanticsAction(SemanticsActions.OnClick)
        val title = words.get(Res.string.files_skills_title)
        try {
            ui.waitText(title)                                           // the row, next to Kelo's own files
            ui.waitText(words.plural(Res.plurals.files_skills_count, 1, 1))
            ui.waitText("user.md")
            click(title)
            ui.waitText(if (lang == "ko") "주간 장보기 목록" else "Weekly grocery list")
            assertFalse("a built-in skill is never listed", ui.has("Research a question."))
            click(if (lang == "ko") "주간 장보기 목록" else "Weekly grocery list")
            ui.waitText(words.get(Res.string.files_skills_hint))        // read only: the chat is where a skill changes
            ui.waitText(if (lang == "ko") "지난주 목록을 열어요." else "Open last week's list.")
            click(words.get(Res.string.files_delete))
            ui.waitText(words.get(Res.string.files_skill_delete_q))
            compose.onAllNodes(hasText(words.get(Res.string.files_delete)) and hasClickAction()).onLast().performSemanticsAction(SemanticsActions.OnClick)   // the dialog's button
            ui.waitUntil("back in Kelo's files without the row") { ui.has("user.md") && !ui.has(title) }
        } catch (e: Throwable) {
            runCatching { ui.shot("keloskills-zz-failed-$lang") }
            throw e
        }
    }
}
