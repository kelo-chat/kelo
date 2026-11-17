package chat.kelo.app.ui.text

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.ExperimentalTestApi
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.runComposeUiTest
import androidx.compose.ui.text.AnnotatedString
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

/** The app's Text (every screen uses it, not Material's): Korean words stay whole where the platform asks for it, and nothing else changes. */
@OptIn(ExperimentalTestApi::class)
class AppTextTest {
    @Test fun `with a joiner the drawn text is joined and the semantics read as the original`() = runComposeUiTest {
        var drawn = ""
        setContent {
            CompositionLocalProvider(LocalWordJoiner provides WordJoin::join) {
                Text("로그인하면 포함", onTextLayout = { drawn = it.layoutInput.text.text })
            }
        }
        waitForIdle()
        assertTrue(drawn.contains(WordJoin.JOINER), "joiners between the syllables: $drawn")
        assertEquals("로그인하면 포함", WordJoin.strip(drawn))
        onNodeWithText("로그인하면 포함").assertExists()   // found by its original text
    }

    @Test fun `without a joiner (Android, other languages) the text is drawn as given`() = runComposeUiTest {
        var drawn = ""
        setContent {
            CompositionLocalProvider(LocalWordJoiner provides WordJoin::join) {
                Text(AnnotatedString("Sign in 123"), onTextLayout = { drawn = it.layoutInput.text.text })
            }
        }
        waitForIdle()
        assertEquals("Sign in 123", drawn)
    }

    @Test fun `the default joiner leaves Korean alone`() = runComposeUiTest {
        var drawn = ""
        setContent { Text("로그인하면", onTextLayout = { drawn = it.layoutInput.text.text }) }
        waitForIdle()
        assertEquals("로그인하면", drawn)
    }
}
