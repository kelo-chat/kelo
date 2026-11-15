package chat.kelo.desktop.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.key.Key
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.key
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.ui.components.TokenText
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.i18n.pluralStringResource
import chat.kelo.desktop.i18n.stringResource

/*
 * The building blocks of 마이 and the other pages with sub pages, as on the phone (android/kelo .../my/Rows.kt): a heading above a
 * group, rows inside a group, cards, and a sub page with a back arrow (Escape steps out too). Sized for a window: a sub page is
 * at most [SUB_PAGE_WIDTH] wide and centred.
 */

const val SUB_PAGE_WIDTH = 760

/** A group's heading, in the accent colour. */
@Composable
fun SectionLabel(text: String) {
    Text(
        text, color = Kelo.colors.accent, style = MaterialTheme.typography.labelLarge.copy(fontSize = 14.sp),
        modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 20.dp, bottom = 4.dp).semantics { heading() },
    )
}

/** Collects the rows of a [MenuGroup]. */
class GroupScope {
    internal val rows = mutableListOf<@Composable () -> Unit>()
    fun row(content: @Composable () -> Unit) {
        rows += content
    }
}

/** Rows one under the other (no card around them). */
@Composable
fun MenuGroup(build: GroupScope.() -> Unit) {
    val rows = GroupScope().apply(build).rows
    if (rows.isEmpty()) return
    Column(Modifier.fillMaxWidth()) { rows.forEach { it() } }
}

/** A settings row: an icon, the name, and under it the current value. A danger row is red; [badge] is a count in a red pill. */
@Composable
fun MenuRow(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    value: String? = null,
    valueColor: Color? = null,
    danger: Boolean = false,
    badge: Int? = null,
) {
    val c = Kelo.colors
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable(role = Role.Button, onClick = onClick)
            .heightIn(min = 56.dp).padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Icon(icon, contentDescription = null, tint = if (danger) c.danger else c.muted, modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f)) {
            Text(
                TokenText.fit(label), color = if (danger) c.danger else c.ink, style = MaterialTheme.typography.bodyLarge,
                maxLines = if (TokenText.isToken(label)) 1 else 2, overflow = TextOverflow.Ellipsis,
            )
            if (!value.isNullOrEmpty()) {
                Text(TokenText.fit(value), color = valueColor ?: c.muted, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
        }
        if (badge != null && badge > 0) {
            val desc = pluralStringResource(R.plurals.count_new, badge, badge)
            Text(
                // a quiet pill: new things to look at, nothing to act on (red is kept for what waits for the owner: WorkBadge)
                if (badge > 99) stringResource(R.string.count_badge_max) else badge.toString(), color = c.ink, fontSize = 12.sp, fontWeight = FontWeight.Bold,
                modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(c.mine).padding(horizontal = 7.dp, vertical = 1.dp)
                    .semantics { contentDescription = desc },
            )
        }
    }
}

/** A card on a page: rounded 12dp corners and a hairline. */
@Composable
fun MyCard(modifier: Modifier = Modifier, onClick: (() -> Unit)? = null, content: @Composable ColumnScope.() -> Unit) {
    val c = Kelo.colors
    Column(
        modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(c.card).border(1.dp, c.line, RoundedCornerShape(12.dp))
            .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClick = onClick) else Modifier)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
        content = content,
    )
}

/** One block inside a card; blocks after the first get a hairline above. */
@Composable
fun KeyBox(first: Boolean = true, content: @Composable ColumnScope.() -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (!first) HorizontalDivider(color = Kelo.colors.line, modifier = Modifier.padding(bottom = 4.dp))
        content()
    }
}

/** A muted centred line for an empty list. */
@Composable
fun EmptyNote(text: String) {
    Text(text, color = Kelo.colors.muted, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth().padding(top = 24.dp))
}

/** The back arrow and a title (a sub page's top). */
@Composable
fun BackTitleBar(title: String, onBack: () -> Unit, modifier: Modifier = Modifier, actions: @Composable RowScope.() -> Unit = {}) {
    val c = Kelo.colors
    Row(modifier.fillMaxWidth().heightIn(min = 56.dp).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(onClick = onBack) { Icon(KeloIcons.ArrowLeft, contentDescription = stringResource(R.string.back), tint = c.ink) }
        // a long title wraps to a second line rather than being cut (German, Thai)
        Text(title, modifier = Modifier.weight(1f).padding(start = 4.dp, end = 8.dp), style = MaterialTheme.typography.titleLarge, color = c.ink,
            maxLines = 2, overflow = TextOverflow.Ellipsis)
        actions()
    }
}

/**
 * A sub page: the back arrow and title on top and the content scrolling under it, below the floating header ([topInset]),
 * centred and at most [maxWidth] wide. Escape steps back as the arrow does.
 */
@Composable
fun SubPage(
    title: String, onBack: () -> Unit, topInset: Dp = HeaderHeight, maxWidth: Int = SUB_PAGE_WIDTH,
    actions: @Composable RowScope.() -> Unit = {},
    content: @Composable ColumnScope.() -> Unit,
) {
    val c = Kelo.colors
    Box(
        Modifier.fillMaxSize().background(c.bg).onPreviewKeyEvent { e ->
            if (e.type == KeyEventType.KeyDown && e.key == Key.Escape) { onBack(); true } else false
        },
        contentAlignment = Alignment.TopCenter,
    ) {
        Column(Modifier.widthIn(max = maxWidth.dp).fillMaxWidth().padding(top = topInset)) {
            BackTitleBar(title, onBack, actions = actions)
            Column(
                Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 24.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                content = content,
            )
        }
    }
}

/** A yes/no question. [danger] colours the yes button red. */
@Composable
fun ConfirmDialog(text: String, yes: String, onYes: () -> Unit, onDismiss: () -> Unit, danger: Boolean = true, title: String? = null) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.card,
        title = title?.let { { Text(it, color = c.ink) } },
        text = { Text(text, color = c.ink) },
        confirmButton = { TextButton(onClick = { onDismiss(); onYes() }) { Text(yes, color = if (danger) c.danger else c.accent) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** A small rounded word (red when [denied]). */
@Composable
fun Badge(text: String, denied: Boolean = false) {
    val c = Kelo.colors
    val (bg, fg) = if (!denied) c.bubble to c.muted else c.dangerSoft to c.dangerInk
    Text(text, color = fg, fontSize = 12.sp, maxLines = 1, modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(bg).padding(horizontal = 9.dp, vertical = 3.dp))
}

/** A small tinted label ("지금 쓰는 모델", "이 기기"). */
@Composable
fun Tag(text: String) {
    val c = Kelo.colors
    Text(text, color = c.ink, fontSize = 11.sp, maxLines = 1, modifier = Modifier.clip(RoundedCornerShape(99.dp)).background(c.mine).padding(horizontal = 8.dp, vertical = 2.dp))
}

/** A thin track with a filled part; red when [hot]. */
@Composable
fun Bar(fraction: Float, hot: Boolean) {
    val c = Kelo.colors
    Box(Modifier.fillMaxWidth().padding(vertical = 2.dp).height(8.dp).clip(RoundedCornerShape(99.dp)).background(c.bubble)) {
        Box(Modifier.fillMaxWidth(fraction.coerceIn(0f, 1f)).height(8.dp).clip(RoundedCornerShape(99.dp)).background(if (hot) c.danger else c.accent))
    }
}

/** A bold line with an icon in front. */
@Composable
fun IconTitle(icon: ImageVector, text: String, modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Icon(icon, contentDescription = null, tint = Kelo.colors.ink, modifier = Modifier.size(20.dp))
        Text(text, fontWeight = FontWeight.Bold, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyLarge)
    }
}
