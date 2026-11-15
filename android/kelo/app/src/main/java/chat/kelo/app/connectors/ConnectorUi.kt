package chat.kelo.app.connectors

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import chat.kelo.app.R
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo

/** A status badge: its colours, light and dark. */
@Composable
fun StatusBadge(badge: Badge?, modifier: Modifier = Modifier) {
    if (badge == null) return
    val c = Kelo.colors
    val (bg, fg) = when (badge) {
        Badge.CONNECTED -> c.successSoft to c.successInk
        Badge.AVAILABLE -> c.infoSoft to c.infoInk
        Badge.BROWSER -> c.neutralSoft to c.neutralInk
        Badge.UNSUPPORTED -> Color.Transparent to c.muted
        Badge.DISABLED, Badge.COMING_SOON -> c.bubble to c.muted
    }
    val shape = RoundedCornerShape(99.dp)
    Text(
        stringResource(badge.label),
        modifier = modifier
            .clip(shape)
            .background(bg)
            .then(if (badge == Badge.UNSUPPORTED) Modifier.border(1.dp, c.line, shape) else Modifier)
            .padding(horizontal = 9.dp, vertical = 3.dp),
        color = fg, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp),
        maxLines = 1,
    )
}

/** `ol.steps`: numbered help lines from the server. */
@Composable
fun Steps(lines: List<String>) {
    if (lines.isEmpty()) return
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        lines.forEachIndexed { i, line ->
            Row {
                Text(stringResource(R.string.step_number, i + 1), color = c.ink, style = MaterialTheme.typography.bodySmall, modifier = Modifier.width(20.dp))
                Text(line, color = c.ink, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
            }
        }
    }
}

/** `<b><ShieldIcon /> …</b>`: a bold line with the shield in front. */
@Composable
fun ShieldTitle(text: String) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.size(18.dp))
        Spacer(Modifier.size(6.dp))
        Text(text, fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
fun BoldLine(text: String) {
    Text(text, fontWeight = FontWeight.Bold, color = Kelo.colors.ink, style = MaterialTheme.typography.bodyLarge)
}

@Composable
fun SmallText(text: String) {
    Text(text, color = Kelo.colors.ink, style = MaterialTheme.typography.bodySmall)
}

/** A question with a red action and 취소. */
@Composable
fun ConfirmDialog(text: String, action: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.card,
        text = { Text(text, color = c.ink, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = { TextButton(onClick = { onDismiss(); onConfirm() }) { Text(action, color = c.danger) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** Opens a service's own page (sign-in, verification, settings) in the user's browser. False when nothing can open it. */
fun openInBrowser(context: Context, url: String): Boolean = try {
    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE))
    true
} catch (_: ActivityNotFoundException) {
    false
} catch (_: SecurityException) {
    false
}

/**
 * Runs [load] when [key] changes and every time the app comes back to the foreground (the user signed in in the browser and came
 * back), so a panel shows the new state without being touched.
 */
@Composable
fun ReloadOnReturn(key: Any?, load: suspend () -> Unit) {
    var returns by remember { mutableIntStateOf(0) }
    val current by rememberUpdatedState(load)
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { returns++ }
    LaunchedEffect(key, returns) { current() }
}

/**
 * A service's tile: its own app icon from the server when there is one (ConnectorIcons.kt; the services' trademarks, shown only to
 * say which service a row is), else its initials on a colour made from its id.
 */
@Composable
fun ConnectorTile(x: Connector, size: androidx.compose.ui.unit.Dp) {
    ServiceIcon(x.icon, x.iconSmall, x.iconColor, size) { InitialsTile(x, size) }
}

/** The initials tile: the fallback when a service has no icon or it cannot be loaded. */
@Composable
fun InitialsTile(x: Connector, size: androidx.compose.ui.unit.Dp) {
    val c = Kelo.colors
    val hue = remember(x.id) { ConnectorList.hue(x.id) }
    val letters = remember(x.name) { ConnectorList.initials(x.name.ifBlank { x.id }) }
    val bg = ConnectorList.tileColor(hue, c.dark)
    androidx.compose.foundation.layout.Box(
        Modifier.size(size).clip(RoundedCornerShape(size * 0.28f)).background(bg),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            letters, color = chat.kelo.app.ui.theme.TileInk, fontWeight = FontWeight.Bold, maxLines = 1,
            fontSize = (size.value * if (letters.length > 1) 0.36f else 0.44f).sp,
        )
    }
}

/** The red "다시 연결 필요" badge. */
@Composable
fun ReconnectBadge(modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Text(
        stringResource(R.string.connector_status_reconnect),
        modifier = modifier.clip(RoundedCornerShape(99.dp)).background(c.danger.copy(alpha = 0.12f)).padding(horizontal = 9.dp, vertical = 3.dp),
        color = c.danger, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp), maxLines = 1,
    )
}
