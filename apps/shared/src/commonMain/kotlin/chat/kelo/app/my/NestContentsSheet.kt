package chat.kelo.app.my

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import chat.kelo.app.res.*
import chat.kelo.app.ui.components.KeloModal
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.MutedText
import chat.kelo.app.ui.components.SectionLabel
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo

/*
 * What an install or an update puts on the phone, in full: the notes from loading it, where it connects, every file with its size,
 * and the text of each .md file and the manifest (tap a file). Long paths and long lines wrap inside the sheet; nothing scrolls the page.
 */

/** The preview card's "See everything it will install". */
@Composable
fun NestContentsSheet(contents: NestContents, onDismiss: () -> Unit) {
    KeloModal(onDismiss) { NestContentsBody(contents) }
}

/** The sheet's content (no sheet around it, so a test can lay it out at 320 or 412 dp). */
@Composable
fun NestContentsBody(contents: NestContents, modifier: Modifier = Modifier) {
    var opened by rememberSaveable { mutableStateOf<String?>(null) }
    val shown = opened?.takeIf { it in contents.texts }
    Column(
        modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (shown != null) {
            LinkButton(string(Res.string.nest_contents_back), onClick = { opened = null })
            Text(shown, color = Kelo.colors.ink, style = MaterialTheme.typography.titleSmall, fontFamily = FontFamily.Monospace)
            Text(contents.texts.getValue(shown), color = Kelo.colors.ink, style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace)
            if (NestContentsRules.isCut(contents, shown)) MutedText(string(Res.string.nest_contents_cut), small = true)
            return@Column
        }
        Text(string(Res.string.nest_contents_title), color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
        if (contents.warnings.isNotEmpty()) {
            SectionLabel(string(Res.string.nest_contents_warnings))
            contents.warnings.forEach { Text(it, color = Kelo.colors.danger, style = MaterialTheme.typography.bodySmall) }
        }
        SectionLabel(string(Res.string.nest_contents_endpoints))
        if (contents.endpoints.isEmpty()) MutedText(string(Res.string.nest_contents_none), small = true)
        contents.endpoints.forEach { Text(it, color = Kelo.colors.ink, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace) }
        SectionLabel(string(Res.string.nest_contents_files))
        contents.files.forEach { f ->
            val tappable = f.path in contents.texts
            Column(
                Modifier.fillMaxWidth().let { if (tappable) it.clickable { opened = f.path } else it },
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(f.path, color = Kelo.colors.ink, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
                MutedText(sizeLabel(f.bytes), small = true)
            }
        }
        MutedText(string(Res.string.nest_contents_total, sizeLabel(contents.totalBytes)), small = true)
    }
}

/** An update's "N changed files": the three lists (added, removed, changed). */
@Composable
fun NestChangedSheet(title: String, changed: NestChanged, onDismiss: () -> Unit) {
    KeloModal(onDismiss) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(string(Res.string.nest_changed_title), color = Kelo.colors.ink, style = MaterialTheme.typography.titleMedium)
            MutedText(title, small = true)
            PathList(string(Res.string.nest_changed_added), changed.added)
            PathList(string(Res.string.nest_changed_removed), changed.removed)
            PathList(string(Res.string.nest_changed_modified), changed.modified)
        }
    }
}

@Composable
private fun PathList(heading: String, paths: List<String>) {
    if (paths.isEmpty()) return
    SectionLabel(heading)
    paths.forEach { Text(it, color = Kelo.colors.ink, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace) }
}

@Composable
private fun sizeLabel(bytes: Long): String = when (val s = NestContentsRules.size(bytes)) {
    is NestSize.Bytes -> string(Res.string.nest_contents_bytes, s.count)
    is NestSize.Kilobytes -> string(Res.string.nest_contents_kb, s.count)
}
