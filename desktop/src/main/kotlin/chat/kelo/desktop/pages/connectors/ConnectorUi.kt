package chat.kelo.desktop.pages.connectors

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import chat.kelo.app.R
import chat.kelo.app.connectors.Badge
import chat.kelo.app.connectors.BetaRules
import chat.kelo.app.connectors.Connector
import chat.kelo.app.connectors.ConnectorCalls
import chat.kelo.app.connectors.ConnectorList
import chat.kelo.app.connectors.GoogleBetaState
import chat.kelo.app.connectors.OAuthApp
import chat.kelo.app.core.ApiError
import chat.kelo.app.ui.icons.KeloIcons
import chat.kelo.app.ui.theme.Kelo
import chat.kelo.desktop.core.AppContainer
import chat.kelo.desktop.core.Browser
import chat.kelo.desktop.i18n.stringResource
import chat.kelo.desktop.ui.Card
import chat.kelo.desktop.ui.ErrorText
import chat.kelo.desktop.ui.KeloTextField
import chat.kelo.desktop.ui.LinkButton
import chat.kelo.desktop.ui.MutedText
import chat.kelo.desktop.ui.PrimaryButton
import chat.kelo.desktop.ui.SecondaryButton
import kotlinx.coroutines.launch
import java.awt.KeyboardFocusManager
import java.beans.PropertyChangeListener

/* The connectors' small parts, as on the phone (android/kelo .../connectors/ConnectorUi.kt, GoogleReview.kt, GoogleBeta.kt). */

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
        modifier = modifier.clip(shape).background(bg)
            .then(if (badge == Badge.UNSUPPORTED) Modifier.border(1.dp, c.line, shape) else Modifier)
            .padding(horizontal = 9.dp, vertical = 3.dp),
        color = fg, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp), maxLines = 2,
    )
}

/** The red "다시 연결 필요" badge. */
@Composable
fun ReconnectBadge(modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Text(
        stringResource(R.string.connector_status_reconnect),
        modifier = modifier.clip(RoundedCornerShape(99.dp)).background(c.danger.copy(alpha = 0.12f)).padding(horizontal = 9.dp, vertical = 3.dp),
        color = c.danger, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp), maxLines = 2,
    )
}

/** The neutral "준비 중" pill of a Google row whose sign-in waits for Google's review. */
@Composable
fun ReviewPill(modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Text(
        stringResource(R.string.google_review_pill),
        modifier = modifier.clip(RoundedCornerShape(99.dp)).background(c.neutralSoft).padding(horizontal = 9.dp, vertical = 3.dp),
        color = c.neutralInk, style = MaterialTheme.typography.labelSmall.copy(fontSize = 12.sp, lineHeight = 16.sp), maxLines = 2,
    )
}

/** Numbered help lines from the server. */
@Composable
fun Steps(lines: List<String>) {
    if (lines.isEmpty()) return
    val c = Kelo.colors
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        lines.forEachIndexed { i, line ->
            Row {
                Text(stringResource(R.string.step_number, i + 1), color = c.ink, style = MaterialTheme.typography.bodySmall, modifier = Modifier.width(22.dp))
                Text(line, color = c.ink, style = MaterialTheme.typography.bodySmall, modifier = Modifier.weight(1f))
            }
        }
    }
}

/** A bold line with the shield in front. */
@Composable
fun ShieldTitle(text: String) {
    val c = Kelo.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(KeloIcons.Shield, contentDescription = null, tint = c.ink, modifier = Modifier.size(18.dp))
        Spacer(Modifier.size(6.dp))
        Text(text, fontWeight = FontWeight.Bold, color = c.ink, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f, fill = false))
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
fun ConfirmAsk(text: String, action: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    val c = Kelo.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = c.card,
        text = { Text(text, color = c.ink, style = MaterialTheme.typography.bodyMedium) },
        confirmButton = { TextButton(onClick = { onDismiss(); onConfirm() }) { Text(action, color = c.danger) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** [template] with its %1$s shown bold ([code]: in a fixed-width font). */
@Composable
fun emphasized(template: String, arg: String, code: Boolean = false): AnnotatedString {
    val i = template.indexOf("%1\$s")
    if (i < 0) return AnnotatedString(template)
    return buildAnnotatedString {
        append(template.substring(0, i))
        withStyle(SpanStyle(fontWeight = FontWeight.Bold, fontFamily = if (code) FontFamily.Monospace else null)) { append(arg) }
        append(template.substring(i + 4))
    }
}

/**
 * Runs [load] when [key] changes and every time a Kelo window becomes active again (the user signed in in the browser and came
 * back), so a panel shows the new state without being touched. The desktop's version of the phone's ON_RESUME reload.
 */
@Composable
fun ReloadOnReturn(key: Any?, load: suspend () -> Unit) {
    var returns by remember { mutableIntStateOf(0) }
    val current by rememberUpdatedState(load)
    DisposableEffect(Unit) {
        val kfm = runCatching { KeyboardFocusManager.getCurrentKeyboardFocusManager() }.getOrNull()
        val l = PropertyChangeListener { e -> if (e.newValue != null && e.oldValue == null) returns++ }
        kfm?.addPropertyChangeListener("activeWindow", l)
        onDispose { kfm?.removePropertyChangeListener("activeWindow", l) }
    }
    LaunchedEffect(key, returns) { current() }
}

/**
 * A service's tile: its own app icon from the server when there is one ([ConnectorIcons]; the services' trademarks, shown only to
 * say which service a row is), else its initials on a colour made from its id.
 */
@Composable
fun ConnectorTile(x: Connector, size: Dp) {
    ServiceIcon(x.icon, x.iconSmall, x.iconColor, size) { InitialsTile(x, size) }
}

/** The initials tile: the fallback when a service has no icon or it cannot be loaded. */
@Composable
fun InitialsTile(x: Connector, size: Dp) {
    val c = Kelo.colors
    val hue = remember(x.id) { ConnectorList.hue(x.id) }
    val letters = remember(x.name) { ConnectorList.initials(x.name.ifBlank { x.id }) }
    Box(Modifier.size(size).clip(RoundedCornerShape(size * 0.28f)).background(ConnectorList.tileColor(hue, c.dark)), contentAlignment = Alignment.Center) {
        Text(letters, color = chat.kelo.app.ui.theme.TileInk, fontWeight = FontWeight.Bold, maxLines = 1,
            fontSize = (size.value * if (letters.length > 1) 0.36f else 0.44f).sp)
    }
}

/** Opens a page in the system browser; false (and an error line) when nothing could open it. */
fun openInBrowser(url: String): Boolean = Browser.open(url)

/**
 * Why there is no Google sign-in button yet (the server's [Connector.note], else the app's words), the support link once set, and
 * the way to ask to be let in early ([BetaRequest]); [onOpened] reloads once that request is approved.
 */
@Composable
fun ReviewNote(app: AppContainer, c: Connector, onOpened: () -> Unit = {}) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        BoldLine(stringResource(R.string.google_review_title))
        MutedText(c.note.ifBlank { stringResource(R.string.google_review_note) }, small = true)
        if (c.supportUrl.startsWith("https://")) LinkButton(stringResource(R.string.google_review_support), onClick = { openInBrowser(c.supportUrl) })
        BetaRequest(app, c, onOpened)
    }
}

/** Ask to try Google sign-in while it is closed ([BetaRules]): the Google address goes to this server, "Requested" stays. */
@Composable
fun BetaRequest(app: AppContainer, c: Connector, onOpened: () -> Unit) {
    val product = BetaRules.product(c.id) ?: return
    var state by remember(c.id) { mutableStateOf<GoogleBetaState?>(null) }
    var sheet by remember(c.id) { mutableStateOf(false) }
    LaunchedEffect(c.id) { state = runCatching { ConnectorCalls.googleBeta(app.sessions.requireApi()) }.getOrNull() }
    val s = state
    when (BetaRules.shown(s)) {
        BetaRules.Shown.REQUEST -> PrimaryButton(stringResource(R.string.beta_request), onClick = { sheet = true })
        BetaRules.Shown.REQUESTED -> Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SecondaryButton(stringResource(R.string.beta_requested), onClick = {}, enabled = false)
            MutedText(stringResource(R.string.beta_sent, s?.email.orEmpty()), small = true)
        }
        BetaRules.Shown.DECLINED -> MutedText(stringResource(R.string.beta_declined), small = true)
        BetaRules.Shown.OPENED -> LaunchedEffect(Unit) { onOpened() }
        BetaRules.Shown.NOTHING -> {}
    }
    if (sheet) BetaDialog(app, product, onClose = { sheet = false }, onSent = { state = it; sheet = false })
}

@Composable
private fun BetaDialog(app: AppContainer, product: String, onClose: () -> Unit, onSent: (GoogleBetaState) -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var email by remember { mutableStateOf("") }
    var tried by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    val valid = BetaRules.email(email)
    val send: () -> Unit = {
        tried = true
        if (valid != null && !busy) {
            busy = true; err = ""
            scope.launch {
                try {
                    onSent(ConnectorCalls.requestGoogleBeta(app.sessions.requireApi(), valid, product, chat.kelo.desktop.core.ReleaseInfo.VERSION))
                } catch (e: ApiError) {
                    err = e.detail
                } finally {
                    busy = false
                }
            }
        }
    }
    AlertDialog(
        onDismissRequest = onClose, containerColor = c.card, modifier = Modifier.widthIn(max = 520.dp),
        title = { Text(stringResource(R.string.beta_request), color = c.ink) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                MutedText(stringResource(R.string.beta_sheet_note), small = true)
                KeloTextField(email, { email = it; err = "" }, stringResource(R.string.beta_email_hint), maxLength = 254)
                if (tried && valid == null) ErrorText(stringResource(R.string.beta_email_invalid), small = true)
                if (err.isNotEmpty()) ErrorText(err, small = true)
            }
        },
        confirmButton = { PrimaryButton(stringResource(if (busy) R.string.beta_sending else R.string.beta_send), enabled = !busy && email.isNotBlank(), onClick = send) },
        dismissButton = { TextButton(onClick = onClose) { Text(stringResource(R.string.cancel), color = c.ink) } },
    )
}

/** The operator's switch: open new Google sign-ins to everyone. */
@Composable
fun GoogleOpenSwitch(app: AppContainer, on: Boolean, onChanged: (OAuthApp) -> Unit) {
    val scope = rememberCoroutineScope()
    val c = Kelo.colors
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    val title = stringResource(R.string.google_review_open_switch)
    Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = c.ink)
            Text(stringResource(R.string.google_review_open_warning), style = MaterialTheme.typography.bodySmall, color = c.muted)
        }
        Switch(
            checked = on, enabled = !busy,
            onCheckedChange = { v ->
                busy = true; err = ""
                scope.launch {
                    try { onChanged(ConnectorCalls.setGoogleOpen(app.sessions.requireApi(), v)) } catch (e: ApiError) { err = e.detail } finally { busy = false }
                }
            },
            modifier = Modifier.semantics { contentDescription = title },
            colors = SwitchDefaults.colors(checkedTrackColor = c.accent, checkedThumbColor = c.onAccent),
        )
    }
    if (err.isNotEmpty()) ErrorText(err, small = true)
}

/** For the person who runs Kelo for others: the OAuth app (GitHub or Google) everyone signs in with. Hidden for other devices (403). */
@Composable
fun OperatorSettings(app: AppContainer, provider: String, onSaved: () -> Unit = {}, startOpen: Boolean = false) {
    val scope = rememberCoroutineScope()
    val github = provider == "github"
    var open by remember(provider) { mutableStateOf(startOpen) }
    var oauth by remember(provider) { mutableStateOf<OAuthApp?>(null) }
    var cid by remember(provider) { mutableStateOf("") }
    var secret by remember(provider) { mutableStateOf("") }
    var msg by remember(provider) { mutableStateOf("") }
    var owner by remember(provider) { mutableStateOf<Boolean?>(null) }
    LaunchedEffect(provider) {
        try {
            val a = ConnectorCalls.oauthApp(app.sessions.requireApi(), provider)
            oauth = a; cid = a.clientId; owner = true
        } catch (e: ApiError) {
            owner = if (e.status == 403) false else null
        }
    }
    if (owner == false) return
    if (!open) {
        if (owner == true) LinkButton(stringResource(R.string.operator_open), onClick = { open = true })
        return
    }
    val a = oauth ?: return
    val secretHint = stringResource(if (github) R.string.operator_secret_github else R.string.operator_secret_google)
    Card(Modifier.padding(top = 6.dp)) {
        BoldLine(stringResource(if (github) R.string.operator_title_github else R.string.operator_title_google))
        MutedText(stringResource(R.string.operator_note), small = true)
        if (a.callback.isNotBlank()) {
            Text(emphasized(stringResource(if (github) R.string.operator_callback_github else R.string.operator_callback_google), a.callback, code = true),
                style = MaterialTheme.typography.bodySmall, color = Kelo.colors.ink)
        }
        KeloTextField(cid, { cid = it }, stringResource(R.string.client_id), maxLength = 100)
        KeloTextField(secret, { secret = it }, if (a.hasSecret) stringResource(R.string.operator_secret_saved, secretHint) else secretHint, password = true, maxLength = 200)
        val savedText = stringResource(R.string.operator_saved)
        PrimaryButton(stringResource(R.string.save), enabled = cid.isNotBlank(), onClick = {
            msg = ""
            scope.launch {
                try {
                    val saved = ConnectorCalls.saveOauthApp(app.sessions.requireApi(), provider, cid.trim(), secret.trim())
                    secret = ""; oauth = saved; cid = saved.clientId; msg = savedText
                    onSaved()
                } catch (e: ApiError) { msg = e.detail }
            }
        })
        MutedText(stringResource(if (a.isBroker) R.string.operator_broker_on else R.string.operator_broker_off), small = true)
        if (!github) GoogleOpenSwitch(app, a.openToAll, onChanged = { oauth = it })
        if (msg.isNotEmpty()) SmallText(msg)
    }
}
