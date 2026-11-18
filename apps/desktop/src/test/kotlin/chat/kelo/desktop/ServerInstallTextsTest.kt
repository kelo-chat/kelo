package chat.kelo.desktop

import chat.kelo.desktop.core.DesktopPlatform
import chat.kelo.desktop.pages.ServerInstallTexts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The server install page's words per system: the Mac words never say "PC", "Windows" or "computer" (the owner's wording rule: this
 * Mac, Kelo server), and every Mac key exists in the English strings and in every language.
 */
class ServerInstallTextsTest {
    private val values = File("../shared/strings/values/strings_desktop_server.xml")

    private fun english(): Map<String, String> {
        assertTrue("the English strings are at ${values.absolutePath}", values.isFile)
        return Regex("""<string name="([^"]+)">([^<]*)</string>""").findAll(values.readText())
            .associate { it.groupValues[1] to it.groupValues[2] }
    }

    @Test fun theMacWordsMapToTheMacKeys() {
        val mac = ServerInstallTexts.forOs(DesktopPlatform.Os.MAC)
        assertEquals("desktop_server_row_mac", mac.row.key)
        assertEquals("desktop_server_row_hint_mac", mac.hint.key)
        assertEquals("desktop_server_installed_mac", mac.installed.key)
        assertEquals("desktop_server_unavailable_mac", mac.unavailable.key)
        val windows = ServerInstallTexts.forOs(DesktopPlatform.Os.WINDOWS)
        assertEquals("desktop_server_row", windows.row.key)
    }

    @Test fun theMacWordsNeverSayPcWindowsOrComputer() {
        val strings = english()
        val macKeys = strings.keys.filter { it.endsWith("_mac") } + listOf("desktop_server_port_busy", "desktop_server_downgrade",
            "desktop_server_remove", "desktop_server_remove_title", "desktop_server_remove_body", "desktop_server_remove_data")
        for (key in macKeys) {
            val text = strings[key] ?: error("missing in values/: $key")
            for (word in listOf("PC", "Windows", "computer")) {
                assertFalse("$key says '$word': $text", Regex("\\b$word\\b", RegexOption.IGNORE_CASE).containsMatchIn(text))
            }
        }
    }

    @Test fun theMacKeysAreInEveryLanguage() {
        val folders = File("../shared/strings").listFiles { f -> f.isDirectory && f.name.startsWith("values") }.orEmpty()
        assertEquals("the 20 languages", 20, folders.size)
        val needed = english().keys.filter { it.endsWith("_mac") || it in MAC_SHARED }
        for (folder in folders) {
            val text = File(folder, "strings_desktop_server.xml").readText()
            for (key in needed) assertTrue("${folder.name} lacks $key", text.contains("name=\"$key\""))
        }
    }

    @Test fun theHeaderNamesBothSystems() {
        assertTrue(values.readText().contains("(Windows: this PC, Mac: this Mac)"))
    }

    private companion object {
        val MAC_SHARED = setOf("desktop_server_port_busy", "desktop_server_downgrade", "desktop_server_remove", "desktop_server_remove_title",
            "desktop_server_remove_body", "desktop_server_remove_data", "desktop_server_removing", "desktop_server_removed")
    }
}
