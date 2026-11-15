package chat.kelo.desktop.pages.my.ops

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.LocalStrings
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.MutedText
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.FormatStyle

/* Small pieces the operator, keychain, card and permission pages share (the phone's my/Format.kt and agent/AgentFormat.kt). */

/** "11/6 06:00" (the phone's date_time_pattern), or "아직 없어요". */
@Composable
internal fun whenText(seconds: Double?): String =
    if (seconds == null || seconds == 0.0) stringResource(R.string.vault_never) else dayTime((seconds * 1000).toLong())

@Composable
internal fun dayTime(millis: Long): String {
    val l = LocalStrings.current.locale
    val z = Instant.ofEpochMilli(millis).atZone(ZoneId.systemDefault())
    return runCatching { DateTimeFormatter.ofPattern(stringResource(R.string.date_time_pattern), l).format(z) }
        .getOrElse { DateTimeFormatter.ofLocalizedDateTime(FormatStyle.SHORT).withLocale(l).format(z) }
}

/** A loading line where the phone shows grey skeleton lines. */
@Composable
internal fun Loading() = MutedText(stringResource(R.string.loading), small = true)

/** A label and its value side by side (the label column 40%). */
@Composable
internal fun KeyLine(label: String, value: String) {
    val c = Kelo.colors
    Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(label, color = c.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(0.4f))
        Text(value.ifBlank { "—" }, color = c.ink, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(0.6f))
    }
}

/** A field's small label above it. */
@Composable
internal fun FieldLabel(text: String) {
    Text(text, color = Kelo.colors.muted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 2.dp))
}

/** Two or more choices side by side; long words wrap inside their half. */
@Composable
internal fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    val c = Kelo.colors
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(c.bubble).padding(3.dp)) {
        options.forEachIndexed { i, o ->
            val on = i == selected
            Box(
                Modifier.weight(1f).clip(RoundedCornerShape(11.dp)).background(if (on) c.card else c.bubble)
                    .clickable(role = Role.RadioButton) { onSelect(i) }.padding(horizontal = 6.dp, vertical = 10.dp),
                contentAlignment = Alignment.Center,
            ) { Text(o, color = c.ink, fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal, textAlign = TextAlign.Center) }
        }
    }
}

/** A label over a block of text (a report's reply, the question, the comment). */
@Composable
internal fun Labeled(label: String, text: String) {
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(label, color = c.muted, style = MaterialTheme.typography.bodySmall)
        Text(text, color = c.ink, style = MaterialTheme.typography.bodySmall)
    }
}
