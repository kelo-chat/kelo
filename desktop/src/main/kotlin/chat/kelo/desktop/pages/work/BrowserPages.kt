// Ported from android/kelo .../agent/BrowserPages.kt (keep in step with it).
package chat.kelo.desktop.pages.work

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import chat.kelo.app.R
import chat.kelo.app.agent.*
import chat.kelo.app.work.*
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import chat.kelo.desktop.ui.ChoiceChip
import chat.kelo.desktop.ui.ConfirmDialog
import chat.kelo.desktop.ui.EmptyNote
import chat.kelo.desktop.ui.Badge
import chat.kelo.desktop.ui.Tag
import androidx.compose.runtime.collectAsState
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@Composable
internal fun TabRow(t: BrowserTab, onClick: () -> Unit) {
    val c = Kelo.colors
    val thumb = remember(t.thumbnail) { t.thumbnail?.let(BrowserImage::decode) }
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(Modifier.size(width = 72.dp, height = 48.dp).clip(RoundedCornerShape(8.dp)).background(c.bubble), contentAlignment = Alignment.Center) {
            if (thumb != null) Image(thumb, contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
            else Icon(KeloIcons.Globe, contentDescription = null, tint = c.muted)
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(t.title.ifBlank { t.host.ifBlank { tabLabel(t) } }, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium, color = c.ink,
                maxLines = 2, overflow = TextOverflow.Ellipsis)
            if (t.host.isNotBlank()) Text(t.host, style = MaterialTheme.typography.bodySmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TabStatus(t)
                Text(tabLabel(t), style = MaterialTheme.typography.labelSmall, color = c.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** Whose tab it is, in the app's words (a phone's tab carries the phone's own name). */
@Composable
private fun tabLabel(t: BrowserTab): String = when (t.kind) {
    "owner" -> stringResource(R.string.agent_tab_kind_owner)
    "schedule" -> stringResource(R.string.agent_tab_kind_schedule)
    "task" -> stringResource(R.string.agent_tab_kind_task)
    else -> t.label
}

@Composable
private fun TabStatus(t: BrowserTab) {
    val c = Kelo.colors
    val (word, color) = when (t.status) {
        "working" -> stringResource(R.string.agent_tab_working) to c.accent
        "failed" -> stringResource(R.string.agent_tab_failed) to c.danger
        else -> stringResource(R.string.agent_tab_idle) to c.muted
    }
    Text(
        word, style = MaterialTheme.typography.labelSmall, color = color, fontWeight = FontWeight.SemiBold,
        modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(color.copy(alpha = 0.12f)).padding(horizontal = 7.dp, vertical = 2.dp),
    )
}
