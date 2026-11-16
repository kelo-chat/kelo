package chat.kelo.app.ui.components

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * A field that holds one value (an address, a key, a token, a model id, a code, an e-mail, a name, a number) never wraps: it is one
 * line that scrolls sideways, its hint is one line, and in a narrow column its button goes under it (FieldWithAction). Every field
 * of the app's screens is checked here (FieldWrapScan; the self-test of the scan and the field components are checked in apps/shared/), so a new screen that breaks this fails the unit tests.
 */
class SingleLineFieldsTest {
    private val root = listOf(File("src/main/java"), File("app/src/main/java")).first { it.isDirectory }

    @Test fun noOneValueFieldCanWrap() {
        // the components are in apps/shared/ (checked by its own SingleLineFieldsTest)
        val problems = FieldWrapScan.scan(listOf(root), emptyList())
        assertTrue("one-value fields that can wrap or are squeezed beside a button:\n" + problems.joinToString("\n"), problems.isEmpty())
    }
}
