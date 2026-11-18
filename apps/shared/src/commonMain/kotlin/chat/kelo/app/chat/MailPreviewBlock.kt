package chat.kelo.app.chat

import androidx.compose.foundation.border
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.text.TextStyle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import chat.kelo.app.LocalAppServices
import chat.kelo.app.chat.MailPreviewStore.State
import chat.kelo.app.res.Res
import chat.kelo.app.res.mail_preview_desc
import chat.kelo.app.res.mail_preview_failed
import chat.kelo.app.res.mail_preview_gone
import chat.kelo.app.res.mail_preview_link_open
import chat.kelo.app.res.mail_preview_link_title
import chat.kelo.app.res.mail_preview_loading
import chat.kelo.app.res.mail_preview_open
import chat.kelo.app.res.mail_preview_pictures_note
import chat.kelo.app.res.mail_preview_simple_note
import chat.kelo.app.res.mail_preview_title
import chat.kelo.app.res.mail_preview_too_big
import chat.kelo.app.res.close
import chat.kelo.app.res.retry
import chat.kelo.app.ui.components.ConfirmDialog
import chat.kelo.app.ui.components.LinkButton
import chat.kelo.app.ui.components.SkeletonRows
import chat.kelo.app.ui.text.Text
import chat.kelo.app.ui.text.string
import chat.kelo.app.ui.theme.Kelo
import kotlinx.coroutines.launch

/**
 * The platform's renderer of the preview document (Android: a web view with no script and no network; a tapped link comes back through
 * [onLink]). [fallback] draws the blocks instead when the platform cannot draw the document. Null (no host): the blocks are drawn.
 */
interface MailPreviewHost {
    @Composable
    fun Document(html: String, scrollable: Boolean, onLink: (String) -> Unit, modifier: Modifier, fallback: @Composable () -> Unit)
}

val LocalMailPreviewHost = staticCompositionLocalOf<MailPreviewHost?> { null }

private val PREVIEW_HEIGHT = 220.dp
private val BOX_CORNER = RoundedCornerShape(10.dp)
private val INK = Color(0xFF111111)
private val LINK_STYLE = SpanStyle(color = Color(0xFF0B57D0), textDecoration = TextDecoration.Underline)

/** The collapsed preview of a designed mail, in its approval card: the words as they will be sent, in a fixed box. A tap opens the full view. */
@Composable
fun MailPreviewBlock(approvalId: String, design: MailDesign) {
    val store = LocalAppServices.current.mailPreviews
    val scope = rememberCoroutineScope()
    val state by store.state(approvalId).collectAsState()
    var full by remember(approvalId) { mutableStateOf(false) }
    LaunchedEffect(approvalId) { store.load(approvalId, design.previewPath) }
    val c = Kelo.colors
    val openDesc = string(Res.string.mail_preview_desc)
    val host = LocalMailPreviewHost.current
    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(string(Res.string.mail_preview_title), style = MaterialTheme.typography.labelSmall, color = c.muted)
        when (val s = state) {
            is State.Ready -> {
                Box(
                    Modifier.fillMaxWidth().height(PREVIEW_HEIGHT).clip(BOX_CORNER).border(1.dp, c.line, BOX_CORNER)
                        .background(Color.White).clipToBounds(),
                ) {
                    // the mail is designed on white: the box stays white in the dark theme too
                    if (host != null) {
                        host.Document(s.p.html, false, { }, Modifier.fillMaxSize()) { MailBlocksView(s.p.blocks, { }, Modifier.fillMaxWidth().padding(12.dp)) }
                    } else {
                        MailBlocksView(s.p.blocks, { }, Modifier.fillMaxWidth().padding(12.dp))
                    }
                    Box(
                        Modifier.align(Alignment.BottomCenter).fillMaxWidth().height(32.dp)
                            .background(Brush.verticalGradient(listOf(Color.Transparent, Color.White))),
                    )
                    Box(
                        Modifier.fillMaxSize()
                            .clickable(role = Role.Button, onClick = { full = true })
                            .semantics { contentDescription = openDesc },
                    )
                }
                LinkButton(string(Res.string.mail_preview_open), onClick = { full = true })
                if (s.p.pictures > 0) Text(string(Res.string.mail_preview_pictures_note), style = MaterialTheme.typography.labelSmall, color = c.muted)
                if (host == null) Text(string(Res.string.mail_preview_simple_note), style = MaterialTheme.typography.labelSmall, color = c.muted)
                if (s.p.cut) Text("…", style = MaterialTheme.typography.bodySmall, color = c.muted)
            }
            is State.Gone -> Text(string(Res.string.mail_preview_gone), style = MaterialTheme.typography.bodySmall, color = c.muted)
            is State.TooBig -> Text(string(Res.string.mail_preview_too_big), style = MaterialTheme.typography.bodySmall, color = c.muted)
            is State.Failed -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(string(Res.string.mail_preview_failed), style = MaterialTheme.typography.bodySmall, color = c.muted)
                LinkButton(string(Res.string.retry), onClick = { scope.launch { store.load(approvalId, design.previewPath) } })
            }
            is State.Idle, is State.Loading -> Column(
                Modifier.fillMaxWidth().height(PREVIEW_HEIGHT).clip(BOX_CORNER).border(1.dp, c.line, BOX_CORNER).padding(12.dp),
            ) {
                Text(string(Res.string.mail_preview_loading), style = MaterialTheme.typography.bodySmall, color = c.muted)
                SkeletonRows(rows = 4)
            }
        }
    }
    if (full) MailPreviewFull(approvalId) { full = false }
}

/** The full view of a designed mail: the whole document, scrollable; a link asks before it opens outside the app. */
@Composable
fun MailPreviewFull(approvalId: String, onClose: () -> Unit) {
    val store = LocalAppServices.current.mailPreviews
    val state by store.state(approvalId).collectAsState()
    val host = LocalMailPreviewHost.current
    val uri = LocalUriHandler.current
    val c = Kelo.colors
    var link by remember(approvalId) { mutableStateOf<String?>(null) }
    val onLink: (String) -> Unit = { url -> MailPreviewRules.linkToOpen(url)?.let { link = it } }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Column(
                (if (host != null) Modifier.fillMaxSize() else Modifier.widthIn(max = 720.dp).fillMaxHeight(0.9f))
                    .background(c.card).padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(string(Res.string.mail_preview_title), style = MaterialTheme.typography.titleMedium, color = c.ink,
                        maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    LinkButton(string(Res.string.close), onClick = onClose)
                }
                Box(Modifier.fillMaxWidth().weight(1f).clip(BOX_CORNER).border(1.dp, c.line, BOX_CORNER).background(Color.White)) {
                    when (val s = state) {
                        is State.Ready -> if (host != null) {
                            host.Document(s.p.html, true, onLink, Modifier.fillMaxSize()) {
                                MailBlocksView(s.p.blocks, onLink, Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp))
                            }
                        } else {
                            MailBlocksView(s.p.blocks, onLink, Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp))
                        }
                        is State.Failed -> Text(string(Res.string.mail_preview_failed), style = MaterialTheme.typography.bodyMedium,
                            color = c.muted, modifier = Modifier.padding(12.dp))
                        is State.Gone -> Text(string(Res.string.mail_preview_gone), style = MaterialTheme.typography.bodyMedium,
                            color = c.muted, modifier = Modifier.padding(12.dp))
                        is State.TooBig -> Text(string(Res.string.mail_preview_too_big), style = MaterialTheme.typography.bodyMedium,
                            color = c.muted, modifier = Modifier.padding(12.dp))
                        else -> Text(string(Res.string.mail_preview_loading), style = MaterialTheme.typography.bodyMedium,
                            color = c.muted, modifier = Modifier.padding(12.dp))
                    }
                }
                if (host == null) Text(string(Res.string.mail_preview_simple_note), style = MaterialTheme.typography.labelSmall, color = c.muted)
            }
        }
        link?.let { url ->
            ConfirmDialog(
                text = MailPreviewRules.linkLabel(url) + "\n" + url,
                yes = string(Res.string.mail_preview_link_open),
                onYes = { uri.openUri(url) },
                onDismiss = { link = null },
                danger = false,
                title = string(Res.string.mail_preview_link_title),
            )
        }
    }
}

/**
 * The blocks of a preview as text: headings, paragraphs, list items (with their number and depth), quotes, rules and table rows. The
 * desktop app draws the preview this way; a web view draws the document itself. A link goes through [onLink] (the owner confirms it).
 */
@Composable
fun MailBlocksView(blocks: List<MailBlock>, onLink: (String) -> Unit, modifier: Modifier = Modifier) {
    val c = Kelo.colors
    Column(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        blocks.forEach { b ->
            when (b.kind) {
                MailBlock.Kind.RULE -> HorizontalDivider(color = c.line)
                MailBlock.Kind.ROW -> Row(Modifier.fillMaxWidth()) {
                    b.cells.forEach { cell ->
                        Box(Modifier.weight(1f).then(cell.bg?.let { Modifier.background(Color(it)) } ?: Modifier).padding(4.dp)) {
                            Text(annotated(cell.spans, onLink), style = MaterialTheme.typography.bodyMedium, textAlign = cell.align.textAlign())
                        }
                    }
                }
                MailBlock.Kind.ITEM -> Row(Modifier.fillMaxWidth().padding(start = (16 * b.depth).dp)) {
                    Text(if (b.ordinal > 0) "${b.ordinal}." else "•", style = MaterialTheme.typography.bodyMedium, color = INK,
                        modifier = Modifier.padding(end = 8.dp))
                    Text(annotated(b.spans, onLink), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f),
                        textAlign = b.align.textAlign())
                }
                MailBlock.Kind.QUOTE -> Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
                    Box(Modifier.width(3.dp).fillMaxHeight().background(c.line))
                    Spacer(Modifier.width(10.dp))
                    Text(annotated(b.spans, onLink), style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f),
                        textAlign = b.align.textAlign())
                }
                MailBlock.Kind.HEADING -> Text(annotated(b.spans, onLink), style = headingStyle(b.level),
                    modifier = Modifier.fillMaxWidth().then(b.bg?.let { Modifier.background(Color(it)) } ?: Modifier),
                    textAlign = b.align.textAlign())
                MailBlock.Kind.PARAGRAPH -> Text(annotated(b.spans, onLink), style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.fillMaxWidth().then(b.bg?.let { Modifier.background(Color(it)) } ?: Modifier),
                    textAlign = b.align.textAlign())
            }
        }
    }
}

@Composable
private fun headingStyle(level: Int): TextStyle = when (level) {
    1 -> MaterialTheme.typography.titleLarge
    2 -> MaterialTheme.typography.titleMedium
    else -> MaterialTheme.typography.titleSmall
}

private fun MailAlign.textAlign(): TextAlign = when (this) {
    MailAlign.START -> TextAlign.Start
    MailAlign.CENTER -> TextAlign.Center
    MailAlign.END -> TextAlign.End
}

private fun annotated(spans: List<MailSpan>, onLink: (String) -> Unit): AnnotatedString = buildAnnotatedString {
    spans.forEach { s ->
        val style = SpanStyle(
            fontWeight = if (s.bold) FontWeight.Bold else null,
            fontStyle = if (s.italic) FontStyle.Italic else null,
            textDecoration = listOfNotNull(
                if (s.underline) TextDecoration.Underline else null,
                if (s.strike) TextDecoration.LineThrough else null,
            ).let { if (it.isEmpty()) null else TextDecoration.combine(it) },
            color = s.color?.let { Color(it) } ?: INK,
        )
        if (s.href.isNotEmpty()) {
            withLink(LinkAnnotation.Clickable(tag = s.href, styles = TextLinkStyles(LINK_STYLE)) { onLink(s.href) }) { append(s.text) }
        } else {
            withStyle(style) { append(s.text) }
        }
    }
}
