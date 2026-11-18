package chat.kelo.desktop.pages

import chat.kelo.app.res.Res
import chat.kelo.app.res.desktop_server_installed
import chat.kelo.app.res.desktop_server_installed_mac
import chat.kelo.app.res.desktop_server_row
import chat.kelo.app.res.desktop_server_row_hint
import chat.kelo.app.res.desktop_server_row_hint_mac
import chat.kelo.app.res.desktop_server_row_mac
import chat.kelo.app.res.desktop_server_unavailable
import chat.kelo.app.res.desktop_server_unavailable_mac
import chat.kelo.desktop.core.DesktopPlatform
import org.jetbrains.compose.resources.StringResource

/** The server install page's own words per system: "this PC" on Windows, "this Mac" on a Mac (never one for the other). */
object ServerInstallTexts {
    class Texts(
        val row: StringResource,
        val hint: StringResource,
        val installed: StringResource,
        val unavailable: StringResource,
    )

    fun forOs(os: DesktopPlatform.Os): Texts = when (os) {
        DesktopPlatform.Os.MAC -> Texts(
            Res.string.desktop_server_row_mac, Res.string.desktop_server_row_hint_mac,
            Res.string.desktop_server_installed_mac, Res.string.desktop_server_unavailable_mac,
        )
        else -> Texts(
            Res.string.desktop_server_row, Res.string.desktop_server_row_hint,
            Res.string.desktop_server_installed, Res.string.desktop_server_unavailable,
        )
    }
}
