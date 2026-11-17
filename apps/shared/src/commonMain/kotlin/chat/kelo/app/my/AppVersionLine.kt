package chat.kelo.app.my

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import chat.kelo.app.ui.text.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import chat.kelo.app.core.SourceLink
import chat.kelo.app.res.*
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/**
 * The version lines at the bottom of My: "App version: 0.10.2 · android-0.10.2" and "Server version: 0.10.2 · release-0.10.2". The tag is the
 * release the build came from: a link to that release's source on GitHub for a clean build ([openUrl] opens it in the system browser),
 * plain "android-0.10.2 (modified)" for a build from changed files, and nothing when the build has no valid tag ([SourceLink],
 * docs/versioning.md). The server's tag links the same way (the server's source repository).
 */
@Composable
fun AppVersionLine(
    appVersion: String, serverVersion: String?, sourceTag: String?, sourceDirty: Boolean, openUrl: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val c = Kelo.colors
    val style = MaterialTheme.typography.bodySmall
    val app = appVersion.takeIf { it.isNotEmpty() }
    val server = serverVersion?.takeIf { it.isNotEmpty() }
    if (app == null && server == null) return
    val label = SourceLink.label(sourceTag, sourceDirty, string(Res.string.my_source_modified))
    val url = SourceLink.url(sourceTag, sourceDirty)
    val serverTag = server?.let { SourceLink.serverTag(it) }
    val serverUrl = server?.let { SourceLink.serverUrl(it) }
    Column(modifier) {
        if (app != null) VersionRow(string(Res.string.my_version, app), label, url, openUrl)
        if (server != null) VersionRow(string(Res.string.my_server_version, server), serverTag, serverUrl, openUrl)
    }
}

/** "text · label": the label is a link when [url] is given, else plain muted text; no label, no dot. */
@Composable
private fun VersionRow(text: String, label: String?, url: String?, openUrl: (String) -> Unit) {
    val c = Kelo.colors
    val style = MaterialTheme.typography.bodySmall
    Row {
        Text(text + (if (label != null) " · " else ""), color = c.muted, style = style)
        if (label != null) {
            if (url != null) {
                val description = string(Res.string.my_source_link)
                Text(
                    label, color = c.accent, style = style, textDecoration = TextDecoration.Underline,
                    modifier = Modifier
                        .clickable(onClickLabel = description, role = Role.Button) { openUrl(url) }
                        .semantics { contentDescription = description },
                )
            } else {
                Text(label, color = c.muted, style = style)
            }
        }
    }
}
